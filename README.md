# AI 同声传译助手

实时英文语音 → 中文翻译 → 桌面悬浮字幕，100% 本地运行。

## 架构

```
系统音频 → WASAPI 内录 → VAD 切片 → Faster-Whisper STT → Qwen 翻译 → 悬浮字幕窗
```

## 快速开始

```bash
# 安装依赖
pip install pyaudiowpatch numpy faster-whisper transformers torch modelscope PyQt5 google-genai httpx

# 启动
python main.py
# 或双击 start.bat
```

## 文件说明

| 文件 | 功能 |
|------|------|
| `main.py` | 主入口，串联全链路 |
| `audio_capture.py` | WASAPI Loopback 内录 + 能量 VAD 切片 |
| `ai_processor.py` | AI 翻译处理器 (Qwen + Gemini 混合) |
| `stt_engine.py` | STT 语音识别引擎 |
| `ui_main.py` | PyQt5 全透明悬浮字幕窗 |
| `config_manager.py` | 配置管理（首次引导 + 持久化） |
| `start.bat` | Windows 一键启动脚本 |

## 特性

- **100% 本地运行**：无需 API Key 也能用
- **混合翻译**：Qwen 秒出草稿 + Gemini 云端精修
- **全透明悬浮窗**：不遮挡视频内容，可拖动，右键锁定穿透
- **零门槛配置**：首次运行交互式引导，配置自动保存
