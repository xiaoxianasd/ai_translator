"""
ai_processor.py — AI 翻译核心处理器

流水线：音频块 → STT 识别 → Gemini 文本翻译（含上下文纠错）→ 结构化 JSON

功能：
- 从 audio_queue 消费音频 Chunk
- 调用 STT 引擎转为英文文本
- 维护最近 3 句话的滑动窗口历史，发送给 Gemini 做文本翻译
- 通过 prompt 强制 LLM 返回 final_translation + draft_translation 双轨输出
- 异常保护：网络断开、API 报错不导致线程崩溃

依赖：google-genai, queue, threading
"""

import queue
import json
import time
import logging
import threading
import os
from collections import deque

from google import genai

from config_manager import get_api_key, get_stt_config
from stt_engine import create_stt_engine, BaseSTTEngine

logger = logging.getLogger("ai_processor")

GEMINI_MODEL = "gemini-2.5-flash"
CONTEXT_WINDOW_SIZE = 3

# ======================== Prompt ========================

TRANSLATION_PROMPT = """你是一位极其专业的通用同声传译员。你的任务是将用户输入的流式英文语音识别结果翻译成中文。

【核心挑战】：输入通常是不完整的片段（残句），并且可能包含由于机器听音导致的同音词错误。

【你的职责】：
1. 动态推理：请根据历史前文的内容，迅速判断当前所处的领域（如科技、娱乐、医学、日常闲聊等）。
2. 智能纠错：利用你推测出的领域背景，自动修正当前句子中明显不合理的同音词识别错误。
3. 意译输出：输出符合中文表达习惯的流畅翻译，不要逐字死翻。

## 历史上下文
{context}

## 当前输入
"{current}"

## 翻译规则
1. 当前输入是**完整句子** → final_translation 填最终翻译, draft_translation 填 ""
2. 当前输入是**残句/片段** → final_translation 填 "", draft_translation 填暂定翻译
3. 注意语义连贯性，结合上文判断当前片段是否构成了完整语义

## 严格 JSON（必须遵守）
```json
{{
  "final_translation": "...",
  "draft_translation": "..."
}}
```"""


# ======================== 通用 JSON 解析器 ========================

def parse_translation_json(raw: str, fallback_text: str) -> dict:
    """鲁棒解析翻译 JSON，支持 Markdown 代码块、暴力提取 {} 内容"""
    if not raw or not raw.strip():
        return {"final_translation": "", "draft_translation": fallback_text}

    # 1) 直接解析
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # 2) 提取 ```json / ``` 代码块
    for tag in ("```json", "```"):
        if tag in raw:
            try:
                s = raw.index(tag) + len(tag)
                e = raw.index("```", s)
                return json.loads(raw[s:e].strip())
            except (json.JSONDecodeError, ValueError):
                pass

    # 3) 暴力提取第一个 { 到最后一个 }
    try:
        a, b = raw.index("{"), raw.rindex("}") + 1
        return json.loads(raw[a:b])
    except (json.JSONDecodeError, ValueError):
        pass

    # 4) 彻底失败 → 兜底
    logger.warning("JSON 解析失败: %s", raw[:200])
    return {"final_translation": "", "draft_translation": fallback_text}


# ======================== Qwen 本地翻译引擎 ========================

