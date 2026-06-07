# AI 同声传译助手

实时英文语音 → 中文翻译 → 桌面悬浮字幕，100% 本地运行。

## 环境准备

### 前提条件

- **Python 3.12+**（推荐通过 Miniconda 管理环境，非必需但建议）

| 选择 | 下载 | 大小 |
|------|------|------|
| Miniconda（推荐） | [docs.anaconda.com/miniconda/install/](https://docs.anaconda.com/miniconda/install/) | ~50MB |
| 系统 Python | [python.org/downloads](https://www.python.org/downloads/) | ~25MB |

> Conda 提供隔离的 Python 环境，但不是必须的。`start.bat` 会自动检测 Conda，没有则用系统 Python 创建 venv。

### GPU 加速（推荐）

如果你有 **NVIDIA 显卡**，程序会自动启用 GPU 加速——只需显卡驱动支持 CUDA 12.1+（大多数现代驱动已满足），无需安装 CUDA Toolkit 或任何编译工具链。

### 验证

```bash
# 如果用 Conda
conda --version

# 如果用系统 Python
python --version
```

## 安装运行

### 一键启动

双击 `start.bat`，自动完成依赖检测：

- 依赖已安装 → `[OK] Fast startup!` 秒进程序
- 依赖缺失 → 自动走安装流程

首次运行会自动完成：

1. 查找 Conda / 系统 Python
2. 创建专用 Python 3.12 环境（首次 3-5 分钟）
3. 安装所有依赖包
4. 下载 AI 翻译模型 `Qwen2.5-1.5B GGUF`（~1.2GB，仅首次）
5. 下载 Silero VAD 模型（~2MB，仅首次）
6. 启动程序

> 之后每次双击 `start.bat` 即可直接使用，无需重复安装。

### 手动安装

```bash
# 1. 创建环境（二选一）
conda create -n ai_translator python=3.12 -y       # Conda 用户
python -m venv .venv                                 # 系统 Python 用户

# 2. 安装基础依赖
pip install -r requirements.txt

# 3. 安装 llama-cpp-python（GPU / CPU 二选一）
# GPU 版（NVIDIA 显卡，~50MB 预编译包，无需编译器）：
pip uninstall -y llama-cpp-python
pip install llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu121

# CPU 版（无显卡或非 NVIDIA）：
pip install llama-cpp-python

# 4. 启动（设置国内镜像加速模型下载）
set HF_ENDPOINT=https://hf-mirror.com
python main.py
```

## 可选配置（Gemini 云端精修）

默认使用纯本地 Qwen 1.5B 模型翻译，完全免费、无需联网。

如果希望翻译质量更好，可以填入 Gemini API Key 启用云端精修。Gemini 会在异步线程池中运行，不阻塞主流程的秒出草稿：

1. 访问 [Google AI Studio](https://aistudio.google.com/apikey) 获取免费 API Key
2. 启动程序后首次会弹出引导，填入 Key 即可
3. 或直接编辑 `config.json`：

```json
{
    "gemini_api_key": "你的API-Key",
    "local_model_path": ""
}
```

`local_model_path` 留空则自动下载模型到 `~/.cache/ai_translator/models/`。

## 使用说明

| 操作 | 方法 |
|------|------|
| 移动窗口 | 按住字幕任意位置拖动 |
| 锁定穿透 | 右键字幕 → "锁定（穿透点击）" |
| 关闭程序 | 右键字幕 → "关闭" |

## 架构

```
系统音频 → WASAPI Loopback ──→ Silero VAD 神经网络切片
                                    │
                                    ▼
                            Faster-Whisper STT
                          （幻觉过滤 + 去重）
                                    │
                         ┌──────────┴──────────┐
                         ▼                     ▼
                  Qwen GGUF 主线程秒出草稿   Gemini 异步线程池精修
                         │                     │
                         └──────────┬──────────┘
                                    ▼
                        悬浮字幕窗（HTML 多行滚动）
                         历史句纯白 · 草稿浅灰斜体
```

## 技术栈

| 模块 | 方案 |
|------|------|
| 音频采集 | pyaudiowpatch + WASAPI Loopback |
| VAD 静音检测 | Silero VAD（神经网络，~2MB） |
| 语音识别 | Faster-Whisper tiny（本地，300MB，GPU 加速） |
| 翻译引擎 | Qwen2.5-1.5B GGUF Q4_K_M（llama-cpp-python，~1.2GB，GPU 加速） |
| 云端精修 | Gemini 2.5 Flash（可选，异步线程池） |
| 前端 UI | PyQt5 全透明悬浮窗 + HTML 富文本多行渲染 |

## 特性

- **100% 本地运行**：无需 API Key 也能用
- **GPU 加速**：有 NVIDIA 显卡自动启用，Qwen + Whisper 跑在显存里，无需安装 CUDA Toolkit
- **异步混合翻译**：Qwen 主线程秒出草稿 + Gemini 后台精修，互不阻塞
- **Silero VAD 神经网络切片**：替代能量检测，精准识别语音边界，零校准等待
- **追溯修正**：Gemini 检测到残句合并时自动覆写上一句翻译
- **全透明悬浮窗**：不遮挡视频内容，可拖动，右键锁定穿透
- **多行滚动字幕**：历史句白色正体 + 草稿浅灰斜体，一目了然
- **一键启动**：双击 `start.bat` 即用，自动检测依赖、自动安装 CUDA 版推理引擎
- **离线可用**：模型缓存后无需联网
- **国内友好**：自动使用 HuggingFace 和 pip 镜像加速

## 常见问题

### 启动时提示 "Conda not found"

`start.bat` 会自动查找系统中的 Python。如果用系统 Python + venv 方案，不需要 Conda。如果你偏好 Conda，见[环境准备](#环境准备)。

### 如何确认 GPU 加速已生效

启动时看到 `[OK] CUDA enabled` 即表示 GPU 加速已启用。也可以在 Python 中验证：

```bash
python -c "import llama_cpp; print('CUDA:', llama_cpp.llama_supports_gpu_offload())"
```

输出 `CUDA: True` 即为 GPU 模式。否则会自动回退 CPU。

### 模型下载失败

程序默认使用 `hf-mirror.com` 国内镜像下载模型。如果仍失败，可以手动下载：

1. 下载 [Qwen2.5-1.5B-Instruct-Q4_K_M.gguf](https://hf-mirror.com/bartowski/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/Qwen2.5-1.5B-Instruct-Q4_K_M.gguf)（~1.2GB）
2. 放到 `C:\Users\<用户名>\.cache\ai_translator\models\`
3. 重新启动

### Silero VAD 模型下载失败

Silero VAD 通过 `torch.hub` 从 GitHub 下载（~2MB）。如果网络不通，可先设置代理：

```bash
set GITHUB_PROXY=https://ghproxy.com/
```

### 翻译延迟太高

可尝试调整音频切片上限：编辑 `audio_capture.py`，将 `MAX_CHUNK_DURATION_SEC` 减小（当前默认 4 秒）。

## 文件说明

| 文件 | 功能 |
|------|------|
| `main.py` | 主入口，串联全链路 |
| `audio_capture.py` | WASAPI Loopback 内录 + Silero VAD 切片 |
| `ai_processor.py` | AI 翻译处理器（Qwen 本地 + Gemini 异步） |
| `stt_engine.py` | STT 语音识别引擎（Faster-Whisper + 幻觉过滤） |
| `ui_main.py` | PyQt5 全透明悬浮字幕窗（HTML 多行滚动） |
| `config_manager.py` | 配置管理（首次引导 + 持久化） |
| `start.bat` | Windows 一键启动（依赖检测 + 自动安装） |
| `requirements.txt` | Python 依赖清单 |
| `dev_log.md` | 完整开发日志 |
