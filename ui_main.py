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
        margin_h = int(self._win_w * 0.20)
        margin_v = int(self._win_h * 0.10)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(margin_h, margin_v, margin_h, margin_v)
        layout.setSpacing(8)

        # 统一使用一个富文本 Label 展示滚动历史
        self.display_label = QLabel()
        self.display_label.setFont(QFont("Microsoft YaHei", 16, QFont.Bold))
        self.display_label.setStyleSheet("color: transparent; background: transparent;")
        self.display_label.setWordWrap(True)
        self.display_label.setAlignment(Qt.AlignBottom | Qt.AlignHCenter)

        glow = QGraphicsDropShadowEffect()
        glow.setBlurRadius(5)
        glow.setOffset(0, 0)
        glow.setColor(QColor(0, 0, 0, 230))
        self.display_label.setGraphicsEffect(glow)

        self.display_label.setText("<span style='color: white;'>等待语音输入...</span>")

        layout.addStretch()
        layout.addWidget(self.display_label)

    # ---------- 线程通信 ----------

    def _start_fetcher(self):
        self._fetcher = DataFetcherThread(self._queue, parent=self)
        self._fetcher.translation_ready.connect(self.update_labels)
        self._fetcher.start()

    def update_labels(self, data: dict):
        history = data.get("history", [])
        draft = data.get("draft", "")

        lines = []
        # 1. 渲染历史记录（纯白正体字）
        for text in history:
            lines.append(f"<span style='color: #FFFFFF; font-style: normal;'>{text}</span>")

        # 2. 渲染草稿（浅灰斜体字）
        if draft:
            lines.append(f"<span style='color: #DDDDDD; font-style: italic; font-weight: normal;'>{draft}</span>")

        # 将多行文本用换行符连接
        html_text = "<br><br>".join(lines)
        self.display_label.setText(html_text)

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

    # 显示一条静态示例验证窗口样式（新格式）
    q.put({
        "history": ["如果我们查看", "这些销售数据"],
        "draft": "原因在于..."
    })
    print("\n  >>> 字幕窗已启动，观察屏幕下方...\n")

    try:
        sys.exit(app.exec_())
    except KeyboardInterrupt:
        overlay.close()
        print("\n  已退出。")
