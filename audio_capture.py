"""
audio_capture.py — Windows 系统音频流捕获与 Silero VAD 静音切片

功能：
- 通过 pyaudiowpatch 抓取 WASAPI Loopback 虚拟通道，实现对系统播放声音的内录
- 基于 Silero VAD 神经网络模型实时判别人声/静音
- VADIterator 自动检测 speech start/end 边界，buffer 间语音帧后整体切片入队
- 通过 queue.Queue 异步将切片发送给下游消费者

依赖：pyaudiowpatch, numpy, torch
"""

import queue
import threading
import time
import logging
import numpy as np
import pyaudiowpatch as pyaudio
import torch

logger = logging.getLogger("audio_capture")

# ======================== 配置常量 ========================
TARGET_SAMPLE_RATE = 16000         # 输出音频采样率（Hz）
VAD_FRAME_SIZE = 512               # 每帧采样点数（Silero 要求严格的 512 或 256）
MIN_CHUNK_DURATION_SEC = 0.5       # 最短切片（秒）
MAX_CHUNK_DURATION_SEC = 4         # 最长强制截断时间（秒）
READ_CHUNK_DURATION_SEC = 0.1      # 每次从声卡读取的源音频时长（秒）


class AudioCapture:
    """Windows 系统音频内录 + Silero VAD 切片器

    对每个从 WASAPI 抓取的音频帧：
    1. 转换字节流 → float32 多声道数组
    2. 多声道 → 单声道（取均值）
    3. 重采样到 16kHz
    4. 拆分为 512 采样点微帧，逐帧送入 Silero VADIterator
    5. VADIterator 自动检测 speech start/end → 切片入队
    """

    def __init__(self, audio_queue: queue.Queue):
        self._queue = audio_queue
        self._stop_event = threading.Event()
        self.calibrated = threading.Event()
        self._pa: pyaudio.PyAudio | None = None
        self._stream = None
        self._thread: threading.Thread | None = None

        # --- 静态加载 Silero VAD ---
        logger.info("正在加载 Silero VAD 模型 (~2MB)...")
        try:
            self.vad_model, utils = torch.hub.load(
                repo_or_dir='snakers4/silero-vad',
                model='silero_vad',
                force_reload=False
            )
            self.VADIterator = utils[3]
            logger.info("Silero VAD 模型就绪")
            self.calibrated.set()
        except Exception as e:
            logger.error("Silero VAD 加载失败，请检查网络连接: %s", e)
            raise

    # ==================== 设备查找 ====================

    def _find_loopback(self, pa: pyaudio.PyAudio) -> dict:
        """获取默认输出设备对应的 WASAPI Loopback 虚拟通道设备信息"""
        try:
            info = pa.get_default_wasapi_loopback()
            logger.info("Loopback 设备: %s (index=%d)", info.get("name"), info.get("index"))
            return info
        except Exception:
            logger.warning("get_default_wasapi_loopback() 调用失败，回退到遍历设备列表")
            for i in range(pa.get_device_count()):
                dev = pa.get_device_info_by_index(i)
                if dev.get("isLoopbackDevice", False):
                    logger.info("遍历找到 Loopback: %s (index=%d)", dev.get("name"), i)
                    return dev
            raise RuntimeError(
                "未找到 WASAPI Loopback 设备。\n"
                "请确认：1) 音频驱动支持 WASAPI  2) 系统音频服务正在运行"
            )

    # ==================== 音频格式转换 ====================

    @staticmethod
    def _bytes_to_float32(raw: bytes, fmt: int, channels: int) -> np.ndarray:
        """将原始 PCM 字节流转为 float32 归一化数组，shape=(n_frames, channels)，值域 [-1.0, 1.0]"""
        if fmt == pyaudio.paFloat32:
            data = np.frombuffer(raw, dtype=np.float32)
        elif fmt == pyaudio.paInt16:
            data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        elif fmt == pyaudio.paInt24:
            raw_u8 = np.frombuffer(raw, dtype=np.uint8)
            n_samples = len(raw_u8) // 3
            raw_u8 = raw_u8[:n_samples * 3]
            padded = np.zeros((n_samples, 4), dtype=np.uint8)
            padded[:, :3] = raw_u8.reshape(-1, 3)
            as_int32 = padded.view(np.int32).flatten()
            sign_bit = 0x800000
            mask_24 = 0xFFFFFF
            data = np.where(
                (as_int32 & sign_bit) != 0,
                as_int32 | ~np.int32(mask_24),
                as_int32 & mask_24,
            ).astype(np.float32) / 8388608.0
        elif fmt == pyaudio.paInt32:
            data = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
        else:
            raise ValueError(f"不支持的音频格式: {fmt}")

        total_frames = len(data) // channels
        if total_frames == 0:
            return np.empty((0, channels), dtype=np.float32)
        return data[:total_frames * channels].reshape(total_frames, channels)

    @staticmethod
    def _resample(audio: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
        """线性插值重采样（单声道一维数组）"""
        if src_rate == dst_rate:
            return audio.astype(np.float32)
        duration = len(audio) / src_rate
        n_out = int(np.ceil(duration * dst_rate))
        t_old = np.linspace(0, duration, len(audio), endpoint=False)
        t_new = np.linspace(0, duration, n_out, endpoint=False)
        return np.interp(t_new, t_old, audio).astype(np.float32)

    # ==================== 主采集循环 ====================

    def _run(self):
        """后台线程主循环：打开音频流 → 循环读取 → Silero VAD 判别 → 切片入队"""
        self._pa = pyaudio.PyAudio()

        try:
            # ---- 1. 查找 Loopback 设备 ----
            dev = self._find_loopback(self._pa)
            dev_index = dev["index"]
            detail = self._pa.get_device_info_by_index(dev_index)
            src_rate = int(detail["defaultSampleRate"])
            src_channels = detail["maxInputChannels"]
            logger.info("源音频格式: %d Hz, %d 声道", src_rate, src_channels)

            # ---- 2. 打开音频输入流 ----
            read_frames = int(src_rate * READ_CHUNK_DURATION_SEC)
            for fmt, fmt_name in [(pyaudio.paFloat32, "paFloat32"), (pyaudio.paInt16, "paInt16")]:
                try:
                    self._stream = self._pa.open(
                        format=fmt,
                        channels=src_channels,
                        rate=src_rate,
                        input=True,
                        input_device_index=dev_index,
                        frames_per_buffer=read_frames,
                    )
                    actual_fmt = fmt
                    logger.info("音频流已打开: %s, buffer=%d 帧", fmt_name, read_frames)
                    break
                except OSError:
                    logger.debug("%s 打开失败，尝试下一个…", fmt_name)
            else:
                raise RuntimeError("无法打开 Loopback 音频流")

            logger.info(">>> 音频采集已启动，开始监听...")

            # ---- 3. 神经网络 VAD 切片状态机 ----
            vad_iterator = self.VADIterator(
                self.vad_model,
                threshold=0.5,
                sampling_rate=TARGET_SAMPLE_RATE,
                min_silence_duration_ms=300
            )

            speech_buffer: list[np.ndarray] = []
            is_speaking = False
            max_consecutive_frames = int(MAX_CHUNK_DURATION_SEC * TARGET_SAMPLE_RATE / VAD_FRAME_SIZE)

            while not self._stop_event.is_set():
                try:
                    raw = self._stream.read(read_frames, exception_on_overflow=False)
                except OSError as e:
                    logger.error("音频流读取失败: %s", e)
                    break

                audio_chunk = self._bytes_to_float32(raw, actual_fmt, src_channels)
                if audio_chunk.size == 0:
                    continue
                mono = audio_chunk.mean(axis=1) if src_channels > 1 else audio_chunk[:, 0]
                resampled = self._resample(mono, src_rate, TARGET_SAMPLE_RATE)

                n_frames = len(resampled) // VAD_FRAME_SIZE

                for i in range(n_frames):
                    start_idx = i * VAD_FRAME_SIZE
                    frame = resampled[start_idx:start_idx + VAD_FRAME_SIZE]

                    tensor_frame = torch.from_numpy(frame)
                    speech_dict = vad_iterator(tensor_frame, return_seconds=False)

                    if speech_dict is not None:
                        if 'start' in speech_dict:
                            is_speaking = True
                            speech_buffer.clear()
                            speech_buffer.append(frame.copy())

                        elif 'end' in speech_dict:
                            is_speaking = False
                            speech_buffer.append(frame.copy())
                            self._emit_chunk(speech_buffer)
                            speech_buffer.clear()
                    else:
                        if is_speaking:
                            speech_buffer.append(frame.copy())

                            if len(speech_buffer) >= max_consecutive_frames:
                                self._emit_chunk(speech_buffer)
                                speech_buffer.clear()
                                vad_iterator.reset_states()
                                is_speaking = False

        except Exception:
            logger.exception("音频采集线程发生未预期异常")
        finally:
            if speech_buffer:
                self._emit_chunk(speech_buffer)
            if self._stream:
                try:
                    self._stream.stop_stream()
                    self._stream.close()
                except Exception:
                    pass
            if self._pa:
                self._pa.terminate()
            logger.info("音频采集线程已退出")

    def _emit_chunk(self, buffer: list[np.ndarray]):
        """将缓冲区中的语音帧拼接，过短则丢弃，合格则入队"""
        chunk = np.concatenate(buffer)
        duration_sec = len(chunk) / TARGET_SAMPLE_RATE

        if duration_sec < MIN_CHUNK_DURATION_SEC:
            logger.debug("丢弃过短切片 (%.0fms < %.0fms)，疑似噪音误判",
                         duration_sec * 1000, MIN_CHUNK_DURATION_SEC * 1000)
            return

        logger.info("切片产出 | 时长: %.1fms | 采样点: %d",
                     duration_sec * 1000, len(chunk))
        self._queue.put(chunk)

    # ==================== 公共接口 ====================

    def start(self):
        """启动音频采集后台线程（只能调用一次）"""
        if self._thread is not None:
            raise RuntimeError("AudioCapture 已经启动，不可重复调用 start()")
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="AudioCapture"
        )
        self._thread.start()
        logger.info("AudioCapture 线程已创建并启动")

    def stop(self):
        """停止音频采集，等待线程退出（最多等待 3 秒）"""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        logger.info("AudioCapture 已完全停止")


