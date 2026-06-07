"""
config_manager.py — 用户配置管理模块

功能：
- 首次运行时交互式引导用户输入 API Key / 偏好设置
- 配置持久化到本地 config.json
- 后续启动自动加载，无需重复配置
- 环境变量优先级高于配置文件（方便高级用户）
"""

import json
import os
import sys

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

DEFAULT_CONFIG = {
    "gemini_api_key": "",
    "stt_provider": "mock",       # "mock" | "deepgram" | "azure"
    "stt_api_key": "",
    "ui_font_size": 18,
    "ui_opacity": 0.85,
    "setup_completed": False,     # 是否已完成首次引导
    "local_model_path": "",       # GGUF 模型路径，留空则自动下载
}


def _load_config() -> dict:
    """从本地配置文件加载，不存在则返回默认配置"""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
                # 合并默认配置（兼容新增字段）
                cfg = DEFAULT_CONFIG.copy()
                cfg.update(saved)
                return cfg
        except (json.JSONDecodeError, OSError):
            pass
    return DEFAULT_CONFIG.copy()


def _save_config(cfg: dict) -> None:
    """保存配置到本地 JSON 文件"""
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def _run_setup_wizard() -> dict:
    """交互式首次设置向导：一次性配置 Gemini + Deepgram Key"""
    cfg = _load_config()

    print("\n" + "=" * 60)
    print("  欢迎使用 AI 同声传译助手！")
    print("=" * 60)
    print()
    print("  首次运行需要配置以下服务（全部可选，回车跳过用 Mock 模式）")
    print()

    # --- 1. Gemini API Key ---
    print("─" * 60)
    print("  [1/2] Gemini 翻译 API Key")
    print("  获取: https://aistudio.google.com/apikey")
    print("  作用: 将英文翻译为中文")
    print()
    key = input("  Gemini API Key (回车跳过): ").strip()
    if key:
        cfg["gemini_api_key"] = key
    print()

    # --- 2. Deepgram API Key ---
    print("─" * 60)
    print("  [2/2] Deepgram 语音识别 API Key")
    print("  获取: https://console.deepgram.com (免费 $200 额度)")
    print("  作用: 将英文语音转为文字（不配则用模拟数据）")
    print()
    dg_key = input("  Deepgram API Key (回车跳过): ").strip()
    if dg_key:
        cfg["stt_provider"] = "deepgram"
        cfg["stt_api_key"] = dg_key
    print()

    # --- 完成 ---
    cfg["setup_completed"] = True
    _save_config(cfg)

    print("=" * 60)
    parts = []
    if cfg["gemini_api_key"]:
        parts.append("Gemini 翻译")
    if cfg["stt_api_key"]:
        parts.append("Deepgram 语音识别")
    if parts:
        print(f"  配置完成: {', '.join(parts)}")
    else:
        print("  配置完成: Mock 模式（功能演示用）")
    print("  之后可通过 python config_manager.py 重新配置")
    print("=" * 60)
    print()

    return cfg


def get_api_key() -> str:
    """获取 Gemini API Key

    优先级：环境变量 > 配置文件 > 交互输入
    """
    # 1. 环境变量（最高优先级）
    env_key = os.environ.get("GEMINI_API_KEY", "")
    if env_key:
        return env_key

    # 2. 配置文件已有 Key
    cfg = _load_config()
    saved_key = cfg.get("gemini_api_key", "")
    if saved_key:
        return saved_key

    # 3. 已完成首次引导但用户跳过了 → 静默返回空，不再打扰
    if cfg.get("setup_completed", False):
        return ""

    # 4. 首次运行：显示完整设置向导
    cfg = _run_setup_wizard()
    return cfg.get("gemini_api_key", "")


def get_stt_config() -> dict:
    """获取 STT 配置

    优先级：环境变量 > 配置文件 > 默认 mock

    返回
    ----
    dict  {"provider": str, "api_key": str}
    """
    cfg = _load_config()
    provider = cfg.get("stt_provider", "mock")
    api_key = cfg.get("stt_api_key", "")

    # 环境变量覆盖
    dg_key = os.environ.get("DEEPGRAM_API_KEY", "")
    if dg_key:
        provider = "deepgram"
        api_key = dg_key

    return {"provider": provider, "api_key": api_key}


def get_config() -> dict:
    """获取完整配置字典"""
    cfg = _load_config()
    # 环境变量覆盖
    env_key = os.environ.get("GEMINI_API_KEY", "")
    if env_key:
        cfg["gemini_api_key"] = env_key
    return cfg


def update_config(**kwargs) -> None:
    """更新配置项并保存"""
    cfg = _load_config()
    cfg.update(kwargs)
    _save_config(cfg)


def reset_config() -> None:
    """删除配置文件，恢复初始状态"""
    if os.path.exists(CONFIG_FILE):
        os.remove(CONFIG_FILE)
    print("  配置已重置。下次运行将重新引导设置。")


# ======================== 测试入口 ========================

if __name__ == "__main__":
    import sys
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    print("当前配置:")
    cfg = get_config()
    for k, v in cfg.items():
        if "key" in k.lower() and v:
            print(f"  {k}: {v[:8]}...{v[-4:]}")
        else:
            print(f"  {k}: {v}")

    print(f"\n配置文件位置: {CONFIG_FILE}")
