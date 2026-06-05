# AI 同声传译助手

实时英文语音 → 中文翻译 → 桌面悬浮字幕，100% 本地运行。

## 环境准备

**唯一需要手动安装的：Python 3.10+**

前往 [python.org](https://www.python.org/downloads/) 下载安装包，安装时**务必勾选 "Add Python to PATH"**。

安装完成后，打开终端验证：

```bash
python --version
# 应输出 Python 3.10.x 或更高
```

> Windows 10/11 用户也可以用系统自带的 winget 一键安装：
> ```bash
> winget install Python.Python.3.12
> ```

## 安装运行

### 方式一：一键启动

双击 `start.bat`，首次运行会自动完成：

1. 查找系统 Python
2. 创建项目专属虚拟环境 `.venv`
3. 安装所有依赖包
4. 下载 AI 模型（首次约 1-2GB）
5. 启动程序

### 方式二：手动安装

```bash
# 1. 创建虚拟环境
python -m venv .venv

# 2. 激活虚拟环境
.venv\Scripts\activate

# 3. 安装依赖
pip install -r requirements.txt

# 4. 启动
python main.py
```

### requirements.txt

```txt
numpy
pyaudiowpatch
faster-whisper
transformers
torch
accelerate
modelscope
PyQt5
google-genai
httpx
```

## 可选配置（Gemini 云端精修）

默认使用纯本地 Qwen 模型翻译，完全免费、无需联网。

如果希望翻译质量更好，可以填入 Gemini API Key 启用云端精修：

1. 访问 [Google AI Studio](https://aistudio.google.com/apikey) 获取免费 API Key
2. 启动程序后，在弹出的配置界面中填入 Key
3. 或直接编辑 `config.json`：

```json
{
    "gemini_api_key": "你的API-Key",
    "gemini_model": "gemini-2.5-flash"
}
```

不填 Key 也能正常使用，程序会在首次运行时引导你配置。

## 架构

```
系统音频 → WASAPI 内录 → VAD 切片 → Faster-Whisper STT → Qwen 翻译 → 悬浮字幕窗
```

## 使用说明

| 操作 | 方法 |
|------|------|
| 移动窗口 | 按住字幕任意位置拖动 |
| 锁定穿透 | 右键字幕 → "锁定（穿透点击）" |
| 关闭程序 | 右键字幕 → "关闭" |

## 特性

- **100% 本地运行**：无需 API Key 也能用
- **混合翻译**：Qwen 秒出草稿 + Gemini 云端精修（可选）
- **全透明悬浮窗**：不遮挡视频内容，可拖动，右键锁定穿透
- **零门槛配置**：首次运行交互式引导，配置自动保存

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
