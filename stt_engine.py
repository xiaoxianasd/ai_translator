"""
stt_engine.py — STT 语音识别引擎模块

支持的引擎:
- Deepgram: 通过 REST API 发送 WAV 音频，低延迟高精度
- Mock: 随机返回模拟句子，无需 API Key

接口:
    engine = create_stt_engine("deepgram", api_key="...")
    text = engine.transcribe(audio_chunk)  # audio_chunk: np.ndarray, float32, 16kHz, mono
"""

import io
import wave
import random
import time
import logging
from abc import ABC, abstractmethod

import numpy as np
import httpx

logger = logging.getLogger("stt_engine")

# ======================== Mock 样本 ========================

MOCK_SAMPLES = [
    "Welcome to the annual technology conference.",
    "Today we're going to discuss the future of artificial intelligence.",
    "The results show significant improvement in accuracy.",
    "Let me walk you through the main findings.",
    "I'd like to thank the team for their hard work.",
    "This approach reduces latency by over forty percent.",
    "Please feel free to ask questions at the end.",
    "The next speaker will cover practical applications.",
    "This is a very",
    "Let me just quickly",
    "We need to consider the",
    "The most important thing is",
    "If we look at the",
]

# ======================== 工具函数 ========================

def _numpy_to_wav_bytes(audio: np.ndarray, sample_rate: int = 16000) -> bytes:
    """将 float32 单声道音频数组转换为 WAV 字节流"""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)          # 16-bit PCM
        wf.setframerate(sample_rate)
        # float32 [-1.0, 1.0] → int16
        clamped = np.clip(audio, -1.0, 1.0)
        samples = (clamped * 32767).astype(np.int16)
        wf.writeframes(samples.tobytes())
    return buf.getvalue()


# ======================== 抽象基类 ========================

class BaseSTTEngine(ABC):
    """STT 引擎抽象基类"""

    @abstractmethod
    def transcribe(self, audio_chunk: np.ndarray) -> str:
        """将音频块转写为英文文本"""
        ...


# ======================== Mock 引擎 ========================

class MockSTT(BaseSTTEngine):
    """Mock STT 引擎：随机返回模拟句子，用于开发调试"""

    def transcribe(self, audio_chunk: np.ndarray) -> str:
        time.sleep(random.uniform(0.05, 0.15))
        return random.choice(MOCK_SAMPLES)


# ======================== Deepgram 引擎 ========================

class DeepgramSTT(BaseSTTEngine):
    """Deepgram STT 引擎：通过 REST API 进行语音识别

    参数
    ----
    api_key : str  Deepgram API Key
    language : str  识别语言代码（默认 "en"）
    model : str     识别模型（默认 "nova-2"）
    """

    BASE_URL = "https://api.deepgram.com/v1/listen"

    def __init__(self, api_key: str, language: str = "en", model: str = "nova-2"):
        self._api_key = api_key
        self._language = language
        self._model = model
        self._client = httpx.Client(timeout=httpx.Timeout(10.0))

    def transcribe(self, audio_chunk: np.ndarray) -> str:
        """发送音频到 Deepgram，返回识别文本"""
        wav_bytes = _numpy_to_wav_bytes(audio_chunk)

        params = {
            "model": self._model,
            "language": self._language,
            "smart_format": "true",
            "punctuate": "true",
        }
        headers = {
            "Authorization": f"Token {self._api_key}",
            "Content-Type": "audio/wav",
        }

        try:
            resp = self._client.post(
                self.BASE_URL,
                params=params,
                headers=headers,
                content=wav_bytes,
            )
            resp.raise_for_status()
            data = resp.json()

            # 提取识别文本
            transcript = (
                data.get("results", {})
                .get("channels", [{}])[0]
                .get("alternatives", [{}])[0]
                .get("transcript", "")
                .strip()
            )

            if not transcript:
                logger.debug("Deepgram 返回空文本（可能是静音或无法识别）")
                return ""

            logger.debug("Deepgram 识别: %s", transcript)
            return transcript

        except httpx.HTTPStatusError as e:
            logger.error("Deepgram API 错误 (%d): %s", e.response.status_code, e.response.text[:200])
            return f"[STT错误 HTTP{e.response.status_code}]"
        except httpx.RequestError as e:
            logger.error("Deepgram 网络错误: %s", e)
            return "[STT网络错误]"
        except (KeyError, IndexError, TypeError) as e:
            logger.error("Deepgram 响应解析失败: %s", e)
            return "[STT解析错误]"

    def close(self):
        self._client.close()