# ==================== 模块入口函数 ====================

def start_audio_capture(audio_queue: queue.Queue) -> AudioCapture:
    """启动音频采集的"生产者线程"，捕获系统播放声音并按意群切片。

    参数
    ----
    audio_queue : queue.Queue
        输出队列，每个元素是一个 np.ndarray：
        - dtype: float32
        - shape: (n_samples,)  单声道
        - sample_rate: 16000 Hz
        - 值域: [-1.0, 1.0]

    返回
    ----
    AudioCapture 实例，调用 .stop() 可停止采集。
    """
    capture = AudioCapture(audio_queue)
    capture.start()
    return capture


# ==================== 测试入口 ====================

if __name__ == "__main__":
    import sys
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
    print("  AI 同声传译助手 — 音频采集模块测试")
    print("  请在系统上打开任意英文视频 / 音频开始播放...")
    print("  按 Ctrl+C 停止采集")
    print("=" * 60)

    q: queue.Queue = queue.Queue()
    capture = start_audio_capture(q)

    try:
        chunk_index = 0
        while True:
            chunk = q.get()
            chunk_index += 1
            dur_sec = len(chunk) / TARGET_SAMPLE_RATE
            print(f"  >> 切片 #{chunk_index} | 时长={dur_sec:.2f}秒 | 采样点={len(chunk)}")
    except KeyboardInterrupt:
        print("\n  用户中断，正在停止...")
    finally:
        capture.stop()
        print("  已退出。")
