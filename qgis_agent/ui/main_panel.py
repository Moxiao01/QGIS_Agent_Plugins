# -*- coding: utf-8 -*-
"""
QGIS Agent 主面板
停靠式对话界面，显示推理过程和工具调用状态
"""
import json
from qgis.PyQt.QtWidgets import (
    QDockWidget, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QTextEdit, QLineEdit, QLabel,
    QSplitter, QScrollArea, QFrame, QMessageBox,
    QProgressBar, QTabWidget, QPlainTextEdit,
)
from qgis.PyQt.QtCore import Qt, QThread, pyqtSignal, QObject
from qgis.PyQt.QtGui import QFont, QColor, QTextCursor

from ..core.agent import QGISAgent
from ..core.config import AgentConfig
from .settings_dialog import SettingsDialog


# ------------------------------------------------------------------ #
#  后台线程：避免UI阻塞                                               #
# ------------------------------------------------------------------ #
class AgentWorker(QObject):
    """在后台线程运行Agent推理"""
    finished = pyqtSignal(str)
    thinking = pyqtSignal(str)
    tool_called = pyqtSignal(str, str)   # name, args_json
    tool_result = pyqtSignal(str, str)   # name, result
    error = pyqtSignal(str)

    def __init__(self, agent: QGISAgent, user_input: str):
        super().__init__()
        self.agent = agent
        self.user_input = user_input

    def run(self):
        self.agent.on_thinking(lambda msg: self.thinking.emit(msg))
        self.agent.on_tool_call(lambda n, a: self.tool_called.emit(n, json.dumps(a, ensure_ascii=False)))
        self.agent.on_tool_result(lambda n, r: self.tool_result.emit(n, r))
        self.agent.on_response(lambda r: self.finished.emit(r))
        self.agent.on_error(lambda e: self.error.emit(e))
        self.agent.chat(self.user_input)


# ------------------------------------------------------------------ #
#  消息气泡组件                                                        #
# ------------------------------------------------------------------ #
class MessageBubble(QFrame):
    """单条消息气泡"""
    def __init__(self, role: str, content: str, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.StyledPanel)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)

        # 角色标签
        role_label = QLabel({"user": "🧑 用户", "assistant": "🤖 Agent",
                              "tool": "🔧 工具", "thinking": "💭 推理"}.get(role, role))
        role_label.setFont(QFont("Arial", 9, QFont.Bold))

        # 内容
        content_label = QTextEdit()
        content_label.setReadOnly(True)
        content_label.setPlainText(content)
        content_label.setMaximumHeight(min(200, 40 + content.count('\n') * 20))
        content_label.setFrameShape(QFrame.NoFrame)

        layout.addWidget(role_label)
        layout.addWidget(content_label)

        # 样式
        colors = {
            "user": "#e8f4fd",
            "assistant": "#f0f7ee",
            "tool": "#fff8e6",
            "thinking": "#f5f0ff",
        }
        self.setStyleSheet(f"QFrame {{ background: {colors.get(role, '#f5f5f5')}; border-radius: 8px; margin: 2px 0; }}")


