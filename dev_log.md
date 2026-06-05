# AI 同声传译助手 — 开发日志

## 目录
1. [需求概述](#需求概述)
2. [技术选型](#技术选型)
3. [第一阶段：音频采集](#第一阶段音频采集)
4. [第二阶段：翻译引擎](#第二阶段翻译引擎)
5. [第三阶段：悬浮字幕 UI](#第三阶段悬浮字幕-ui)
6. [第四阶段：STT 接入](#第四阶段-stt-接入)
7. [第五阶段：Gemini 限流与本地化](#第五阶段gemini-限流与本地化)
8. [第六阶段：Qwen 替换 OPUS-MT](#第六阶段qwen-替换-opus-mt)
9. [第七阶段：v0.6 稳定性与性能](#第七阶段v06-稳定性与性能)
10. [最终架构](#最终架构)
11. [文件清单](#文件清单)

---

## 需求概述

**目标**：开发一款 AI 同声传译助手，将英语（或其他外语）单向音频流实时翻译成中文，以字幕形式呈现。

**核心要求**：
- 实时音频捕获与翻译
- 字幕悬浮窗显示
- 自动纠正识别/翻译错误
- 100% 本地运行（最终方案）

---

## 技术选型

| 模块 | 最终方案 | 说明 |
|------|----------|------|
| 语言 | Python 3.13 | Anaconda 环境 |
| 音频采集 | pyaudiowpatch + numpy | WASAPI Loopback 内录 |
| VAD 静音检测 | 自研能量检测 | 纯 numpy，基于 RMS 分位数校准 |
| 语音识别 (STT) | Faster-Whisper tiny | 本地免费，300MB |
| 翻译引擎 | Qwen2.5-0.5B（+ Gemini 可选） | 默认纯本地，Gemini 可选精修 |
| 前端 UI | PyQt5 | 全透明悬浮窗 |
| 部署 | start.bat | 双击即用，自动创建 venv |

---

## 第一阶段：音频采集

**文件**：`audio_capture.py`

### 技术决策过程

1. **最初方案：silero-vad**
   - 需要 PyTorch → Python 3.13 下 DLL 加载失败
   - 多次重装后仍然报 `c10.dll` 错误
   
2. **切换到 webrtcvad**
   - 需要 C++ 编译器 → 系统未安装 MSVC
   - 编译失败

3. **最终方案：自研能量检测 VAD**
   - 纯 numpy，零外部依赖
   - 基于 RMS 能量 + 分位数校准
   - 自动校准噪声基准，顶底限幅防跑偏

### 核心参数演变

| 参数 | 初始值 | 最终值 | 原因 |
|------|--------|--------|------|
| VAD 帧长 | 30ms (480采样) | 32ms (512采样) | silero-vad 要求 |
| 静音阈值 | 500ms | 600ms | 减少碎片 |
| 最小切片 | 无 | 0.8s | 过滤噪音碎片 |
| 最长切片 | 15s | 5s | 小块降低延迟 |
| 噪声上限 | 无 | 0.03 | 防止校准时音频干扰 |
| 语音灵敏度 | 2.5x | 1.5x | 轻声也能检测 |

### 关键 Bug 修复

- **Bug**：校准时播放音频导致噪声基准过高（0.22）
  - **修复**：改用 20% 分位数 + 噪声上限 0.03
  - **修复**：启动时等待校准完成后再提示用户播放音频

---

## 第二阶段：翻译引擎

**文件**：`ai_processor.py`

### 迭代过程

1. **Mock STT + Gemini 文本翻译**
   - 随机模拟英文句子
   - Gemini 翻译质量好，但受限于文本 API 配额

2. **Gemini 直接听译（跳过 STT）**
   - Gemini 2.5 Flash 支持音频输入
   - 但免费版音频 API 每分钟限 5 次 → 429 错误
   - 延迟高（8s 音频需 14s 处理）

3. **STT + Gemini 文本翻译（回退）**
   - Faster-Whisper 做 STT，Gemini 做翻译
   - 文本 API 每天限 20 次 → 仍然不够

4. **OPUS-MT 本地翻译**
   - 永久免费，但翻译质量差
   - 混杂英文、重复输出、不通顺

5. **Qwen2.5-0.5B 本地翻译（当前）**
   - 质量接近 Gemini
   - ~1GB 模型，CPU 推理 3-8s
   - 支持混合模式（本地 + Gemini 精修）

### 翻译质量对比

| 模型 | 速度 | 质量 | 费用 |
|------|------|------|------|
| Gemini 2.5 Flash | 2-5s | 优秀 | 免费版 20次/天 |
| Qwen2.5-0.5B | 3-8s | 良好 | 永久免费 |
| OPUS-MT | 0.5s | 较差 | 永久免费 |

### 混合翻译模式

```
音频 → Whisper → Qwen 草稿 (3-5s) → 悬浮窗灰色
                       ↓
                  Gemini 精修 (2-5s) → 悬浮窗白色覆盖
```

---

## 第三阶段：悬浮字幕 UI

**文件**：`ui_main.py`

### 设计迭代

1. **第一版**：毛玻璃背景 + 自定义标题栏
   - 用户反馈：太丑

2. **第二版**：全透明背景 + 居中文字
   - 白字黑边（QGraphicsDropShadowEffect）
   - 拖动移动、右键锁定穿透
   - 字号：final 18pt Bold / draft 13pt 斜体

### 线程安全通信

```
DataFetcherThread (QThread)
    ↓ pyqtSignal(dict)
update_labels() → final_label / draft_label
```

### 交互方式

| 操作 | 方法 |
|------|------|
| 移动窗口 | 按住任意位置拖动 |
| 锁定穿透 | 右键 → "锁定（穿透点击）" |
| 关闭 | 右键 → "关闭" |

---

## 第四阶段：STT 接入

**文件**：`stt_engine.py`

### 尝试过的方案

1. **Deepgram REST API**
   - 低延迟高精度
   - 免费额度 $200 但有限

2. **Groq Whisper API**
   - 极快
   - 需注册

3. **Faster-Whisper 本地**
   - 最终选择
   - 永久免费，tiny 模型 300MB

### 引擎架构

```python
BaseSTTEngine          # 抽象接口
├── MockSTT            # 模拟（开发用）
├── DeepgramSTT        # Deepgram REST API
└── FasterWhisperSTT   # 本地 Whisper（当前）
```

---

## 第五阶段：Gemini 限流与本地化

### 问题

Gemini 免费版限制：
- 音频 API：5 次/分钟
- 文本 API：20 次/天

实时翻译每秒切一次片 → 几分钟烧光全天额度。

### 解决方案

1. 切换 Faster-Whisper 做 STT（本地）
2. 切换到本地翻译模型（避限流）
3. 混合模式：本地秒出 + Gemini 精修

---

## 第六阶段：Qwen 替换 OPUS-MT

### 原因

OPUS-MT 翻译质量差：
- 中文输出混杂英文
- 重复词汇
- 不通顺

### 方案

Qwen2.5-0.5B-Instruct：
- 1GB 模型
- 通用 LLM，翻译质量接近云端
- CPU 推理 3-8s

### 安装问题

- llama-cpp-python 需要 C++ 编译器 → 失败
- 改用 transformers 直接加载，CPU 推理

---

## 第七阶段：v0.6 稳定性与性能

### 故事背景

v0.5 发布后，在实际使用中暴露了多个问题：

1. **DLL 崩溃**：`start.bat` 双击启动后，Faster-Whisper STT 引擎加载时 `c10.dll` 初始化失败（WinError 1114）
2. **延迟过高**：一句话从听到到翻译出来需要 5-15 秒，完全跟不上实时对话
3. **卡死**：积压的音频块越来越多，队列满后整条流水线阻塞
4. **字幕重叠**：翻译失败时的 `[翻译错误]` 残留，和新翻译叠在一起
5. **断网启动不了**：ModelScope 每次启动都强制联网检查，网络不稳就崩溃

同时明确了产品定位：从"专用于特定场景"升级为"面向大众的通用产品"。

---

### 7.1 DLL 地狱终极修复

**文件**：`main.py`, `stt_engine.py`

#### 根因

Python 3.13 收紧了 Windows 上 DLL 搜索规则。`PyQt5` 在导入时会加载自身的 Qt DLL（`Qt5Core.dll` 等），污染 DLL 搜索路径。当 `ctranslate2` → `torch` → `c10.dll` 这条链在 PyQt5 **之后**触发时，Windows 加载器在 Qt 目录里找到了不兼容的依赖版本，导致 `WinError 1114`。

直接 `import torch` 可以成功，通过 PyQt5 之后的导入链就失败。

#### 修复方案

```python
# main.py — torch 必须抢在 PyQt5 之前导入
import torch  # noqa: E402  — 必须在 PyQt5 之前
from PyQt5.QtWidgets import QApplication

# stt_engine.py — 防御性注册 DLL 搜索路径
if sys.platform == "win32":
    import torch
    os.add_dll_directory(os.path.join(os.path.dirname(torch.__file__), "lib"))
```

**教训**：Python 3.13 + 科学计算库在 Windows 上仍是高风险组合。DLL 导入顺序至关重要。

---

### 7.2 缺依赖修复

**文件**：`requirements.txt`

`transformers` 使用 `device_map="cpu"` 需要 `accelerate` 包，但 `requirements.txt` 里没写。

新增 `accelerate` 到依赖列表。

---

### 7.3 Qwen 翻译延迟优化

**文件**：`ai_processor.py`, `stt_engine.py`

#### 瓶颈分析

| 环节 | 优化前 | 优化后 | 手段 |
|------|--------|--------|------|
| Whisper STT | beam_size=5 | beam_size=1 | 贪婪解码 ≈ 1.5× 快 |
| Qwen 翻译 | max_tokens=128, 无优化 | max_tokens=64, inference_mode | ≈ 2× 快 |
| Qwen 线程 | 固定 4 核 | `cpu_count() - 2` | 充分利用多核 |
| 队列积压 | 全处理 | 跳过旧块只保留最新 | 消除延迟累积 |

#### 队列跳过逻辑

```python
# ai_processor.py — 积压时只处理最新音频块
dropped = 0
while True:
    try:
        audio_chunk = audio_queue.get_nowait()
        dropped += 1
    except queue.Empty:
        break
if dropped:
    logger.info("跳过 %d 个积压音频块，只处理最新", dropped)
```

**效果**：端到端延迟从 5-15s 降至 3-6s。

---

### 7.4 关闭 Gemini 混合模式

**文件**：`config.json`

#### 问题

Gemini 混合模式（Qwen 秒出草稿 + Gemini 云端精修）虽然翻译质量好，但 Gemini 的网络请求增加 2-5 秒延迟，收益不明显。

#### 决策

`config.json` 中 `gemini_api_key` 清空，默认纯本地 Qwen 模式。用户如需精修可自行填入 Key。

---

### 7.5 ModelScope 离线启动

**文件**：`ai_processor.py`

#### 问题

`snapshot_download()` 每次启动都强制连接 `modelscope.cn` 检查更新。网络不稳时超时/连接重置导致启动崩溃，即使模型已经完整缓存在本地。

#### 修复

```python
# 先查本地缓存，命中直接跳过网络
cache_root = os.path.expanduser("~/.cache/modelscope/hub/models")
local_path = os.path.join(cache_root, owner, repo.replace(".", "___"))
if os.path.isdir(local_path) and os.path.exists(os.path.join(local_path, "model.safetensors")):
    logger.info("使用本地缓存: %s", local_path)
else:
    local_path = snapshot_download(model_name)  # 仅首次/缓存失效时联网
```

---

### 7.6 字幕 UI 修复

**文件**：`ui_main.py`

#### 问题

`update_labels()` 方法是条件设置 label 文本的——只有新数据包含 `final_translation` 时才更新 `final_label`。如果上次翻译失败留下了 `[翻译错误]`，而这次只有草稿没有最终翻译，旧错误文本就残留在屏幕上，和新字幕重叠。

#### 修复

```python
# 始终同步设置两个 label，空串即清空
def update_labels(self, data: dict):
    final = data.get("final_translation", "")
    draft = data.get("draft_translation", "")
    # 过滤掉错误消息（以 [ 开头），避免显示 [翻译错误] 等内部标记
    self.final_label.setText(final if final and not final.startswith("[") else "")
    self.draft_label.setText(draft if draft and not draft.startswith("[") else "")
```

---

### 7.7 动态记忆池（v0.6 核心新功能）

**文件**：`stt_engine.py`

#### 需求

v0.5 的 STT 识别是逐句独立进行的，缺乏上下文。Whisper 不知道"刚才在聊什么话题"，容易把同音异义词识别错。

#### 实现

```python
class FasterWhisperSTT:
    def __init__(self, ...):
        self._memory = ""  # 动态记忆池，不硬编码任何主题

    def transcribe(self, audio_chunk):
        segments, _ = self._model.transcribe(
            ...,
            initial_prompt=self._memory or None,  # 传入历史上下文
        )
        ...
        if transcript:
            # 滑动窗口：只保留最后 200 个字符
            self._memory = (self._memory + " " + transcript)[-200:].strip()
```

**效果**：Whisper 能利用前文推断当前话题领域，减少同音词识别错误。

---

### 7.8 通用万能同传 System Prompt

**文件**：`ai_processor.py`

#### 需求

v0.5 的翻译 Prompt 是固定领域导向的。v0.6 升级为通用产品，需要模型自动推断领域。

#### 新 Prompt（Gemini / 云端）

```
你是一位极其专业的通用同声传译员。
【核心挑战】：输入通常是不完整的片段（残句），并且可能包含由于机器听音导致的同音词错误。
【你的职责】：
1. 动态推理：根据历史前文，迅速判断当前领域（科技、娱乐、医学、日常闲聊等）
2. 智能纠错：利用领域背景，自动修正同音词识别错误
3. 意译输出：输出符合中文表达习惯的流畅翻译
## 严格 JSON（必须遵守）
{"final_translation": "...", "draft_translation": "..."}
```

#### Qwen 本地 Prompt

小模型用简版 + few-shot 示例，防止输出英文原文：

```python
messages = [
    {"role": "system", "content": "你是英中翻译器。禁止输出英文单词。只输出中文译文。"},
    {"role": "user", "content": "翻译: Hello, how are you today?"},
    {"role": "assistant", "content": "你今天好吗？"},          # few-shot 示例
    {"role": "user", "content": "翻译: " + text},
]
```

同时加入输出检测：如果英文单词占比 >50%，标记 `[翻译失败]` 不在字幕显示。

---

## 最终架构

```
系统音频
    │
    ▼
WASAPI Loopback ──→ 能量 VAD 切片
    │
    ▼
Faster-Whisper tiny + 动态记忆池 ──→ 英文文本（含上下文纠错）
    │
    ▼
Qwen2.5-0.5B (3-6s) ──→ 本地翻译 ──→ 悬浮窗
    │
    └──→ (可选) Gemini 精修
```

**默认纯本地运行**，无需 API Key，无需联网。

---

## 文件清单

```
Ai_Translator/
├── main.py              # 主入口，串联全链路
├── audio_capture.py     # WASAPI 内录 + VAD 切片
├── ai_processor.py      # AI 翻译处理器 (Qwen + Gemini)
├── stt_engine.py        # STT 语音识别引擎 (Faster-Whisper)
├── ui_main.py           # PyQt5 悬浮字幕窗
├── config_manager.py    # 配置管理（首次引导 + 持久化）
├── config.json          # 用户配置（API Key 等）
├── start.bat            # Windows 双击启动
├── requirements.txt     # Python 依赖
├── dev_log.md           # 本开发日志
└── .gitignore           # Git 忽略规则
```

---

## 运行方式

```bash
# 双击即可
start.bat

# 或终端
.venv\Scripts\python.exe main.py
```

首次运行自动引导配置 API Key（可选），之后无需任何操作。

---

## 经验教训

1. **Python 3.13 + PyTorch DLL 地狱** → torch 必须在 PyQt5 之前导入，顺序决定生死
2. **机器学习依赖太重** → VAD 用纯 numpy，避免 DLL 地狱
3. **免费 API 有限流** → 默认纯本地模式，云端作可选补充
4. **国内网络不稳** → ModelScope 缓存 + 离线优先，不依赖实时联网
5. **用户不想配置环境变量** → config.json + 交互式引导
6. **小模型容易"不听话"** → few-shot 示例 + 输出检测兜底
7. **实时系统必须跳过积压** → 丢弃旧帧比累积延迟更符合用户体验
8. **字幕 UI 状态管理** → 条件更新导致残留，必须全量设置清空旧数据
