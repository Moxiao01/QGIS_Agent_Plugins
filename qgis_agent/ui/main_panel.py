# -*- coding: utf-8 -*-
"""Dockable chat panel for QGIS Agent."""

import json
import threading

from qgis.PyQt.QtCore import QObject, QThread, QTimer, Qt, pyqtSignal
from qgis.PyQt.QtGui import QFont
from qgis.PyQt.QtWidgets import (
    QDockWidget,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..core.agent import QGISAgent
from .settings_dialog import SettingsDialog


class MainThreadRequest:
    """Blocking request used to marshal QGIS and dialog calls to the GUI thread."""

    def __init__(self, **payload):
        self.payload = payload
        self.done = threading.Event()
        self.result = None
        self.error = None
        self.cancelled = False


class AgentWorker(QObject):
    """Run network-bound LLM reasoning without calling QGIS from this thread."""

    finished = pyqtSignal(str)
    thinking = pyqtSignal(str)
    tool_called = pyqtSignal(str, str)
    tool_result = pyqtSignal(str, str)
    error = pyqtSignal(str)
    execute_requested = pyqtSignal(object)
    confirm_requested = pyqtSignal(object)

    def __init__(self, agent: QGISAgent, user_input: str):
        super().__init__()
        self.agent = agent
        self.user_input = user_input
        self._had_error = False

    def _execute_on_main_thread(self, name, args, fn):
        request = MainThreadRequest(name=name, args=args, fn=fn)
        self.execute_requested.emit(request)
        timeout = max(5, int(self.agent.config.tool_execution_timeout))
        if not request.done.wait(timeout):
            request.cancelled = True
            raise TimeoutError(f"工具 {name} 等待主线程超过 {timeout} 秒")
        if request.error:
            raise request.error
        return request.result

    def _confirm_on_main_thread(self, code):
        request = MainThreadRequest(code=code)
        self.confirm_requested.emit(request)
        timeout = max(5, int(self.agent.config.tool_execution_timeout))
        if not request.done.wait(timeout):
            request.cancelled = True
            return False
        return bool(request.result)

    def _emit_error(self, message):
        self._had_error = True
        self.error.emit(message)

    def run(self):
        self.agent.on_thinking(lambda message: self.thinking.emit(message))
        self.agent.on_tool_call(
            lambda name, args: self.tool_called.emit(
                name, json.dumps(args, ensure_ascii=False, default=str)
            )
        )
        self.agent.on_tool_result(lambda name, result: self.tool_result.emit(name, result))
        self.agent.on_response(None)
        self.agent.on_error(self._emit_error)
        self.agent.set_tool_executor(self._execute_on_main_thread)
        self.agent.set_confirm_execute(self._confirm_on_main_thread)
        try:
            response = self.agent.chat(self.user_input)
            if not self._had_error:
                self.finished.emit(response)
        except Exception as exc:
            self._emit_error(f"Agent 运行异常: {exc}")
        finally:
            self.agent.set_tool_executor(None)
            self.agent.set_confirm_execute(None)


class MessageBubble(QFrame):
    """Simple read-only chat message bubble."""

    def __init__(self, role: str, content: str, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.StyledPanel)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)

        role_label = QLabel({
            "user": "🧑 用户",
            "assistant": "🤖 Agent",
            "tool": "🔧 工具",
            "thinking": "💭 推理",
        }.get(role, role))
        role_label.setFont(QFont("Arial", 9, QFont.Bold))

        content_label = QTextEdit()
        content_label.setReadOnly(True)
        content_label.setPlainText(content)
        line_count = max(1, content.count("\n") + 1)
        content_label.setMaximumHeight(min(240, 38 + line_count * 20))
        content_label.setFrameShape(QFrame.NoFrame)

        layout.addWidget(role_label)
        layout.addWidget(content_label)
        colors = {
            "user": "#e8f4fd",
            "assistant": "#f0f7ee",
            "tool": "#fff8e6",
            "thinking": "#f5f0ff",
        }
        self.setStyleSheet(
            f"QFrame {{ background: {colors.get(role, '#f5f5f5')}; "
            "border-radius: 8px; margin: 2px 0; }}"
        )