# ------------------------------------------------------------------ #
#  主面板                                                              #
# ------------------------------------------------------------------ #
class QGISAgentPanel(QDockWidget):
    """QGIS Agent 主停靠面板"""

    def __init__(self, iface, agent: QGISAgent):
        super().__init__("🌍 QGIS Agent", iface.mainWindow())
        self.iface = iface
        self.agent = agent
        self.thread = None
        self.worker = None

        self._build_ui()
        self._connect_signals()

    # ------------------------------------------------------------------ #
    #  UI 构建                                                             #
    # ------------------------------------------------------------------ #
    def _build_ui(self):
        main_widget = QWidget()
        self.setWidget(main_widget)
        root = QVBoxLayout(main_widget)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(4)

        # 顶部工具栏
        toolbar = QHBoxLayout()
        self.btn_settings = QPushButton("⚙ 设置")
        self.btn_settings.setMaximumWidth(70)
        self.btn_clear = QPushButton("🗑 清空")
        self.btn_clear.setMaximumWidth(70)
        self.lbl_status = QLabel("就绪")
        self.lbl_status.setStyleSheet("color: #666; font-size: 11px;")
        toolbar.addWidget(self.btn_settings)
        toolbar.addWidget(self.btn_clear)
        toolbar.addStretch()
        toolbar.addWidget(self.lbl_status)
        root.addLayout(toolbar)

        # 选项卡：对话 / 日志
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)

        # --- 对话标签 ---
        chat_widget = QWidget()
        chat_layout = QVBoxLayout(chat_widget)
        chat_layout.setContentsMargins(0, 0, 0, 0)

        self.chat_scroll = QScrollArea()
        self.chat_scroll.setWidgetResizable(True)
        self.chat_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.chat_container = QWidget()
        self.chat_vbox = QVBoxLayout(self.chat_container)
        self.chat_vbox.setAlignment(Qt.AlignTop)
        self.chat_vbox.setSpacing(4)
        self.chat_scroll.setWidget(self.chat_container)
        chat_layout.addWidget(self.chat_scroll)

        # 进度条
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        chat_layout.addWidget(self.progress)

        self.tabs.addTab(chat_widget, "💬 对话")

        # --- 日志标签 ---
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFont(QFont("Consolas", 9))
        self.tabs.addTab(self.log_view, "📋 日志")

        # 快捷问题按钮区
        quick_label = QLabel("快捷操作:")
        quick_label.setStyleSheet("color: #888; font-size: 10px;")
        root.addWidget(quick_label)

        quick_btns = QHBoxLayout()
        for text in ["列出所有图层", "缓冲区分析", "计算面积", "导出报告"]:
            btn = QPushButton(text)
            btn.setStyleSheet("font-size: 10px; padding: 3px 6px;")
            btn.clicked.connect(lambda checked, t=text: self._set_input(t))
            quick_btns.addWidget(btn)
        root.addLayout(quick_btns)

        # 输入区
        input_row = QHBoxLayout()
        self.input_box = QLineEdit()
        self.input_box.setPlaceholderText("输入您的地理分析需求，按Enter发送...")
        self.input_box.setMinimumHeight(36)
        self.btn_send = QPushButton("发送 ▶")
        self.btn_send.setMinimumHeight(36)
        self.btn_send.setMinimumWidth(70)
        self.btn_send.setStyleSheet("background: #2c5f2e; color: white; border-radius: 4px; font-weight: bold;")
        input_row.addWidget(self.input_box)
        input_row.addWidget(self.btn_send)
        root.addLayout(input_row)

        self.setMinimumWidth(360)

    def _connect_signals(self):
        self.btn_send.clicked.connect(self._on_send)
        self.input_box.returnPressed.connect(self._on_send)
        self.btn_clear.clicked.connect(self._on_clear)
        self.btn_settings.clicked.connect(self._on_settings)

    # ------------------------------------------------------------------ #
    #  事件处理                                                            #
    # ------------------------------------------------------------------ #
    def _on_send(self):
        text = self.input_box.text().strip()
        if not text or self.thread is not None:
            return

        self.input_box.clear()
        self._add_bubble("user", text)
        self._set_busy(True)

        # 代码执行确认回调
        self.agent.set_confirm_execute(self._confirm_code_execute)

        # 启动后台线程
        self.thread = QThread()
        self.worker = AgentWorker(self.agent, text)
        self.worker.moveToThread(self.thread)

        self.thread.started.connect(self.worker.run)
        self.worker.finished.connect(self._on_agent_finished)
        self.worker.thinking.connect(self._on_thinking)
        self.worker.tool_called.connect(self._on_tool_called)
        self.worker.tool_result.connect(self._on_tool_result)
        self.worker.error.connect(self._on_agent_error)
        self.worker.finished.connect(self.thread.quit)
        self.worker.error.connect(self.thread.quit)
        self.thread.finished.connect(self._on_thread_done)

        self.thread.start()

    def _on_agent_finished(self, response: str):
        self._add_bubble("assistant", response)
        self._set_busy(False)

    def _on_agent_error(self, error: str):
        self._add_bubble("assistant", f"❌ {error}")
        self._set_busy(False)

    def _on_thinking(self, msg: str):
        self.lbl_status.setText(msg)
        self._log(f"[思考] {msg}")

    def _on_tool_called(self, name: str, args: str):
        self._add_bubble("tool", f"调用工具: {name}\n参数: {args}")
        self._log(f"[工具调用] {name}: {args}")
        self.tabs.setCurrentIndex(0)

    def _on_tool_result(self, name: str, result: str):
        short = result[:300] + "..." if len(result) > 300 else result
        self._add_bubble("tool", f"工具结果: {name}\n{short}")
        self._log(f"[工具结果] {name}:\n{result}")

    def _on_thread_done(self):
        self.thread = None
        self.worker = None
        self.lbl_status.setText("就绪")
        self._set_busy(False)

    def _on_clear(self):
        self.agent.clear_history()
        for i in reversed(range(self.chat_vbox.count())):
            w = self.chat_vbox.itemAt(i).widget()
            if w:
                w.deleteLater()
        self.log_view.clear()
        self._log("对话历史已清空")

    def _on_settings(self):
        dlg = SettingsDialog(self.agent.config, self)
        dlg.exec_()
        # 重新加载LLM客户端（配置可能已变更）
        from ..core.llm_client import LLMClient
        self.agent.llm = LLMClient(self.agent.config)

    def _set_input(self, text: str):
        self.input_box.setText(text)
        self.input_box.setFocus()

    def _confirm_code_execute(self, code: str) -> bool:
        """弹窗确认是否执行代码"""
        msg = QMessageBox(self)
        msg.setWindowTitle("确认执行代码")
        msg.setText("Agent 将执行以下 Python 代码：")
        msg.setDetailedText(code)
        msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        msg.setDefaultButton(QMessageBox.Yes)
        return msg.exec_() == QMessageBox.Yes

    # ------------------------------------------------------------------ #
    #  UI 辅助                                                             #
    # ------------------------------------------------------------------ #
    def _add_bubble(self, role: str, content: str):
        bubble = MessageBubble(role, content, self.chat_container)
        self.chat_vbox.addWidget(bubble)
        # 滚动到底部
        from qgis.PyQt.QtCore import QTimer
        QTimer.singleShot(50, lambda: self.chat_scroll.verticalScrollBar().setValue(
            self.chat_scroll.verticalScrollBar().maximum()))

    def _set_busy(self, busy: bool):
        self.btn_send.setEnabled(not busy)
        self.input_box.setEnabled(not busy)
        if busy:
            self.progress.show()
        else:
            self.progress.hide()
            self.lbl_status.setText("就绪")

    def _log(self, msg: str):
        self.log_view.appendPlainText(msg)
