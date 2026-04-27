# -*- coding: utf-8 -*-
"""
QGIS Agent 设置对话框
"""
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox,
    QPushButton, QLabel, QGroupBox, QCheckBox,
    QFileDialog, QMessageBox, QTabWidget, QWidget,
)
from qgis.PyQt.QtCore import Qt
from ..core.config import AgentConfig


class SettingsDialog(QDialog):
    """LLM与Agent配置对话框"""

    def __init__(self, config: AgentConfig, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("QGIS Agent 设置")
        self.setMinimumWidth(480)
        self._build_ui()
        self._load_values()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        layout.addWidget(tabs)

        # ---- LLM 配置 ----
        llm_widget = QWidget()
        llm_form = QFormLayout(llm_widget)
        llm_form.setLabelAlignment(Qt.AlignRight)

        self.provider_combo = QComboBox()
        self.provider_combo.addItems(["openai_compatible", "anthropic", "ollama"])
        self.provider_combo.currentTextChanged.connect(self._on_provider_changed)
        llm_form.addRow("LLM 后端:", self.provider_combo)

        self.api_key_edit = QLineEdit()
        self.api_key_edit.setEchoMode(QLineEdit.Password)
        self.api_key_edit.setPlaceholderText("sk-...")
        llm_form.addRow("API Key:", self.api_key_edit)

        self.api_base_edit = QLineEdit()
        self.api_base_edit.setPlaceholderText("https://api.openai.com/v1")
        llm_form.addRow("API Base URL:", self.api_base_edit)

        self.model_edit = QLineEdit()
        self.model_edit.setPlaceholderText("gpt-4o / deepseek-chat / qwen-plus ...")
        llm_form.addRow("模型名称:", self.model_edit)

        self.temperature_spin = QDoubleSpinBox()
        self.temperature_spin.setRange(0.0, 2.0)
        self.temperature_spin.setSingleStep(0.05)
        llm_form.addRow("Temperature:", self.temperature_spin)

        self.max_tokens_spin = QSpinBox()
        self.max_tokens_spin.setRange(512, 32768)
        self.max_tokens_spin.setSingleStep(512)
        llm_form.addRow("Max Tokens:", self.max_tokens_spin)

        # 快速配置提示
        hint = QLabel(
            "<b>常用配置示例：</b><br>"
            "• DeepSeek: base=https://api.deepseek.com/v1, model=deepseek-chat<br>"
            "• 通义千问: base=https://dashscope.aliyuncs.com/compatible-mode/v1, model=qwen-plus<br>"
            "• Ollama本地: provider=ollama, base=http://localhost:11434, model=llama3<br>"
            "• Anthropic: provider=anthropic, model=claude-sonnet-4-5"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #666; font-size: 10px; background:#f9f9f9; padding:6px; border-radius:4px;")
        llm_form.addRow("", hint)

        tabs.addTab(llm_widget, "LLM 配置")

        # ---- Agent 配置 ----
        agent_widget = QWidget()
        agent_form = QFormLayout(agent_widget)
        agent_form.setLabelAlignment(Qt.AlignRight)

        self.max_iter_spin = QSpinBox()
        self.max_iter_spin.setRange(1, 50)
        agent_form.addRow("最大推理轮次:", self.max_iter_spin)

        self.auto_exec_check = QCheckBox("自动执行代码（不询问）")
        agent_form.addRow("代码执行:", self.auto_exec_check)

        self.memory_check = QCheckBox("保留对话历史")
        agent_form.addRow("记忆:", self.memory_check)

        self.max_history_spin = QSpinBox()
        self.max_history_spin.setRange(5, 100)
        agent_form.addRow("最大历史条数:", self.max_history_spin)

        self.crs_edit = QLineEdit()
        self.crs_edit.setPlaceholderText("EPSG:4326")
        agent_form.addRow("默认坐标系:", self.crs_edit)

        output_row = QHBoxLayout()
        self.output_dir_edit = QLineEdit()
        btn_browse = QPushButton("浏览...")
        btn_browse.setMaximumWidth(60)
        btn_browse.clicked.connect(self._browse_output)
        output_row.addWidget(self.output_dir_edit)
        output_row.addWidget(btn_browse)
        agent_form.addRow("输出目录:", output_row)

        tabs.addTab(agent_widget, "Agent 配置")

        # ---- 按钮 ----
        btn_row = QHBoxLayout()
        btn_test = QPushButton("🔌 测试连接")
        btn_test.clicked.connect(self._test_connection)
        btn_ok = QPushButton("保存")
        btn_ok.setDefault(True)
        btn_ok.clicked.connect(self._save_and_close)
        btn_cancel = QPushButton("取消")
        btn_cancel.clicked.connect(self.reject)

        btn_row.addWidget(btn_test)
        btn_row.addStretch()
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_ok)
        layout.addLayout(btn_row)

    def _load_values(self):
        idx = self.provider_combo.findText(self.config.llm_provider)
        self.provider_combo.setCurrentIndex(max(0, idx))
        self.api_key_edit.setText(self.config.llm_api_key)
        self.api_base_edit.setText(self.config.llm_api_base)
        self.model_edit.setText(self.config.llm_model)
        self.temperature_spin.setValue(self.config.llm_temperature)
        self.max_tokens_spin.setValue(self.config.llm_max_tokens)
        self.max_iter_spin.setValue(self.config.max_iterations)
        self.auto_exec_check.setChecked(self.config.enable_auto_execute)
        self.memory_check.setChecked(self.config.enable_memory)
        self.max_history_spin.setValue(self.config.max_history)
        self.crs_edit.setText(self.config.default_crs)
        self.output_dir_edit.setText(self.config.output_dir)

    def _on_provider_changed(self, provider: str):
        is_ollama = (provider == "ollama")
        self.api_key_edit.setEnabled(not is_ollama)
        if is_ollama:
            self.api_base_edit.setPlaceholderText("http://localhost:11434")
        else:
            self.api_base_edit.setPlaceholderText("https://api.openai.com/v1")

    def _browse_output(self):
        d = QFileDialog.getExistingDirectory(self, "选择输出目录", self.output_dir_edit.text())
        if d:
            self.output_dir_edit.setText(d)

    def _test_connection(self):
        self._apply_values()
        from ..core.llm_client import LLMClient
        client = LLMClient(self.config)
        try:
            result = client.chat([{"role": "user", "content": "回复'连接成功'"}],
                                  system="你是测试助手，只回复'连接成功'。")
            QMessageBox.information(self, "连接测试", f"✅ 连接成功！\n模型回复: {result.get('content', '')[:100]}")
        except Exception as e:
            QMessageBox.critical(self, "连接失败", f"❌ 错误: {str(e)}")

    def _apply_values(self):
        self.config.llm_provider = self.provider_combo.currentText()
        self.config.llm_api_key = self.api_key_edit.text().strip()
        self.config.llm_api_base = self.api_base_edit.text().strip()
        self.config.llm_model = self.model_edit.text().strip()
        self.config.llm_temperature = self.temperature_spin.value()
        self.config.llm_max_tokens = self.max_tokens_spin.value()
        self.config.max_iterations = self.max_iter_spin.value()
        self.config.enable_auto_execute = self.auto_exec_check.isChecked()
        self.config.enable_memory = self.memory_check.isChecked()
        self.config.max_history = self.max_history_spin.value()
        self.config.default_crs = self.crs_edit.text().strip()
        self.config.output_dir = self.output_dir_edit.text().strip()

    def _save_and_close(self):
        self._apply_values()
        self.config.save()
        self.accept()
