# AI 同声传译助手

实时英文语音 → 中文翻译 → 桌面悬浮字幕，100% 本地运行。

## 环境准备

### 前提条件

**需要安装 Anaconda 或 Miniconda**（任选其一）：

| 选择 | 下载 | 大小 |
|------|------|------|
| Miniconda（推荐） | [docs.anaconda.com/miniconda/install/](https://docs.anaconda.com/miniconda/install/) | ~50MB |
| Anaconda（完整版） | [anaconda.com/download](https://www.anaconda.com/download) | ~800MB |

> 为什么需要 Conda？翻译引擎 `llama-cpp-python` 在 Windows 上没有预编译的 pip 包，需要 MSVC 编译器才能从源码安装。Conda 提供了预编译版本，免去手动安装编译器的麻烦。
>
> 如果你已经有 Visual Studio 和 MSVC 编译器，可以直接 Python 3.12 + pip 安装，不需要 Conda。

安装完成后，打开终端验证：

```bash
conda --version
```

## 安装运行

### 一键启动

双击 `start.bat`，首次运行会自动完成：

1. 查找 Conda
2. 创建专用 Python 3.12 环境（首次 3-5 分钟）
3. 安装所有依赖包
4. 下载 AI 翻译模型 `Qwen2.5-1.5B GGUF`（~1.2GB，仅首次）
5. 启动程序

> 之后每次双击 `start.bat` 即可直接使用，无需重复安装。

### 手动安装

```bash
# 1. 创建 Conda 环境
conda create -n ai_translator python=3.12 -y

# 2. 安装 llama-cpp-python（预编译包）
conda install -c conda-forge llama-cpp-python -n ai_translator -y

# 3. 安装其余依赖
conda run -n ai_translator pip install -r requirements.txt

# 4. 启动（设置国内镜像加速模型下载）
set HF_ENDPOINT=https://hf-mirror.com
conda run -n ai_translator python main.py
```

## 可选配置（Gemini 云端精修）

默认使用纯本地 Qwen 1.5B 模型翻译，完全免费、无需联网。

如果希望翻译质量更好，可以填入 Gemini API Key 启用云端精修：

1. 访问 [Google AI Studio](https://aistudio.google.com/apikey) 获取免费 API Key
2. 启动程序后首次会弹出引导，填入 Key 即可
3. 或直接编辑 `config.json`：

```json
{
    "gemini_api_key": "你的API-Key"
}
```

不填 Key 完全不影响使用。

## 使用说明

| 操作 | 方法 |
|------|------|
| 移动窗口 | 按住字幕任意位置拖动 |
| 锁定穿透 | 右键字幕 → "锁定（穿透点击）" |
| 关闭程序 | 右键字幕 → "关闭" |

## 架构

```
系统音频 → WASAPI 内录 → VAD 切片 → Faster-Whisper STT → Qwen 1.5B GGUF 翻译 → 悬浮字幕
                                                    └── (可选) Gemini 精修
```

## 技术栈

| 模块 | 方案 |
|------|------|
| 音频采集 | pyaudiowpatch + WASAPI Loopback |
| VAD 静音检测 | 自研能量检测（纯 numpy） |
| 语音识别 | Faster-Whisper tiny（本地） |
| 翻译引擎 | Qwen2.5-1.5B GGUF Q4_K_M（llama-cpp-python） |
| 前端 UI | PyQt5 全透明悬浮窗 |

## 特性

- **100% 本地运行**：无需 API Key 也能用
- **混合翻译**：Qwen 秒出草稿 + Gemini 云端精修（可选）
- **全透明悬浮窗**：不遮挡视频内容，可拖动，右键锁定穿透
- **一键启动**：双击 `start.bat` 即用
- **离线可用**：模型缓存后无需联网
- **国内友好**：自动使用 HuggingFace 和 pip 镜像加速

## 常见问题

### 启动时提示 "Conda not found"

需要先安装 Miniconda 或 Anaconda，见[环境准备](#环境准备)。

### 模型下载失败

程序默认使用 `hf-mirror.com` 国内镜像下载模型。如果仍失败，可以手动下载：

1. 下载 [Qwen2.5-1.5B-Instruct-Q4_K_M.gguf](https://hf-mirror.com/bartowski/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/Qwen2.5-1.5B-Instruct-Q4_K_M.gguf)（~1.2GB）
2. 放到 `C:\Users\<用户名>\.cache\ai_translator\models\`
3. 重新启动

### 翻译延迟太高

可尝试减小音频切片上限：编辑 `audio_capture.py`，将 `MAX_CHUNK_DURATION_SEC` 从 5 改为 3。

## 文件说明

| 文件 | 功能 |
|------|------|
| `main.py` | 主入口 |
| `audio_capture.py` | WASAPI 内录 + VAD 切片 |
| `ai_processor.py` | 翻译处理器（Qwen + Gemini） |
| `stt_engine.py` | STT 语音识别 |
| `ui_main.py` | PyQt5 悬浮字幕窗 |
| `config_manager.py` | 配置管理 |
| `start.bat` | 一键启动脚本 |
