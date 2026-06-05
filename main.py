"""
main.py — AI 同声传译助手一键启动

全链路：系统音频内录 → VAD 切片 → Faster-Whisper STT → Gemini 翻译 → 悬浮字幕窗
"""

import sys
import queue
import threading
import logging

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

from PyQt5.QtWidgets import QApplication

from audio_capture import start_audio_capture
from ai_processor import ai_worker_thread
from config_manager import get_api_key, get_stt_config
from stt_engine import create_stt_engine
from ui_main import SubtitleOverlay

logger = logging.getLogger("main")


def main():
    print("=" * 60)
    print("  AI 同声传译助手 v0.5")
    print("  100% 本地运行 · 永久免费")
    print("=" * 60)
    print()

    api_key = get_api_key()
    stt_cfg = get_stt_config()

    print(f"  STT:  {stt_cfg['provider']} (本地)")
    print(f"  翻译: {'Qwen + Gemini 混合' if api_key else 'Qwen 本地'}")
    print()

    # ==== 预加载模型（监听前完成，避免下载拖慢） ====
    print("  正在加载模型...")
    stt_engine = create_stt_engine(stt_cfg["provider"], stt_cfg["api_key"])
    print(f"  STT 就绪")

    from ai_processor import LocalTranslator
    translator = LocalTranslator()
    print(f"  翻译就绪 (Qwen{' + Gemini' if api_key else ''})")
    print()

    # ==== 启动 ====
    print("  1. 打开英文视频/音频播放")
    print("  2. 字幕将出现在屏幕底部")
    print()

    app = QApplication(sys.argv)

    audio_queue: queue.Queue = queue.Queue(maxsize=50)
    result_queue: queue.Queue = queue.Queue()

    worker = threading.Thread(
        target=ai_worker_thread,
        args=(audio_queue, result_queue),
        kwargs={"api_key": api_key, "stt_engine": stt_engine, "translator": translator},
        daemon=True, name="AI-Worker",
    )
    worker.start()

    capture = start_audio_capture(audio_queue)
    print("  VAD 校准中，请保持安静...")
    capture.calibrated.wait(timeout=5)
    print("  >>> 可以播放音频了\n")

    overlay = SubtitleOverlay(result_queue)
    overlay.show()

    try:
        exit_code = app.exec_()
    finally:
        capture.stop()
        audio_queue.put(None)
        worker.join(timeout=3)
        print("\n  已退出。")
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
