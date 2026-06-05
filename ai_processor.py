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

import torch
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


# ======================== Qwen 本地翻译引擎 ========================

class LocalTranslator:
    """Qwen2.5-0.5B 本地翻译引擎 — 永久免费，质量接近云端"""

    def __init__(self):
        from transformers import AutoModelForCausalLM, AutoTokenizer

        model_name = "Qwen/Qwen2.5-0.5B-Instruct"
        logger.info("正在加载 Qwen2.5-0.5B 翻译模型 (~1GB)...")

        # 先查本地缓存，避免每次启动都联网（网络不稳时会卡死）
        cache_root = os.path.expanduser("~/.cache/modelscope/hub/models")
        owner, repo = model_name.split("/")
        safe_repo = repo.replace(".", "___")
        local_path = os.path.join(cache_root, owner, safe_repo)

        if os.path.isdir(local_path) and os.path.exists(os.path.join(local_path, "model.safetensors")):
            logger.info("使用本地缓存: %s", local_path)
        else:
            try:
                from modelscope import snapshot_download
                local_path = snapshot_download(model_name)
            except Exception as e:
                logger.warning("ModelScope 下载失败 (%s)，尝试已有缓存...", e)
                # 回退：直接找缓存目录中匹配的文件夹
                owner_dir = os.path.join(cache_root, owner)
                if os.path.isdir(owner_dir):
                    for d in os.listdir(owner_dir):
                        if d.startswith(safe_repo[:6]):
                            local_path = os.path.join(owner_dir, d)
                            if os.path.isdir(local_path):
                                logger.info("回退到缓存: %s", local_path)
                                break
        logger.info("模型路径: %s", local_path)

        torch.set_num_threads(max(1, os.cpu_count() - 2))
        self._tokenizer = AutoTokenizer.from_pretrained(
            local_path, trust_remote_code=True, local_files_only=True,
        )
        self._model = AutoModelForCausalLM.from_pretrained(
            local_path, trust_remote_code=True, local_files_only=True,
            dtype=torch.float32, device_map="cpu",
        )
        self._model.eval()
        logger.info("Qwen 翻译模型就绪 (CPU 线程数: %d)", torch.get_num_threads())

    def translate(self, text: str) -> str:
        if not text.strip():
            return ""
        try:
            # few-shot 示例：教小模型理解"翻译 = 输出中文"的格式
            messages = [
                {"role": "system", "content": "你是英中翻译器。唯一任务：把用户输入的英文翻译成中文。禁止输出英文单词。只输出中文译文。不要解释。"},
                {"role": "user", "content": "翻译: Hello, how are you today?"},
                {"role": "assistant", "content": "你今天好吗？"},
                {"role": "user", "content": "翻译: " + text},
            ]
            prompt = self._tokenizer.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True
            )
            inputs = self._tokenizer(prompt, return_tensors="pt")
            with torch.inference_mode():
                outputs = self._model.generate(
                    **inputs, max_new_tokens=64, do_sample=False, num_beams=1,
                    pad_token_id=self._tokenizer.eos_token_id,
                )
            result = self._tokenizer.decode(
                outputs[0][len(inputs.input_ids[0]):], skip_special_tokens=True
            ).strip()

            # 兜底检测：如果输出大部分是英文单词，说明模型没听话，标记为翻译失败
            if result:
                words = result.split()
                if len(words) >= 3:
                    en_count = sum(1 for w in words if w[:1].isascii() and w[:1].isalpha())
                    if en_count / len(words) > 0.5:
                        logger.warning("Qwen 输出含大量英文，疑似翻译失败: %s", result[:80])
                        return "[翻译失败]"

            return result
        except Exception as e:
            logger.error("Qwen 翻译失败: %s", e)
            return "[翻译错误]"


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
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
        for tag in ("```json", "```"):
            if tag in raw:
                try:
                    s = raw.index(tag) + len(tag)
                    e = raw.index("```", s)
                    return json.loads(raw[s:e].strip())
                except (json.JSONDecodeError, ValueError):
                    pass
        try:
            a, b = raw.index("{"), raw.rindex("}") + 1
            return json.loads(raw[a:b])
        except (json.JSONDecodeError, ValueError):
            pass
        logger.warning("JSON 解析失败: %s", raw[:200])
        return {"final_translation": "", "draft_translation": raw.strip()[:300]}


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
        tr_name = "Qwen 秒出 + Gemini 精修"
    elif engine:
        tr_name = "Gemini"
    elif translator:
        tr_name = "Qwen 本地"
    else:
        tr_name = "未启用"
    logger.info("AI 工作线程已启动 | STT=%s | 翻译=%s", stt_name, tr_name)

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
                # 混合模式：先本地秒出草稿 → 再 Gemini 精修
                draft_zh = translator.translate(english_text)
                entry = {
                    "source_text": english_text,
                    "final_translation": "",
                    "draft_translation": draft_zh,
                    "timestamp": time.time(),
                }
                result_queue.put(entry)
                logger.info("[草稿] %s → %s", english_text, draft_zh[:60])

                # 异步精修（同一线程内，Gemini 覆盖草稿）
                try:
                    gemini_result = engine.translate(english_text)
                except Exception:
                    gemini_result = None

                final_zh = (gemini_result or {}).get("final_translation", "")
                if final_zh:
                    entry["final_translation"] = final_zh
                    entry["timestamp"] = time.time()
                    entry["_refined"] = True
                    result_queue.put(entry)
                    logger.info("[精修] %s → %s", english_text, final_zh[:60])
                else:
                    # Gemini 失败，草稿直接升格为最终
                    entry["final_translation"] = draft_zh
                    result_queue.put(entry)

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
                zh = translator.translate(english_text)
                entry = {
                    "source_text": english_text,
                    "final_translation": zh,
                    "draft_translation": "",
                    "timestamp": time.time(),
                }
                logger.info("[翻译] %s → %s", english_text, zh[:60])
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
