# -*- coding: utf-8 -*-
"""
QGIS Agent Plugin
智能地理分析助手 - 支持自然语言驱动的QGIS操作
"""

def classFactory(iface):
    from .plugin import QGISAgentPlugin
    return QGISAgentPlugin(iface)