class LocalTranslator:
    """Qwen2.5-1.5B GGUF Q4_K_M 本地翻译引擎 — llama-cpp-python 推理"""

    MODEL_REPO = "bartowski/Qwen2.5-1.5B-Instruct-GGUF"
    MODEL_FILE = "Qwen2.5-1.5B-Instruct-Q4_K_M.gguf"

    def __init__(self, model_path: str | None = None):
        from llama_cpp import Llama

        if model_path is None:
            model_path = self._resolve_model_path()

        n_threads = max(1, os.cpu_count() - 2) if os.cpu_count() else 4
        logger.info("正在加载 Qwen2.5-1.5B GGUF 翻译模型 (~1.2GB, %d 线程)...", n_threads)

        self._llm = Llama(
            model_path=model_path,
            n_ctx=1024,
            n_threads=n_threads,
            verbose=False,
        )

        # 预热：消化首次推理开销
        try:
            self._llm.create_chat_completion(
                messages=[{"role": "user", "content": "Hello"}],
                max_tokens=1,
            )
            logger.info("Qwen GGUF 预热完成")
        except Exception as e:
            logger.warning("Qwen GGUF 预热失败（不影响使用）: %s", e)

        logger.info("Qwen GGUF 翻译模型就绪 (CPU 线程数: %d)", n_threads)

    def _resolve_model_path(self) -> str:
        from config_manager import get_config
        cfg = get_config()
        user_path = cfg.get("local_model_path", "")
        if user_path and os.path.exists(user_path):
            logger.info("使用用户指定模型: %s", user_path)
            return user_path

        cache_dir = os.path.join(os.path.expanduser("~"), ".cache", "ai_translator", "models")
        local_path = os.path.join(cache_dir, self.MODEL_FILE)

        if os.path.exists(local_path):
            logger.info("使用本地缓存: %s", local_path)
            return local_path

        logger.info("模型未缓存，正在下载 (~1.2GB)...")
        os.makedirs(cache_dir, exist_ok=True)

        # 多源下载
        try:
            from huggingface_hub import hf_hub_download
            downloaded = hf_hub_download(
                repo_id=self.MODEL_REPO,
                filename=self.MODEL_FILE,
                local_dir=cache_dir,
            )
            logger.info("模型下载完成: %s", downloaded)
            return downloaded
        except Exception:
            logger.info("huggingface_hub 下载失败，尝试直连...")

        import httpx
        urls = [
            f"https://hf-mirror.com/{self.MODEL_REPO}/resolve/main/{self.MODEL_FILE}",
            f"https://huggingface.co/{self.MODEL_REPO}/resolve/main/{self.MODEL_FILE}",
        ]
        last_error = None
        for url in urls:
            try:
                logger.info("尝试: %s", url)
                with httpx.Client(timeout=httpx.Timeout(600), follow_redirects=True) as client:
                    with client.stream("GET", url) as resp:
                        resp.raise_for_status()
                        with open(local_path, "wb") as f:
                            for chunk in resp.iter_bytes(chunk_size=8192):
                                f.write(chunk)
                logger.info("模型下载完成: %s", local_path)
                return local_path
            except Exception as e:
                last_error = e
                logger.warning("下载失败: %s", e)

        raise RuntimeError(
            f"模型自动下载失败。请手动下载:\n"
            f"  {self.MODEL_FILE} (~1.2GB)\n"
            f"  从: https://hf-mirror.com/{self.MODEL_REPO}/resolve/main/{self.MODEL_FILE}\n"
            f"  放到: {cache_dir}\n"
            f"  原始错误: {last_error}"
        )

    def translate(self, current_text: str, context_str: str = "") -> dict:
        if not current_text.strip():
            return {"final_translation": "", "draft_translation": ""}
        try:
            response = self._llm.create_chat_completion(
                messages=[
                    {"role": "system", "content": "你是英中翻译器。只输出中文译文。不要解释。"},
                    {"role": "user", "content": f"翻译: {current_text}"},
                ],
                max_tokens=48,
                temperature=0,
            )
            raw = response["choices"][0]["message"]["content"].strip()

            if not raw:
                return {"final_translation": "", "draft_translation": current_text}

            if raw.startswith("{") and raw.endswith("}"):
                return parse_translation_json(raw, current_text)

            return {"final_translation": raw, "draft_translation": ""}
        except Exception as e:
            logger.error("Qwen GGUF 翻译失败: %s", e)
            return {"final_translation": "", "draft_translation": f"[翻译错误] {e}"}


# ======================== Gemini 翻译引擎 ========================

class TranslationEngine:
    """Gemini 文本翻译引擎，维护滑动窗口上下文"""

    def __init__(self, api_key: str):
        self._client = genai.Client(api_key=api_key)
        self._en_history: deque[str] = deque(maxlen=CONTEXT_WINDOW_SIZE)
        self._zh_history: deque[str] = deque(maxlen=CONTEXT_WINDOW_SIZE)

    def translate(self, current_text: str) -> dict:
        if self._en_history:
            lines = []
            for i, (en, zh) in enumerate(zip(self._en_history, self._zh_history), 1):
                lines.append(f"{i}. EN: \"{en}\"\n   ZH: \"{zh}\"")
            context_str = "\n".join(lines)
        else:
            context_str = "（第一句话，暂无上下文）"

        prompt = TRANSLATION_PROMPT.format(context=context_str, current=current_text)

        try:
            response = self._client.models.generate_content(
                model=GEMINI_MODEL, contents=prompt,
            )
            raw_text = response.text or ""
        except Exception as e:
            logger.error("Gemini API 失败: %s", e)
            return {"final_translation": "", "draft_translation": f"[API异常] {e}",
                    "_error": str(e)[:100]}

        result = self._parse_json(raw_text)

        final = result.get("final_translation", "")
        if final and current_text and not final.startswith("[纠正]"):
            self._en_history.append(current_text)
            self._zh_history.append(final)

        return result

    def _parse_json(self, raw: str) -> dict:
        return parse_translation_json(raw, raw.strip()[:300])