# ======================== Faster-Whisper 本地引擎 ========================

class FasterWhisperSTT(BaseSTTEngine):
    """Faster-Whisper 本地 STT 引擎 — 永久免费，无需联网

    参数
    ----
    model_size : str  模型大小 "tiny" / "small" / "medium"（默认 "small"）
    device : str      运行设备 "cpu" / "cuda"（默认 "cpu"）
    language : str    识别语言（默认 "en"）
    """

    def __init__(self, model_size: str = "tiny", device: str = "cpu", language: str = "en"):
        import os as _os, sys as _sys
        if _sys.platform == "win32":
            import torch as _torch
            _torch_lib = _os.path.join(_os.path.dirname(_torch.__file__), "lib")
            _os.add_dll_directory(_torch_lib)
        from faster_whisper import WhisperModel

        self._language = language
        self._memory = ""  # 动态记忆池，随识别累积，用作 initial_prompt 提供上下文
        logger.info("正在加载 Faster-Whisper 模型 (%s)...", model_size)
        self._model = WhisperModel(
            model_size, device=device, compute_type="int8",
            local_files_only=True,
        )
        logger.info("Faster-Whisper 模型就绪 (%s)", model_size)

    def transcribe(self, audio_chunk: np.ndarray) -> str:
        """本地识别音频块，利用历史上下文提高准确率"""
        try:
            segments, _ = self._model.transcribe(
                audio_chunk.astype(np.float32),
                language=self._language,
                beam_size=1,
                initial_prompt=self._memory or None,
                vad_filter=False,
                condition_on_previous_text=False,
            )
            texts = [seg.text.strip() for seg in segments if seg.text.strip()]
            transcript = " ".join(texts)

            # 幻觉黑名单过滤
            hallucinations = ["Thank you.", "Thank you", "Subscribe", "Thanks for watching.", "Let's go.", "bye", "you"]
            if transcript.strip() in hallucinations:
                return ""

            if transcript:
                logger.debug("Whisper 识别: %s", transcript)
                self._memory = (self._memory + " " + transcript)[-200:].strip()
            return transcript
        except Exception as e:
            logger.error("Faster-Whisper 识别失败: %s", e)
            return "[Whisper错误]"


# ======================== 工厂函数 ========================

def create_stt_engine(provider: str, api_key: str = "", **kwargs) -> BaseSTTEngine:
    """根据配置创建 STT 引擎实例

    参数
    ----
    provider : str  "deepgram" | "azure" | "mock"
    api_key : str   对应的 API Key（mock 不需要）
    kwargs : dict   传递给具体引擎的额外参数（如 language, model）

    返回
    ----
    BaseSTTEngine 实例
    """
    provider = provider.lower().strip()

    if provider == "deepgram":
        if not api_key:
            logger.warning("未配置 Deepgram API Key，回退到 Mock STT")
            return MockSTT()
        logger.info("STT 引擎: Deepgram (model=%s)", kwargs.get("model", "nova-2"))
        return DeepgramSTT(api_key, **kwargs)

    elif provider == "whisper":
        logger.info("STT 引擎: Faster-Whisper (本地免费)")
        return FasterWhisperSTT(**kwargs)

    elif provider == "azure":
        logger.warning("Azure STT 暂未实现，回退到 Mock STT")
        return MockSTT()

    else:
        logger.info("STT 引擎: Mock (随机模拟)")
        return MockSTT()


# ======================== 测试入口 ========================

if __name__ == "__main__":
    import sys
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s", datefmt="%H:%M:%S")

    print("=" * 60)
    print("  STT 引擎测试")
    print("=" * 60)

    # --- Mock 测试 ---
    print("\n  [Mock 引擎测试]")
    mock = MockSTT()
    fake_audio = np.random.randn(16000).astype(np.float32) * 0.05
    for i in range(3):
        text = mock.transcribe(fake_audio)
        print(f"    [{i+1}] {text}")

    # --- Deepgram 测试（需要 Key） ---
    import os
    dg_key = os.environ.get("DEEPGRAM_API_KEY", "")
    if dg_key:
        print("\n  [Deepgram 引擎测试]")
        dg = DeepgramSTT(dg_key)
        text = dg.transcribe(fake_audio)
        print(f"    识别结果: {text}")
        dg.close()
    else:
        print("\n  [Deepgram] 跳过（未设置 DEEPGRAM_API_KEY）")
        print("    设置方式: set DEEPGRAM_API_KEY=你的Key")
