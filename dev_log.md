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
10. [第八阶段：v0.7 双轨状态机与 Qwen 深度优化](#第八阶段v07-双轨状态机与-qwen-深度优化)
11. [第九阶段：v0.8 GGUF 量化 + 1.5B 模型升级](#第九阶段v08-gguf-量化--15b-模型升级)
12. [第十阶段：v0.9 Silero VAD + 异步精修 + 多行滚动](#第十阶段v09-silero-vad--异步精修--多行滚动)
13. [最终架构](#最终架构)
14. [文件清单](#文件清单)
15. [运行方式](#运行方式)
16. [经验教训](#经验教训)

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
| 翻译引擎 | Qwen2.5-1.5B GGUF Q4_K_M（+ Gemini 可选） | llama-cpp-python 推理，~1.2GB |
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

## 第八阶段：v0.7 双轨状态机与 Qwen 深度优化

**日期**：2026-06-06

**文件**：`ai_processor.py`, `ui_main.py`

### 背景

v0.6 的翻译输出是单轨的——每次翻译结果直接覆盖上一次，没有"草稿→终稿"的渐进式过渡。同时 Qwen 0.5B 在复杂 Prompt 下表现极不稳定：输出 JSON 格式错误的概率高达 80%，大量句子翻译失败后 fallback 到英文原文，或者模型崩溃后反复输出同一句翻译。

本轮改造的目标：
1. 实现 final/draft 双轨翻译输出的正确状态管理
2. 让 Qwen 0.5B 在 CPU 上稳定产出可用翻译
3. 前端实现"终稿瞬间覆盖草稿"的视觉效果

---

### 8.1 提取通用 JSON 解析器

**文件**：`ai_processor.py`

#### 问题

`TranslationEngine` 内部有 20 行 JSON 解析逻辑（直接解析 → 提取 Markdown 代码块 → 暴力提取花括号 → 兜底），`LocalTranslator` 也需要同样的解析能力，但当时 `LocalTranslator.translate()` 返回的是纯字符串，不涉及 JSON 解析。

#### 解决方案

将 JSON 解析逻辑提取为模块级函数 `parse_translation_json(raw, fallback_text) -> dict`，四级回退策略：

```python
def parse_translation_json(raw: str, fallback_text: str) -> dict:
    # 1) 直接 json.loads
    # 2) 提取 ```json / ``` 代码块
    # 3) 暴力提取第一个 { 到最后一个 }
    # 4) 兜底 → {"final_translation": "", "draft_translation": fallback_text}
```

`TranslationEngine._parse_json()` 的 20 行逻辑浓缩为一行委托：
```python
def _parse_json(self, raw: str) -> dict:
    return parse_translation_json(raw, raw.strip()[:300])
```

---

### 8.2 赋予 Qwen 纠错大脑（第一版 → 失败）

**文件**：`ai_processor.py`

#### 尝试

将 `LocalTranslator.translate()` 签名从 `(text: str) -> str` 改为 `(current_text, context_str) -> dict`：

- 使用 Gemini 同款的 `TRANSLATION_PROMPT`，包含上下文和历史翻译
- System Prompt 强制要求输出 `{"final_translation": "...", "draft_translation": "..."}`
- `max_new_tokens` 从 64 升至 128（为 JSON 结构留空间）
- 输出通过 `parse_translation_json()` 解析

#### 结果：灾难

Qwen 0.5B **完全无法处理**为 Gemini 设计的复杂 Prompt。症状：
- 80% 的输出不是合法 JSON → `parse_translation_json` 兜底返回英文原文 → UI 显示英文
- 模型崩溃后反复输出同一句中文翻译（如连续 3 句都显示 `"非常感谢您给予如此卓越的荣誉。"`）
- 推理延迟增加（128 tokens × 70ms/token ≈ 9秒）

**教训**：0.5B 参数量的模型没有能力同时做"领域推理 + 纠错 + JSON 格式输出"。Prompt 的任务复杂度必须匹配模型容量。

---

### 8.3 简化 Prompt → 引入上下文续写 Bug（第二版）

**文件**：`ai_processor.py`

#### 尝试

新增 `LOCAL_TRANSLATION_PROMPT`，去掉 JSON 要求、领域推理和纠错逻辑，只保留翻译 + 上下文：

```python
LOCAL_TRANSLATION_PROMPT = """根据上文翻译当前英文片段为流畅中文。只输出译文，不要解释。

上文：
{context}

当前："{current}"
译文："""
```

System Prompt 简化为"你是英中翻译器。只输出中文译文。不要JSON。"
`max_new_tokens` 降回 64（后来降至 48）。

上下文格式使用 Gemini 同款的带序号 EN/ZH 对照：
```
1. EN: "For you!"
   ZH: "你！"
2. EN: "This is another step in your life, but for her."
   ZH: "这是你在人生中的另一步，但对她的祝福。"
```

#### 结果：模型把上下文当模板续写

日志揭示了新的失败模式：

```
12:21:33 [翻译] See you in the next video! → 1. EN: "For you!"
   ZH: "你好！"
2. EN: "This is another step
```

Qwen 0.5B 看到 Prompt 中 `EN: "..." ZH: "..."` 的格式后，把它当成了**续写任务**——模型以为自己应该继续输出这个 EN/ZH 列表，而不是翻译当前英文。

**教训**：小模型对 Prompt 中的格式模式极度敏感。任何看起来像"待完成模板"的结构都可能被模型当作续写目标。

---

### 8.4 纯中文上下文 → 依然复制（第三版）

#### 尝试

将上下文格式改为纯中文链，去掉英文和序号：
```
上文：你！；这是你在人生中的另一步，但对她的祝福。；这是一次梦想成真。请突出自己。
```

同时 `LOCAL_TRANSLATION_PROMPT` 去掉 `{context}` 段落，改为更简洁的格式。

#### 结果：模型把中文历史当模板拼贴

```
12:25:56 [翻译] I am their dream come true and their dream.
    → 这是她们的时刻。我的妈妈和爸爸都深深地。；她是她们的时刻。我妈妈和爸爸都深深地。
```

模型把上下文里的分号和中文原文直接吐了出来。给 0.5B 看任何历史文本，它都会试图复制而非理解。

**教训**：0.5B 模型缺乏"区分上下文和任务"的能力。任何形式的上下文都可能被模型当作输出模板。

---

### 8.5 最终方案：彻底去掉上下文（第四版）

**文件**：`ai_processor.py`

#### 方案

```python
LOCAL_TRANSLATION_PROMPT = """将以下英文翻译为流畅中文。只输出译文，不要解释。

英文："{current}"
译文："""
```

去掉 `{context}` 占位符，`translate()` 不再将 `context_str` 注入 Prompt。模型只看到三行内容：指令 + 英文输入 + 译文标记。

```python
def translate(self, current_text: str, context_str: str = "") -> dict:
    prompt = LOCAL_TRANSLATION_PROMPT.format(current=current_text)
    # context_str 参数保留但不再使用
    ...
    # 正常路径：纯中文 → 直接作为 final_translation
    return {"final_translation": raw, "draft_translation": ""}
```

#### 预热优化

`__init__` 中加入一次 dummy 推理，提前触发 PyTorch 懒加载：

```python
dummy = self._tokenizer.apply_chat_template(
    [{"role": "user", "content": "Hello"}], ...)
dummy_in = self._tokenizer(dummy, return_tensors="pt")
self._model.generate(**dummy_in, max_new_tokens=1, ...)
```

消化首次推理的 kernel 编译、KV cache 分配开销，避免真实首句翻译多等 2-3 秒。

---

### 8.6 重构消费者线程历史记忆

**文件**：`ai_processor.py`

#### 变更

1. 在 `ai_worker_thread` 的 `while True:` 循环外部，新增两个滑动窗口记忆池：
   ```python
   en_history: deque[str] = deque(maxlen=3)
   zh_history: deque[str] = deque(maxlen=3)
   ```

2. 纯本地翻译分支（`elif translator:`）：
   - 组装中文历史上下文（不上英文，避免小模型续写）
   - 调用 `translator.translate(english_text, context_str)`
   - 提取返回 dict 中的 `final_translation` 和 `draft_translation`
   - **仅当 final 有效且不是错误标记时**才追加到历史队列

3. 混合模式分支（`if hybrid:`）同步更新：
   - 本地秒出草稿阶段同样使用中文历史上下文
   - Gemini 精修成功后同步更新 `en_history`/`zh_history`
   - Gemini 失败时草稿升格为终稿，也记入历史

#### 关键约束

历史记忆只在翻译**成功产出有效 final** 后才追加。避免错误翻译污染历史，导致后续句子的上下文被带偏。

---

### 8.7 前端视觉覆盖：终稿瞬间覆盖草稿

**文件**：`ui_main.py`

#### 旧逻辑（v0.6）

条件更新——有 final 就设 final_label，有 draft 就设 draft_label。问题：draft 和 final 可能同时显示，造成字幕重叠。

#### 新逻辑（v0.7）

```python
def update_labels(self, data: dict):
    final = data.get("final_translation", "")
    draft = data.get("draft_translation", "")

    if final and not final.startswith("["):
        # 终稿出现 → 瞬间清空草稿
        self.final_label.setText(final)
        self.draft_label.setText("")
    elif draft and not draft.startswith("["):
        # 仅有草稿 → 更新草稿行，终稿保持
        self.draft_label.setText(draft)
```

双轨状态机的语义：
- **终稿优先**：一旦 final 有值，立即占领字幕区，草稿清零
- **草稿兜底**：只有草稿时，显示在副行（斜体小字），终稿行保持上一句不动
- **错误过滤**：以 `[` 开头的内部标记（`[翻译错误]`、`[API异常]` 等）不显示

---

### 8.8 端到端延迟分析

#### 流水线各阶段耗时

| 阶段 | 耗时 | 瓶颈 |
|------|------|------|
| VAD 音频积累 | 3~5秒 | `MAX_CHUNK_DURATION_SEC=5`，首句额外 1.6s 校准 |
| Faster-Whisper STT | 1~2秒 | tiny 模型已是最小，到极限 |
| Qwen 翻译 | 2~4秒 | 0.5B float32 CPU，48 tokens × 70ms |
| **合计** | **6~11秒** | |

#### 延迟构成分析

```
VAD 积累 (3~5s) → STT (1~2s) → Qwen 翻译 (2~4s) → UI 显示
```

三个阶段完全串行。最大的延迟来源：
1. **音频积累**（40%）：VAD 等待停顿或 max_duration 触发
2. **Qwen 推理**（35%）：受限于 DDR4 内存带宽（~25GB/s），每个 token 需读一遍 2GB 权重
3. **STT 识别**（15%）：tiny 模型已无优化空间
4. **其他**（10%）：队列通信、Python 开销

#### 可选优化（未执行）

| 改动 | 预期收益 | 代价 |
|------|----------|------|
| `MAX_CHUNK_DURATION_SEC` 5→3 | -1~2秒 | 更碎切片，可能截断句子 |
| `max_new_tokens` 48→32 | -1秒 | 长译文被截断 |
| 换 NLLB-600M 翻译模型 | -2~3秒 | 需重写翻译模块，失去纠错能力 |
| Qwen GGUF Q4 量化 + 1.5B | -1秒 + 质量质变 | 需要改推理框架 |

---

### 8.9 经验教训

1. **Prompt 复杂度必须匹配模型容量**：0.5B 模型无法同时做领域推理 + 纠错 + JSON 格式输出。每次尝试增加 Prompt 复杂度都导致质量下降。
2. **小模型把上下文当模板**：EN/ZH 对照格式、纯中文链、任何看起来像"待续写"的结构都会诱导模型复制而非翻译。
3. **0.5B 的最佳 Prompt 是零上下文**：去掉所有历史信息后，模型反而能稳定产出可用的翻译。
4. **JSON 输出对 0.5B 是奢侈品**：要求 0.5B 模型输出结构化 JSON 的成功率 <20%。让模型做它擅长的事（直译），代码负责结构化包装。
5. **延迟构成要量化分析再优化**：首句延迟 ≠ 翻译慢，可能是 VAD 校准（1.6s）+ 音频积累（5s）+ Qwen 冷启动（2-3s）的叠加。盲目优化翻译是浪费精力。
6. **PyTorch 首次推理有隐蔽的懒加载开销**：kernel 编译 + KV cache 分配在第一次 `generate()` 时触发，一条 dummy 预热即可化解。

---

## 第九阶段：v0.8 GGUF 量化 + 1.5B 模型升级

**日期**：2026-06-06

**文件**：`ai_processor.py`, `requirements.txt`, `config.json`, `config_manager.py`, `main.py`

### 背景

v0.7 在 0.5B float32 模型上通过简化 Prompt 获得了稳定输出，但翻译质量仍然受限于模型容量。dev_log 8.8 节的延迟分析指出了最高收益的优化方向：

| 改动 | 预期收益 | 代价 |
|------|----------|------|
| **Qwen GGUF Q4 量化 + 1.5B** | **-1秒 + 质量质变** | 需要改推理框架 |

核心洞察：当前 0.5B float32 推理受限于 DDR4 内存带宽（~25GB/s），每个 token 需读一遍 2GB 权重。1.5B Q4_K_M 量化后权重仅 ~1GB，每个 token 内存读取量减半，**速度反超 float32 0.5B**，同时模型容量提升 3 倍带来翻译质量质变。

### 9.1 推理框架切换：transformers → llama-cpp-python

#### 旧方案

```python
from transformers import AutoModelForCausalLM, AutoTokenizer
model = AutoModelForCausalLM.from_pretrained(path, dtype=torch.float32, device_map="cpu")
# 每次推理：手动词条化 + generate + 解码
```

#### 新方案

```python
from llama_cpp import Llama
self._llm = Llama(model_path=model_path, n_ctx=1024, n_threads=n_threads, verbose=False)
# 每次推理：一行 create_chat_completion 搞定
response = self._llm.create_chat_completion(
    messages=[...],
    max_tokens=48,
    temperature=0,
)
```

**优势**：
- `create_chat_completion()` 自动应用 Qwen chat template，无需手动拼接 tokenizer
- GGUF 格式原生支持多种量化（Q4_K_M 等），开箱即用
- 纯 C++ 推理核心，无 PyTorch 懒加载开销

### 9.2 模型获取：HuggingFace 替代 ModelScope

**旧方案**：`modelscope.snapshot_download()` — 国内网络不稳时超时/崩溃

**新方案**：
```
启动 → 检查 config.json local_model_path
  ├── 用户指定 → 直接使用
  └── 为空 → 检查 ~/.cache/ai_translator/models/
       ├── 已缓存 → 直接使用（零网络请求）
       └── 未缓存 → huggingface_hub 下载 (~1.2GB)
            repo: bartowski/Qwen2.5-1.5B-Instruct-GGUF
            mirror: export HF_ENDPOINT=https://hf-mirror.com
```

`huggingface_hub` 自带断点续传和缓存校验，比 ModelScope 更可靠。同时保留离线优先：缓存命中时零网络请求。

### 9.3 依赖变更

```diff
- transformers       # Qwen 0.5B float32 推理
- accelerate         # transformers device_map="cpu" 所需
- modelscope         # 模型下载
+ llama-cpp-python   # GGUF 推理引擎
+ huggingface_hub    # 模型下载（断点续传 + 镜像加速）
```

`torch` 保留 — Faster-Whisper 的 ctranslate2 仍然依赖它，且 `main.py` 需要在 PyQt5 之前导入 torch 解决 DLL 冲突。

### 9.4 配置新增

`config.json` 新增 `local_model_path` 字段：

```json
{
  "local_model_path": ""
}
```

留空 = 自动下载到 `~/.cache/ai_translator/models/`。用户可手动指定已下载的 GGUF 文件路径。

### 9.5 Prompt 简化

去掉了 `LOCAL_TRANSLATION_PROMPT` 常量和手动拼接流程。新实现直接在 `create_chat_completion` 的 messages 中指定 system prompt：

```python
messages=[
    {"role": "system", "content": "你是英中翻译器。只输出中文译文。不要解释。"},
    {"role": "user", "content": f"翻译: {current_text}"},
]
```

1.5B 模型对简单指令的响应远好于 0.5B，无需 few-shot 示例或复杂的输出约束。

### 9.6 预期性能对比

| 指标 | v0.7 (0.5B float32) | v0.8 (1.5B Q4_K_M) |
|------|---------------------|---------------------|
| 模型大小 | 2GB (float32) | ~1.2GB (Q4_K_M) |
| 每 token 内存读取 | ~2GB | ~1GB |
| 推理速度 | 70ms/token | ~35ms/token（预估） |
| 翻译延迟 | 2~4s (48 tokens) | 1~2s (48 tokens) |
| 翻译质量 | 可接受 | 良好 |

---

## 第十阶段：v0.9 Silero VAD + 异步精修 + 多行滚动

**日期**：2026-06-06 ~ 2026-06-07

**文件**：`audio_capture.py`, `ai_processor.py`, `stt_engine.py`, `ui_main.py`, `start.bat`, `requirements.txt`

### 背景

v0.8 的 Qwen GGUF 翻译引擎已经稳定，但实际使用中暴露了几个结构性问题：

1. **能量 VAD 误判**：基于 RMS 的静音检测对环境噪声敏感，校准窗口可能偏高，轻声说话检测不到
2. **Gemini 同步阻塞**：hybrid 模式下 Gemini 的网络请求在主流程中同步等待，导致音频队列积压
3. **字幕无状态覆盖**：每条新翻译直接覆盖旧字幕，无法追溯修正，Gemini 精修到达时已无可修正的对象
4. **Qwen 复读英文**：1.5B 模型偶发输出英文原文，Prompt 约束不够严格
5. **Whisper 幻觉**：静音段有时会输出 "Thank you" / "Subscribe" 等 YouTube 套话
6. **start.bat 每次重装**：缺乏依赖检测，已装好的环境也要走一遍安装流程

本轮改造目标：端到端架构升级，每个模块都要触及。

---

### 10.1 Silero VAD 替换能量检测

**文件**：`audio_capture.py`

#### 旧方案

能量 VAD：逐帧计算 RMS → 和校准的噪声基准比较 → 静音帧数累计 → 触发切片。

问题：
- 需要 50 帧校准，校准时若播放音频 → 噪声基准偏高 → 后续检测失聪
- RMS 阈值对轻声不敏感
- 纯 numpy 无法利用语音信号的时序特征

#### 新方案

Silero VAD（`snakers4/silero-vad`）：2MB 神经网络模型，通过 `torch.hub` 加载。

```python
# __init__ 中静态加载
self.vad_model, utils = torch.hub.load(
    repo_or_dir='snakers4/silero-vad',
    model='silero_vad',
    force_reload=False
)
self.VADIterator = utils[3]  # VADIterator 类
self.calibrated.set()         # 神经网络无需校准，直接放行
```

VADIterator 状态机：
```python
vad_iterator = self.VADIterator(model, threshold=0.5,
    sampling_rate=TARGET_SAMPLE_RATE, min_silence_duration_ms=300)

speech_dict = vad_iterator(tensor_frame, return_seconds=False)
if 'start' in speech_dict:
    is_speaking = True; speech_buffer.clear()
elif 'end' in speech_dict:
    is_speaking = False; self._emit_chunk(speech_buffer)
else:
    if is_speaking: speech_buffer.append(frame)  # 持续说话中
```

**优势**：
- 零校准，启动即用
- 300ms 内部静音缓冲，避免短暂停顿误切
- 对轻声和背景噪声鲁棒
- 删除所有能量 VAD 常量（`NOISE_FLOOR_*`, `ENERGY_SPEECH_RATIO` 等）和 `_rms()` 方法

**依赖**：新增 `torchaudio`（Silero VAD hubconf 顶层导入需要）

---

### 10.2 异步线程池 — 消除 Gemini 阻塞

**文件**：`ai_processor.py`

#### 问题

v0.8 hybrid 模式：Qwen 出草稿 → **同步等待 Gemini 返回** → 精修入队。Gemini 网络延迟 2-5s，主线程在此期间无法处理新音频块，队列积压。

#### 方案

引入 `concurrent.futures.ThreadPoolExecutor(max_workers=3)`：

```python
# 主线程：秒出草稿，不等待
draft_result = translator.translate(english_text, context_str)
result_queue.put({"history": current_history, "draft": draft_zh})

# Gemini 精修提交到后台线程池
def gemini_refine_task(en_text):
    result = engine.translate(en_text, en_hist_copy, zh_hist_copy)
    ...

api_executor.submit(gemini_refine_task, english_text)
# 主线程立即回去接下一个音频块
```

**同时删除**：音频队列的积压跳过逻辑（`dropped += 1` 那段）。不再丢弃任何切片。

---

### 10.3 全局历史状态机 + 追溯修正

**文件**：`ai_processor.py`

#### 旧方案

TranslationEngine 内部维护 `_en_history`/`_zh_history`（deque），`ai_worker_thread` 也有另一套历史。两套状态容易不一致。

每条翻译独立 `msg_id`，UI 端用 `OrderedDict` 按 ID 匹配更新。

#### 新方案

**TranslationEngine 无状态化**：历史由外部传入。

```python
class TranslationEngine:
    def translate(self, current_text: str, en_hist: list, zh_hist: list) -> dict:
        ...

# 调用方
engine.translate(en_text, en_hist_copy, zh_hist_copy)
```

**全局唯一真理**：`ai_worker_thread` 维护唯一的 `en_history`/`zh_history`（`list`），`history_lock` 保护所有读写。

**追溯修正**：Gemini 返回后，`zh_history[-1] = final` 直接覆写最后一条。调用方不知道也不关心是合并还是新句。

**消息格式简化**：

```python
# 旧: {"msg_id": ..., "final_translation": ..., "draft_translation": ...}
# 新: {"history": ["句1", "句2"], "draft": "当前草稿..."}
```

UI 每次收到完整的 history 数组 + 当前草稿，直接重新渲染。

---

### 10.4 HTML 多行滚动字幕

**文件**：`ui_main.py`

#### 旧方案

两个 QLabel（`final_label` + `draft_label`），逐条覆盖，旧消息消失。

#### 新方案

单个 `display_label`，HTML 富文本多行渲染：

```python
lines = []
for text in history:
    lines.append(f"<span style='color: #FFF; font-style: normal;'>{text}</span>")
if draft:
    lines.append(f"<span style='color: #DDD; font-style: italic;'>{draft}</span>")

html = "<br><br>".join(lines)
self.display_label.setText(html)
```

- 历史句：白色正体，`<br><br>` 双倍行距分隔
- 当前草稿：浅灰斜体，视觉上与已定型句子区分
- `AlignBottom | AlignHCenter`：底部对齐，新句往上顶

删除 `OrderedDict` 消息池和 `msg_id` 比对逻辑。

---

### 10.5 Qwen Prompt 强化 — 防止英文复读

**文件**：`ai_processor.py`

#### 问题

1.5B 模型偶发输出英文原文，即使 system prompt 已写明"只输出中文"。

#### 方案

三管齐下：

```python
# 1. System Prompt 升级
"你是专业的英中同声传译员。你的唯一任务是输出纯中文译文。"

# 2. User Prompt 结构化
"请将下面这段英文翻译成流畅的中文。绝对不允许输出任何英文原文，不要有任何解释。\n\n"
"【上文：...】\n\n"  # 上下文注入
"需要翻译的英文：\"{text}\"\n中文翻译结果："  # 明确引导

# 3. 参数调整
max_tokens=64, temperature=0.1  # 微升温打断机械复制倾向

# 4. 英文拦截阈值收紧
if _english_ratio(raw) > 0.4:  # 从 0.5 降到 0.4
    return {"final_translation": "", "draft_translation": ""}
```

---

### 10.6 Gemini Prompt 简化

**文件**：`ai_processor.py`

#### 旧 Prompt（26 行）

要求模型同时判断"是否合并上文"、"是否是独立新句"、"是否是纯残句"，输出 `context_rewritten` 和 `current_translation` 两个字段。

问题：1.5B 以下模型无法稳定处理多字段 JSON 判断。Gemini 虽强但增加不必要的心智负担。

#### 新 Prompt（14 行）

```python
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
```

只要求一个 `final_translation` 字段。合并逻辑交给调用方：有历史就覆写 `zh_history[-1]`，无历史就 `append`。

---

### 10.7 STT 幻觉过滤

**文件**：`stt_engine.py`

#### 问题

Whisper 在静音段有时会输出 "Thank you."、"Subscribe"、"Let's go." 等 YouTube 片头尾套话。

#### 方案

1. **`condition_on_previous_text=False`**：防止 Whisper 把上一句当模板重复输出
2. **幻觉黑名单**：

```python
hallucinations = ["Thank you.", "Thank you", "Subscribe",
                  "Thanks for watching.", "Let's go.", "bye", "you"]
if transcript.strip() in hallucinations:
    return ""
```

3. **`vad_filter=False`**：前端 Silero VAD 已做好切片，Whisper 只管识别

---

### 10.8 start.bat 依赖动态检测

**文件**：`start.bat`

在 `:install` 之前新增 `:check_deps` 标签：

```batch
:check_deps
echo Checking dependencies status...
"!PYTHON!" -c "import faster_whisper, llama_cpp, PyQt5, google.genai, torchaudio" >nul 2>&1
if !ERRORLEVEL! EQU 0 (
    echo [OK] Dependencies are already installed. Fast startup!
    goto :run
)
echo [INFO] Dependencies missing or incomplete. Starting installation...
```

Conda 路径和非 Conda 路径汇合后经过此检测 → 依赖齐全秒启动，缺失则走 `:install`。

新增 `:run` 标签统一出口。

---

### 10.9 依赖变更

**`requirements.txt`**：

```diff
- transformers
- accelerate
- modelscope
+ torchaudio
```

- `torchaudio`：Silero VAD 的 torch.hub 顶层导入依赖
- 移除 `transformers`/`accelerate`：v0.8 已切到 llama-cpp-python
- 移除 `modelscope`：v0.8 已切到 huggingface_hub

---

### 10.10 经验教训

1. **能量 VAD 本质缺陷**：基于 RMS 阈值的静音检测无法利用语音的频谱特征，校准窗口的时机决定了后续所有检测的准确性。神经 VAD（Silero）2MB 解决问题。
2. **异步是实时系统的必要条件**：任何可能阻塞的网络 I/O 必须离线程，否则队列积压的反馈回路会迅速恶化延迟。
3. **状态集中管理 > 分布式状态**：引擎内部状态 + 线程状态 + UI 状态 → 全局唯一 truth + lock 保护。否则追踪 bug 是噩梦。
4. **Prompt 复杂度要与模型容量匹配**：1.5B 模型只需"翻译"一个任务，多字段 JSON + 合并判断是过度设计。Gemini 虽然能处理，但简化 Prompt 提高成功率。
5. **多行滚动字幕比双行覆盖更符合直觉**：用户看到的是持续的对话历史，而不是闪烁替换的单行文字。history 数组一次性推送，渲染逻辑极其简单。
6. **Whisper 的 `condition_on_previous_text` 在流式场景是陷阱**：它会让模型在看到相似音频时输出上一句的内容。同声传译场景必须关闭。

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
Qwen2.5-1.5B GGUF (1-3s) ──→ 本地翻译 ──→ 悬浮窗
    │
    └──→ (可选) Gemini 精修
```

**默认纯本地运行**，无需 API Key，无需联网。

---

## 文件清单

```
Ai_Translator/
├── main.py              # 主入口，串联全链路
├── audio_capture.py     # WASAPI 内录 + Silero VAD 切片
├── ai_processor.py      # AI 翻译处理器 (Qwen + Gemini 异步)
├── stt_engine.py        # STT 语音识别 (Faster-Whisper + 幻觉过滤)
├── ui_main.py           # PyQt5 悬浮字幕窗 (HTML 多行滚动)
├── config_manager.py    # 配置管理（首次引导 + 持久化）
├── config.json          # 用户配置（API Key 等）
├── start.bat            # Windows 一键启动（依赖检测 + 自动安装）
├── requirements.txt     # Python 依赖
├── dev_log.md           # 本开发日志
├── README.md            # 项目说明
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
2. **机器学习依赖太重** → VAD 用纯 numpy，避免 DLL 地狱；后来用 Silero VAD（2MB）替换，性价比极高
3. **免费 API 有限流** → 默认纯本地模式，云端作可选补充
4. **国内网络不稳** → 缓存 + 离线优先 + 镜像加速，不依赖实时联网
5. **用户不想配置环境变量** → config.json + 交互式引导
6. **小模型容易"不听话"** → few-shot 示例 + 输出检测兜底；Prompt 复杂度必须匹配模型容量
7. **实时系统必须跳过积压** → 丢弃旧帧比累积延迟更符合用户体验（v0.9 改用异步线程池后不再需要丢弃）
8. **字幕 UI 状态管理** → 条件更新导致残留，必须全量设置清空旧数据（v0.9 改用 history 数组全量推送）
9. **GGUF 量化是 CPU 推理的最优解** → 1.5B Q4_K_M 比 0.5B float32 更快且质量更高。CPU 推理的瓶颈是内存带宽而非计算量，量化直接减少了每次 token 生成的内存读取量
10. **小模型的 Prompt 上下文问题会随容量增大而缓解** → 1.5B 可以稳定理解简单指令
11. **能量 VAD 本质缺陷** → 基于 RMS 阈值无法利用语音频谱特征，校准窗口时机决定所有后续准确性。Silero 2MB 神经网络解决
12. **异步是实时系统的必要条件** → 任何可能阻塞的网络 I/O 必须离线程，否则队列积压反馈回路迅速恶化延迟
13. **状态集中管理 > 分布式状态** → 全局唯一 truth + lock 保护，避免引擎/线程/UI 三套状态不一致
14. **多行滚动字幕比双行覆盖更符合直觉** → 用户看到的是持续对话历史，history 数组一次性推送，渲染逻辑极其简单
15. **Whisper 的 `condition_on_previous_text` 在流式场景是陷阱** → 同声传译必须关闭，否则模型看到相似音频会重复上一句