# ======================== 消费者工作线程 ========================

def ai_worker_thread(
    audio_queue: queue.Queue,
    result_queue: queue.Queue,
    api_key: str | None = None,
    stt_engine: BaseSTTEngine | None = None,
    translator: LocalTranslator | None = None,
):
    """AI 翻译消费者线程"""
    if api_key is None:
        api_key = get_api_key()

    if stt_engine is None:
        stt_cfg = get_stt_config()
        stt_engine = create_stt_engine(stt_cfg["provider"], stt_cfg["api_key"])

    engine = None

    if api_key:
        try:
            engine = TranslationEngine(api_key)
            logger.info("Gemini 翻译引擎就绪 (model=%s)", GEMINI_MODEL)
        except Exception as e:
            logger.error("Gemini 初始化失败: %s", e)

    # 始终确保有本地翻译兜底（Gemini 失败时自动降级）
    if translator is None:
        try:
            translator = LocalTranslator()
        except Exception as e:
            logger.error("本地翻译加载失败: %s", e)

    # 混合模式：本地+云端同时可用时，本地秒出草稿，云端精修
    hybrid = engine is not None and translator is not None

    stt_name = type(stt_engine).__name__
    if hybrid:
        tr_name = "Qwen 1.5B 秒出 + Gemini 精修"
    elif engine:
        tr_name = "Gemini"
    elif translator:
        tr_name = "Qwen 1.5B GGUF"
    else:
        tr_name = "未启用"
    logger.info("AI 工作线程已启动 | STT=%s | 翻译=%s", stt_name, tr_name)

    # 滑动窗口记忆池（供纯本地翻译及混合模式草稿使用）
    en_history: deque[str] = deque(maxlen=CONTEXT_WINDOW_SIZE)
    zh_history: deque[str] = deque(maxlen=CONTEXT_WINDOW_SIZE)

    while True:
        try:
            audio_chunk = audio_queue.get(timeout=0.5)
        except queue.Empty:
            continue

        if audio_chunk is None:
            logger.info("收到停止信号，退出")
            break

        # 跳过积压的旧音频块，只保留最新
        dropped = 0
        while True:
            try:
                audio_chunk = audio_queue.get_nowait()
                if audio_chunk is None:
                    logger.info("收到停止信号（跳过%d个积压块），退出", dropped)
                    break
                dropped += 1
            except queue.Empty:
                break
        if dropped:
            logger.info("跳过 %d 个积压音频块，只处理最新", dropped)

        if audio_chunk is None:
            break

        # STT 识别
        try:
            english_text = stt_engine.transcribe(audio_chunk)
        except Exception as e:
            logger.exception("STT 异常")
            result_queue.put({"source_text": "", "final_translation": "",
                              "draft_translation": f"[STT异常: {e}]",
                              "timestamp": time.time(), "_error": str(e)})
            continue

        if not english_text or english_text.startswith("["):
            logger.debug("STT 无有效文本: %s", english_text)
            # 空文本/错误也推送到队列，让 UI 知道状态
            if english_text and english_text.startswith("["):
                result_queue.put({"source_text": "", "final_translation": "",
                                  "draft_translation": english_text,
                                  "timestamp": time.time()})
            continue

        logger.debug("STT: %s", english_text)

        # 翻译
        try:
            if hybrid:
                # 组装中文历史供本地翻译纠错（不上英文，避免小模型续写）
                if zh_history:
                    context_str = "上文：" + "；".join(list(zh_history))
                else:
                    context_str = "暂无上文"

                # 本地秒出草稿
                draft_result = translator.translate(english_text, context_str)
                draft_zh = draft_result.get("draft_translation", "") or draft_result.get("final_translation", "")
                entry = {
                    "source_text": english_text,
                    "final_translation": "",
                    "draft_translation": draft_zh,
                    "timestamp": time.time(),
                }
                result_queue.put(entry)
                logger.info("[草稿] %s → %s", english_text, draft_zh[:60])

                # Gemini 精修
                try:
                    gemini_result = engine.translate(english_text)
                except Exception:
                    gemini_result = None

                final_zh = (gemini_result or {}).get("final_translation", "")
                if final_zh and not final_zh.startswith("["):
                    entry["final_translation"] = final_zh
                    entry["timestamp"] = time.time()
                    entry["_refined"] = True
                    result_queue.put(entry)
                    en_history.append(english_text)
                    zh_history.append(final_zh)
                    logger.info("[精修] %s → %s", english_text, final_zh[:60])
                elif draft_zh and not draft_zh.startswith("["):
                    # Gemini 失败，草稿直接升格为最终
                    entry["final_translation"] = draft_zh
                    result_queue.put(entry)
                    en_history.append(english_text)
                    zh_history.append(draft_zh)

            elif engine:
                result = engine.translate(english_text)
                entry = {
                    "source_text": english_text,
                    "final_translation": result.get("final_translation", ""),
                    "draft_translation": result.get("draft_translation", ""),
                    "timestamp": time.time(),
                    "_error": result.get("_error"),
                }
                logger.info("[翻译] %s", json.dumps(entry, ensure_ascii=False, default=str))
                result_queue.put(entry)

            elif translator:
                # 组装中文历史（不上英文，避免小模型续写 EN/ZH 模式）
                if zh_history:
                    context_str = "上文：" + "；".join(list(zh_history))
                else:
                    context_str = "暂无上文"

                result = translator.translate(english_text, context_str)
                final_zh = result.get("final_translation", "")
                draft_zh = result.get("draft_translation", "")

                # 仅当 final 有效时才记入历史
                if final_zh and not final_zh.startswith("[") and "[翻译" not in final_zh:
                    en_history.append(english_text)
                    zh_history.append(final_zh)

                entry = {
                    "source_text": english_text,
                    "final_translation": final_zh,
                    "draft_translation": draft_zh,
                    "timestamp": time.time(),
                }
                logger.info("[翻译] %s → %s", english_text, (final_zh or draft_zh)[:60])
                result_queue.put(entry)

            else:
                result_queue.put({
                    "source_text": english_text,
                    "final_translation": "",
                    "draft_translation": f"[无翻译] {english_text}",
                    "timestamp": time.time(),
                })

        except Exception as e:
            logger.exception("翻译异常")
            result_queue.put({
                "source_text": english_text,
                "final_translation": "",
                "draft_translation": f"[异常: {e}]",
                "timestamp": time.time(), "_error": str(e),
            })

    logger.info("AI 工作线程已退出")


