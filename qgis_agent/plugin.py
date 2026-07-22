# -*- coding: utf-8 -*-
"""QGIS plugin lifecycle integration."""

import os

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction
from qgis.core import Qgis, QgsApplication

from .core.agent import QGISAgent
from .core.config import AgentConfig
from .ui.main_panel import QGISAgentPanel


class QGISAgentPlugin:
    """QGIS Agent plugin entry point."""

    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.panel = None
        self.agent = None
        self.config = None
        self.actions = []
        self.toolbar = None

    def initGui(self):
        self.toolbar = self.iface.addToolBar("QGIS Agent")
        self.toolbar.setObjectName("QGISAgentToolBar")

        icon_path = os.path.join(self.plugin_dir, "icon.svg")
        icon = QIcon(icon_path) if os.path.exists(icon_path) else QgsApplication.getThemeIcon("/processingAlgorithm.svg")
        action = QAction(icon, "QGIS Agent - 智能地理助手", self.iface.mainWindow())
        action.setObjectName("QGISAgentAction")
        action.setCheckable(True)
        action.triggered.connect(self.toggle_panel)
        self.toolbar.addAction(action)
        self.iface.addPluginToMenu("QGIS Agent", action)
        self.actions.append(action)

        self.config = AgentConfig()
        self.agent = QGISAgent(self.iface, self.config)
        self.panel = QGISAgentPanel(self.iface, self.agent)
        self.panel.visibilityChanged.connect(action.setChecked)
        self.iface.addDockWidget(Qt.RightDockWidgetArea, self.panel)
        self.panel.hide()

        if self.config.load_error:
            self.iface.messageBar().pushMessage(
                "QGIS Agent",
                f"配置文件读取失败，已使用默认值: {self.config.load_error}",
                level=Qgis.Warning,
                duration=8,
            )

    def toggle_panel(self, checked=False):
        if not self.panel:
            return
        self.panel.setVisible(not self.panel.isVisible())
        if self.panel.isVisible():
            self.panel.raise_()
            self.panel.input_box.setFocus()

    def unload(self):
        for action in self.actions:
            self.iface.removePluginMenu("QGIS Agent", action)
            if self.toolbar:
                self.toolbar.removeAction(action)
            action.deleteLater()
        self.actions.clear()

        if self.panel:
            self.iface.removeDockWidget(self.panel)
            self.panel.deleteLater()
            self.panel = None
        if self.toolbar:
            self.iface.mainWindow().removeToolBar(self.toolbar)
            self.toolbar.deleteLater()
            self.toolbar = None
        self.agent = None
        self.config = None