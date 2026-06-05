"""
ui_main.py — AI 同声传译助手桌面悬浮字幕窗

功能：
- 全透明背景，仅显示文字
- 文字居中，上下边距 10%、左右边距 20%
- 白色文字 + 黑色描边，浅深背景均可读
- 拖动任意位置可移动，右键锁定穿透 / 关闭
- DataFetcherThread 独立线程，pyqtSignal 安全更新 UI

依赖：PyQt5
"""

import sys
import queue
import time
import threading

from PyQt5.QtWidgets import (
    QApplication, QWidget, QLabel, QVBoxLayout, QMenu,
    QGraphicsDropShadowEffect,
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QPoint
from PyQt5.QtGui import QFont, QCursor, QColor


# ======================== 数据拉取线程 ========================

class DataFetcherThread(QThread):
    translation_ready = pyqtSignal(dict)

    def __init__(self, result_queue: queue.Queue, parent=None):
        super().__init__(parent)
        self._queue = result_queue
        self._running = True

    def run(self):
        while self._running:
            try:
                data = self._queue.get(timeout=0.3)
            except queue.Empty:
                continue
            self.translation_ready.emit(data)

    def stop(self):
        self._running = False


# ======================== 悬浮字幕主窗口 ========================

class SubtitleOverlay(QWidget):
    """全透明悬浮字幕窗 — 居中文字 + 描边，可拖动"""

    def __init__(self, result_queue: queue.Queue = None, parent=None):
        super().__init__(parent)
        self._queue = result_queue or queue.Queue()
        self._fetcher: DataFetcherThread | None = None
        self._drag_pos: QPoint | None = None
        self._locked = False

        self._setup_window()
        self._setup_ui()
        self._start_fetcher()

    # ---------- 窗口属性 ----------

    def _setup_window(self):
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        # 背景: 全透明（空样式表）
        self.setStyleSheet("background: transparent;")

        self._win_w, self._win_h = 1200, 260
        self.resize(self._win_w, self._win_h)

        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            x = (geo.width() - self._win_w) // 2
            y = geo.height() - self._win_h - 20
            self.move(x, y)

    # ---------- 拖动 ----------

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and not self._locked:
            self._drag_pos = event.globalPos() - self.frameGeometry().topLeft()
            self.setCursor(QCursor(Qt.ClosedHandCursor))

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton and self._drag_pos is not None:
            self.move(event.globalPos() - self._drag_pos)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        self.setCursor(QCursor(Qt.ArrowCursor))

    # ---------- 右键菜单 ----------

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background: rgba(30,30,30,230); color: #FFF;
                border: 1px solid rgba(255,255,255,20);
                padding: 4px; font-size: 13px; border-radius: 6px;
            }
            QMenu::item { padding: 6px 26px; }
            QMenu::item:selected { background: rgba(255,255,255,20); border-radius: 3px; }
        """)
        menu.addAction("解锁拖动" if self._locked else "锁定（穿透点击）",
                       self._toggle_lock)
        menu.addSeparator()
        menu.addAction("关闭", self.close)
        menu.exec_(event.globalPos())

    def _toggle_lock(self):
        self._locked = not self._locked
        self.setAttribute(Qt.WA_TransparentForMouseEvents, self._locked)

    # ---------- UI 布局 ----------

    def _setup_ui(self):
        # 百分比边距: 左右 20%, 上下 10%
        margin_h = int(self._win_w * 0.20)
        margin_v = int(self._win_h * 0.10)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(margin_h, margin_v, margin_h, margin_v)
        layout.setSpacing(8)

        # 公共文字样式: 白色 + 黑色 glow 描边
        outline = """
            color: #FFFFFF;
            background: transparent;
        """

        # —— final_translation ——
        self.final_label = QLabel()
        self.final_label.setFont(QFont("Microsoft YaHei", 18, QFont.Bold))
        self.final_label.setStyleSheet(outline)
        self.final_label.setWordWrap(True)
        self.final_label.setAlignment(Qt.AlignCenter)

        # —— draft_translation ——
        self.draft_label = QLabel()
        self.draft_label.setFont(QFont("Microsoft YaHei", 13))
        self.draft_label.setStyleSheet(outline + "font-style: italic;")
        self.draft_label.setWordWrap(True)
        self.draft_label.setAlignment(Qt.AlignCenter)

        # 文字描边效果（白字黑边，任意背景可读）
        for label in (self.final_label, self.draft_label):
            glow = QGraphicsDropShadowEffect()
            glow.setBlurRadius(5)
            glow.setOffset(0, 0)
            glow.setColor(QColor(0, 0, 0, 220))
            label.setGraphicsEffect(glow)

        # 初始占位文字，让窗口可见
        self.final_label.setText("等待语音输入...")
        self.draft_label.setText("")

        layout.addStretch()
        layout.addWidget(self.final_label)
        layout.addWidget(self.draft_label)
        layout.addStretch()

    # ---------- 线程通信 ----------

    def _start_fetcher(self):
        self._fetcher = DataFetcherThread(self._queue, parent=self)
        self._fetcher.translation_ready.connect(self.update_labels)
        self._fetcher.start()

    def update_labels(self, data: dict):
        final = data.get("final_translation", "")
        draft = data.get("draft_translation", "")

        # 始终同步设置两个 label，空串即清空，避免旧文字残留重叠
        if final and not final.startswith("["):
            self.final_label.setText(final)
        else:
            self.final_label.setText("")

        if draft and not draft.startswith("["):
            self.draft_label.setText(draft)
        else:
            self.draft_label.setText("")

    # ---------- 生命周期 ----------

    def closeEvent(self, event):
        if self._fetcher:
            self._fetcher.stop()
            self._fetcher.wait(2000)
        super().closeEvent(event)


# ======================== 独立测试入口 ========================

if __name__ == "__main__":
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    print("=" * 60)
    print("  AI 同声传译助手 — 悬浮字幕窗预览")
    print("  全透明背景 / 文字居中 / 拖动移动 / 右键锁定")
    print("=" * 60)

    app = QApplication(sys.argv)

    q: queue.Queue = queue.Queue()
    overlay = SubtitleOverlay(q)
    overlay.show()

    # 显示一条静态示例，验证窗口样式
    q.put({
        "final_translation": "最终翻译", "draft_translation": "暂定翻译...",
        "source_text": "[示例]", "timestamp": time.time(),
    })
    print("\n  >>> 字幕窗已启动，观察屏幕下方...\n")

    try:
        sys.exit(app.exec_())
    except KeyboardInterrupt:
        overlay.close()
        print("\n  已退出。")
