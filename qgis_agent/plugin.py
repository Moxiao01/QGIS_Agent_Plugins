# -*- coding: utf-8 -*-
import os
from qgis.PyQt.QtWidgets import QAction, QToolBar
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtCore import Qt
from qgis.core import QgsApplication

from .ui.main_panel import QGISAgentPanel
from .core.agent import QGISAgent
from .core.config import AgentConfig


class QGISAgentPlugin:
    """QGIS Agent 插件主类"""

    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.panel = None
        self.agent = None
        self.actions = []
        self.toolbar = None

    def initGui(self):
        """初始化插件界面"""
        # 创建工具栏
        self.toolbar = self.iface.addToolBar("QGIS Agent")
        self.toolbar.setObjectName("QGISAgentToolBar")

        # 创建主面板动作
        icon_path = os.path.join(self.plugin_dir, "icon.png")
        action = QAction(
            QIcon(icon_path) if os.path.exists(icon_path) else QgsApplication.getThemeIcon("/processingAlgorithm.svg"),
            "QGIS Agent - 智能地理助手",
            self.iface.mainWindow()
        )
        action.triggered.connect(self.toggle_panel)
        action.setCheckable(True)

        self.toolbar.addAction(action)
        self.iface.addPluginToMenu("QGIS Agent", action)
        self.actions.append(action)

        # 初始化配置和Agent
        self.config = AgentConfig()
        self.agent = QGISAgent(self.iface, self.config)

        # 创建停靠面板
        self.panel = QGISAgentPanel(self.iface, self.agent)
        self.iface.addDockWidget(Qt.RightDockWidgetArea, self.panel)
        self.panel.hide()

    def toggle_panel(self):
        """切换面板显示/隐藏"""
        if self.panel.isVisible():
            self.panel.hide()
            self.actions[0].setChecked(False)
        else:
            self.panel.show()
            self.actions[0].setChecked(True)

    def unload(self):
        """卸载插件"""
        for action in self.actions:
            self.iface.removePluginMenu("QGIS Agent", action)
            self.iface.removeToolBarIcon(action)
        if self.toolbar:
            del self.toolbar
        if self.panel:
            self.iface.removeDockWidget(self.panel)
            self.panel.deleteLater()
