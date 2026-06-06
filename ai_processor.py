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
import concurrent.futures

from google import genai

from config_manager import get_api_key, get_stt_config
from stt_engine import create_stt_engine, BaseSTTEngine

logger = logging.getLogger("ai_processor")

GEMINI_MODEL = "gemini-2.5-flash"
CONTEXT_WINDOW_SIZE = 3

# ======================== Prompt ========================

TRANSLATION_PROMPT = """你是一位专业的同声传译员。请将最新的英文语音片段翻译成流畅的中文。

## 历史上下文
{context}

## 当前输入片段
"{current}"

请注意：当前片段可能是一个残句。请结合历史上下文，给出最符合当前语境的中文翻译。
必须严格输出以下 JSON 格式：
{{
  "final_translation": "此处填写翻译结果"
}}"""


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


def _english_ratio(text: str) -> float:
    """返回文本中英文单词字符的占比，用于检测模型是否输出了英文原文"""
    if not text:
        return 0.0
    english_chars = sum(1 for c in text if c.isascii() and c.isalpha())
    total_chars = sum(1 for c in text if c.isalpha())
    if total_chars == 0:
        return 0.0
    return english_chars / total_chars


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
            user_prompt = "请将下面这段英文翻译成流畅的中文。绝对不允许输出任何英文原文，不要有任何解释。\n\n"
            if context_str:
                user_prompt += f"【{context_str}】\n\n"
            user_prompt += f"需要翻译的英文：\"{current_text}\"\n中文翻译结果："

            response = self._llm.create_chat_completion(
                messages=[
                    {"role": "system", "content": "你是专业的英中同声传译员。你的唯一任务是输出纯中文译文。"},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=64,
                temperature=0.1,
            )
            raw = response["choices"][0]["message"]["content"].strip()

            if not raw:
                return {"final_translation": "", "draft_translation": ""}

            if raw.startswith("{") and raw.endswith("}"):
                return parse_translation_json(raw, current_text)

            if _english_ratio(raw) > 0.4:
                logger.warning("Qwen 输出英文占比过高 (%.0f%%)，丢弃: %s", _english_ratio(raw) * 100, raw[:80])
                return {"final_translation": "", "draft_translation": ""}

            return {"final_translation": raw, "draft_translation": ""}
        except Exception as e:
            logger.error("Qwen GGUF 翻译失败: %s", e)
            return {"final_translation": "", "draft_translation": f"[翻译错误] {e}"}


# ======================== Gemini 翻译引擎 ========================

class TranslationEngine:
    """Gemini 文本翻译引擎（无状态设计，外部传入历史记录）"""

    def __init__(self, api_key: str):
        self._client = genai.Client(api_key=api_key)

    def translate(self, current_text: str, en_hist: list, zh_hist: list) -> dict:
        if en_hist:
            lines = []
            for i, (en, zh) in enumerate(zip(en_hist, zh_hist), 1):
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
            return {"final_translation": "", "draft_translation": f"[API异常] {e}"}

        return self._parse_json(raw_text)

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

    if translator is None:
        try:
            translator = LocalTranslator()
        except Exception as e:
            logger.error("本地翻译加载失败: %s", e)

    hybrid = engine is not None and translator is not None
    stt_name = type(stt_engine).__name__
    tr_name = "混合修正模式" if hybrid else ("纯云端" if engine else "纯本地")
    logger.info("AI 工作线程已启动 | STT=%s | 翻译=%s", stt_name, tr_name)

    # 全局唯一真理状态
    en_history: list[str] = []
    zh_history: list[str] = []

    api_executor = concurrent.futures.ThreadPoolExecutor(max_workers=3)
    history_lock = threading.Lock()

    while True:
        try:
            audio_chunk = audio_queue.get(timeout=0.5)
        except queue.Empty:
            continue

        if audio_chunk is None:
            break

        try:
            english_text = stt_engine.transcribe(audio_chunk)
        except Exception as e:
            result_queue.put({"history": zh_history.copy(), "draft": f"[STT异常: {e}]"})
            continue

        if not english_text or english_text.startswith("["):
            continue

        logger.debug("STT: %s", english_text)

        try:
            if hybrid:
                # 1. 获取当前状态并生成草稿
                with history_lock:
                    context_str = "上文：" + "；".join(zh_history) if zh_history else "暂无上文"
                    current_history = list(zh_history)

                draft_result = translator.translate(english_text, context_str)
                draft_zh = draft_result.get("draft_translation", "") or draft_result.get("final_translation", "")

                # 推送当前已定型的历史 + 本次残句草稿
                result_queue.put({"history": current_history, "draft": draft_zh})

                # 2. 异步智能修正
                def gemini_refine_task(en_text):
                    with history_lock:
                        en_hist_copy = list(en_history)
                        zh_hist_copy = list(zh_history)

                    result = engine.translate(en_text, en_hist_copy, zh_hist_copy)
                    final = result.get("final_translation", "")

                    if final and not final.startswith("["):
                        with history_lock:
                            if len(zh_history) > 0:
                                zh_history[-1] = final
                                en_history[-1] = en_history[-1] + " " + en_text
                            else:
                                zh_history.append(final)
                                en_history.append(en_text)
                            if len(zh_history) > CONTEXT_WINDOW_SIZE:
                                zh_history.pop(0)
                                en_history.pop(0)
                            result_queue.put({"history": list(zh_history), "draft": ""})

                api_executor.submit(gemini_refine_task, english_text)

            elif engine:
                with history_lock:
                    en_h, zh_h = list(en_history), list(zh_history)
                res = engine.translate(english_text, en_h, zh_h)
                final = res.get("final_translation", "")
                if final:
                    with history_lock:
                        if len(zh_history) > 0:
                            zh_history[-1] = final
                            en_history[-1] += " " + english_text
                        else:
                            zh_history.append(final)
                            en_history.append(english_text)
                        if len(zh_history) > CONTEXT_WINDOW_SIZE:
                            zh_history.pop(0); en_history.pop(0)
                        result_queue.put({"history": list(zh_history), "draft": ""})

            elif translator:
                with history_lock:
                    context_str = "上文：" + "；".join(zh_history) if zh_history else "暂无上文"
                result = translator.translate(english_text, context_str)
                final_zh = result.get("final_translation", "")
                draft_zh = result.get("draft_translation", "")
                if final_zh and not final_zh.startswith("[") and "[翻译" not in final_zh:
                    with history_lock:
                        zh_history.append(final_zh)
                        en_history.append(english_text)
                        if len(zh_history) > CONTEXT_WINDOW_SIZE:
                            zh_history.pop(0); en_history.pop(0)
                result_queue.put({"history": list(zh_history), "draft": draft_zh if not final_zh else ""})

        except Exception as e:
            logger.exception("翻译异常")

    api_executor.shutdown(wait=False)
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