class QGISAgentPanel(QDockWidget):
    """QGIS Agent main dock widget."""

    def __init__(self, iface, agent: QGISAgent):
        super().__init__("🌍 QGIS Agent", iface.mainWindow())
        self.iface = iface
        self.agent = agent
        self.thread = None
        self.worker = None
        self._last_logged_run_path = None
        self._build_ui()
        self._connect_signals()

    def _build_ui(self):
        main_widget = QWidget()
        self.setWidget(main_widget)
        root = QVBoxLayout(main_widget)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(4)

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

        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)

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

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        chat_layout.addWidget(self.progress)
        self.tabs.addTab(chat_widget, "💬 对话")

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFont(QFont("Consolas", 9))
        self.tabs.addTab(self.log_view, "📋 日志")

        quick_label = QLabel("快捷操作:")
        quick_label.setStyleSheet("color: #888; font-size: 10px;")
        root.addWidget(quick_label)
        quick_buttons = QHBoxLayout()
        for text in ["列出所有图层", "缓冲区分析", "计算面积", "导出报告"]:
            button = QPushButton(text)
            button.setStyleSheet("font-size: 10px; padding: 3px 6px;")
            button.clicked.connect(lambda checked=False, value=text: self._set_input(value))
            quick_buttons.addWidget(button)
        root.addLayout(quick_buttons)

        input_row = QHBoxLayout()
        self.input_box = QLineEdit()
        self.input_box.setPlaceholderText("输入地理分析需求，按 Enter 发送...")
        self.input_box.setMinimumHeight(36)
        self.btn_send = QPushButton("发送 ▶")
        self.btn_send.setMinimumHeight(36)
        self.btn_send.setMinimumWidth(70)
        self.btn_send.setStyleSheet(
            "background: #2c5f2e; color: white; border-radius: 4px; font-weight: bold;"
        )
        input_row.addWidget(self.input_box)
        input_row.addWidget(self.btn_send)
        root.addLayout(input_row)
        self.setMinimumWidth(380)

    def _connect_signals(self):
        self.btn_send.clicked.connect(self._on_send)
        self.input_box.returnPressed.connect(self._on_send)
        self.btn_clear.clicked.connect(self._on_clear)
        self.btn_settings.clicked.connect(self._on_settings)

    def _on_send(self):
        text = self.input_box.text().strip()
        if not text or self.thread is not None:
            return
        self.input_box.clear()
        self._add_bubble("user", text)
        self._set_busy(True)

        thread = QThread(self)
        worker = AgentWorker(self.agent, text)
        worker.moveToThread(thread)
        self.thread = thread
        self.worker = worker

        thread.started.connect(worker.run)
        worker.finished.connect(self._on_agent_finished)
        worker.thinking.connect(self._on_thinking)
        worker.tool_called.connect(self._on_tool_called)
        worker.tool_result.connect(self._on_tool_result)
        worker.error.connect(self._on_agent_error)
        worker.execute_requested.connect(self._execute_tool_request)
        worker.confirm_requested.connect(self._confirm_request)
        worker.finished.connect(thread.quit)
        worker.error.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(self._on_thread_done)
        thread.finished.connect(thread.deleteLater)
        thread.start()

    def _on_agent_finished(self, response: str):
        self._add_bubble("assistant", response)
        self._log_last_run_path()
        self._set_busy(False)

    def _on_agent_error(self, error: str):
        self._add_bubble("assistant", f"❌ {error}")
        self._log(f"[错误] {error}")
        self._log_last_run_path()
        self._set_busy(False)

    def _on_thinking(self, message: str):
        self.lbl_status.setText(message)
        self._log(f"[思考] {message}")

    def _on_tool_called(self, name: str, args: str):
        self._add_bubble("tool", f"调用工具: {name}\n参数: {args}")
        self._log(f"[工具调用] {name}: {args}")
        self.tabs.setCurrentIndex(0)

    def _on_tool_result(self, name: str, result: str):
        short = result[:300] + "..." if len(result) > 300 else result
        self._add_bubble("tool", f"工具结果: {name}\n{short}")
        self._log(f"[工具结果] {name}:\n{result}")

    def _execute_tool_request(self, request: MainThreadRequest):
        try:
            if request.cancelled:
                request.error = TimeoutError("工具请求在执行前已超时取消")
                return
            fn = request.payload["fn"]
            args = request.payload["args"]
            request.result = fn(**args)
        except Exception as exc:
            request.error = exc
        finally:
            request.done.set()

    def _confirm_request(self, request: MainThreadRequest):
        try:
            if request.cancelled:
                request.result = False
                return
            request.result = self._confirm_code_execute(str(request.payload.get("code", "")))
        except Exception as exc:
            request.error = exc
            request.result = False
        finally:
            request.done.set()

    def _on_thread_done(self):
        self.thread = None
        self.worker = None
        self.lbl_status.setText("就绪")
        self._set_busy(False)

    def _on_clear(self):
        if self.thread is not None:
            QMessageBox.information(self, "任务执行中", "请等待当前任务结束后再清空对话。")
            return
        self.agent.clear_history()
        for index in reversed(range(self.chat_vbox.count())):
            widget = self.chat_vbox.itemAt(index).widget()
            if widget:
                widget.deleteLater()
        self.log_view.clear()
        self._log("对话历史已清空")

    def _on_settings(self):
        if self.thread is not None:
            QMessageBox.information(self, "任务执行中", "请等待当前任务结束后再修改设置。")
            return
        dialog = SettingsDialog(self.agent.config, self)
        if dialog.exec_():
            from ..core.llm_client import LLMClient
            self.agent.llm = LLMClient(self.agent.config)
            self.agent.refresh_tool_policy()

    def _set_input(self, text: str):
        self.input_box.setText(text)
        self.input_box.setFocus()

    def _confirm_code_execute(self, code: str) -> bool:
        message = QMessageBox(self)
        message.setWindowTitle("确认执行 Python 代码")
        message.setIcon(QMessageBox.Warning)
        message.setText("Agent 请求执行自定义 Python 代码。代码拥有当前 QGIS 用户的全部权限。")
        message.setInformativeText("仅在你理解并信任下列代码时选择“是”。")
        message.setDetailedText(code)
        message.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        message.setDefaultButton(QMessageBox.No)
        return message.exec_() == QMessageBox.Yes

    def _add_bubble(self, role: str, content: str):
        bubble = MessageBubble(role, content, self.chat_container)
        self.chat_vbox.addWidget(bubble)
        QTimer.singleShot(
            50,
            lambda: self.chat_scroll.verticalScrollBar().setValue(
                self.chat_scroll.verticalScrollBar().maximum()
            ),
        )

    def _set_busy(self, busy: bool):
        self.btn_send.setEnabled(not busy)
        self.input_box.setEnabled(not busy)
        self.btn_settings.setEnabled(not busy)
        self.btn_clear.setEnabled(not busy)
        self.progress.setVisible(busy)
        if not busy:
            self.lbl_status.setText("就绪")

    def _log_last_run_path(self):
        path = self.agent.last_run_log_path
        if path and path != self._last_logged_run_path:
            self._log(f"[任务日志] {path}")
            self._last_logged_run_path = path

    def _log(self, message: str):
        self.log_view.appendPlainText(message)
