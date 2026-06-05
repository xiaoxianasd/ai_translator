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
9. [最终架构](#最终架构)
10. [文件清单](#文件清单)

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
| 翻译引擎 | Qwen2.5-0.5B + Gemini | 混合模式：本地秒出 + 云端精修 |
| 前端 UI | PyQt5 | 全透明悬浮窗 |
| 部署 | start.bat | 双击即用 |

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

## 最终架构

```
系统音频
    │
    ▼
WASAPI Loopback ──→ 能量 VAD 切片
    │
    ▼
Faster-Whisper tiny ──→ 英文文本
    │
    ├──→ Qwen2.5-0.5B (3-5s) ──→ 本地翻译草稿 ──→ 悬浮窗灰色
    │
    └──→ Gemini (2-5s) ──→ 云端精修 ──→ 悬浮窗白色
```

**零外部依赖**：所有模型本地运行，无需 API Key 也能工作。

---

## 文件清单

```
Ai_Translator/
├── main.py              # 主入口，串联全链路
├── audio_capture.py     # WASAPI 内录 + VAD 切片
├── ai_processor.py      # AI 翻译处理器
├── stt_engine.py        # STT 语音识别引擎
├── ui_main.py           # PyQt5 悬浮字幕窗
├── config_manager.py    # 配置管理（首次引导 + 持久化）
├── config.json          # 用户配置（API Key 等）
├── start.bat            # Windows 双击启动
├── dev_log.md           # 本开发日志
└── .gitignore           # Git 忽略规则
```

---

## 运行方式

```bash
# 双击即可
start.bat

# 或终端
F:\Anaconda3\python.exe main.py
```

首次运行自动引导配置 API Key（可选），之后无需任何操作。

---

## 经验教训

1. **Python 3.13 + PyTorch 兼容性差** → 改用 Anaconda 环境
2. **机器学习依赖太重** → VAD 用纯 numpy，避免 DLL 地狱
3. **免费 API 有限流** → 优先本地模型，云端作补充
4. **国内 HuggingFace 慢** → 首次下载需耐心，之后缓存
5. **用户不想配置环境变量** → config.json + 交互式引导