# ======================== 测试入口 ========================

if __name__ == "__main__":
    import sys, numpy as np
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    print("=" * 60)
    print("  AI 同声传译助手 — 翻译处理器测试")
    print("=" * 60)

    api_key = get_api_key()
    stt_cfg = get_stt_config()
    stt_engine = create_stt_engine(stt_cfg["provider"], stt_cfg["api_key"])

    if api_key:
        print(f"  [OK] Gemini: {api_key[:8]}...{api_key[-4:]}")
    else:
        print("  [WARN] Gemini 未配置")
    print(f"  [OK] STT: {stt_cfg['provider']}\n")

    audio_q: queue.Queue = queue.Queue()
    result_q: queue.Queue = queue.Queue()

    worker = threading.Thread(
        target=ai_worker_thread,
        args=(audio_q, result_q),
        kwargs={"api_key": api_key, "stt_engine": stt_engine},
        daemon=True, name="AI-Worker",
    )
    worker.start()
    print("  工作线程已启动\n")

    # 测试：直接给一段文字（跳过音频 STT）
    for text in ["Hello everyone", "Today we are going to discuss"]:
        audio_q.put(np.zeros(8000, dtype=np.float32))
        time.sleep(0.5)

    print("  --- 结果 ---\n")
    for _ in range(2):
        try:
            r = result_q.get(timeout=30)
            print(f"  原文: {r['source_text']}")
            print(f"  翻译: {r['final_translation'] or r['draft_translation']}\n")
        except queue.Empty:
            print("  超时\n")
            break

    audio_q.put(None)
    worker.join(timeout=3)
    print("  完成。")
