# -*- coding: utf-8 -*-
"""
QGIS 空间分析工具集
封装常用地理处理操作为 Agent 可调用的工具
"""
import os
import json
from typing import Any, Dict, List, Optional

try:
    import processing
    from qgis.core import (
        QgsVectorLayer, QgsRasterLayer, QgsProject,
        QgsCoordinateReferenceSystem, QgsDistanceArea,
        QgsFeature, QgsGeometry, QgsField, QgsFields,
        QgsVectorFileWriter, QgsWkbTypes, QgsMapLayer,
    )
    from qgis.PyQt.QtCore import QVariant
    QGIS_AVAILABLE = True
except ImportError:
    QGIS_AVAILABLE = False


# ============================================================
# 工具结果封装
# ============================================================
class ToolResult:
    def __init__(self, success: bool, data: Any = None, message: str = ""):
        self.success = success
        self.data = data
        self.message = message

    def to_dict(self) -> Dict:
        return {"success": self.success, "data": self.data, "message": self.message}

    def __str__(self):
        if self.success:
            return f"✅ 成功: {self.message}" + (f"\n{json.dumps(self.data, ensure_ascii=False, indent=2)}" if self.data else "")
        return f"❌ 失败: {self.message}"


# ============================================================
# 数据加载工具
# ============================================================
class DataLoadTools:
    """数据加载与管理工具"""

    @staticmethod
    def load_vector(path: str, layer_name: str = "") -> ToolResult:
        """加载矢量图层"""
        if not QGIS_AVAILABLE:
            return ToolResult(False, message="QGIS 环境不可用")
        if not os.path.exists(path):
            return ToolResult(False, message=f"文件不存在: {path}")

        name = layer_name or os.path.splitext(os.path.basename(path))[0]
        layer = QgsVectorLayer(path, name, "ogr")
        if not layer.isValid():
            return ToolResult(False, message=f"无法加载图层: {path}")

        QgsProject.instance().addMapLayer(layer)
        return ToolResult(True,
            data={"layer_id": layer.id(), "name": name, "feature_count": layer.featureCount(),
                  "crs": layer.crs().authid(), "geometry_type": QgsWkbTypes.displayString(layer.wkbType())},
            message=f"已加载矢量图层: {name} ({layer.featureCount()} 要素)")

    @staticmethod
    def load_raster(path: str, layer_name: str = "") -> ToolResult:
        """加载栅格图层"""
        if not QGIS_AVAILABLE:
            return ToolResult(False, message="QGIS 环境不可用")
        if not os.path.exists(path):
            return ToolResult(False, message=f"文件不存在: {path}")

        name = layer_name or os.path.splitext(os.path.basename(path))[0]
        layer = QgsRasterLayer(path, name)
        if not layer.isValid():
            return ToolResult(False, message=f"无法加载栅格: {path}")

        QgsProject.instance().addMapLayer(layer)
        return ToolResult(True,
            data={"layer_id": layer.id(), "name": name, "width": layer.width(),
                  "height": layer.height(), "crs": layer.crs().authid()},
            message=f"已加载栅格图层: {name}")

    @staticmethod
    def load_wms(url: str, layer_name: str = "WMS图层") -> ToolResult:
        """加载WMS网络图层"""
        if not QGIS_AVAILABLE:
            return ToolResult(False, message="QGIS 环境不可用")
        uri = f"url={url}&format=image/png&crs=EPSG:4326"
        layer = QgsRasterLayer(uri, layer_name, "wms")
        if not layer.isValid():
            return ToolResult(False, message=f"无法连接WMS: {url}")
        QgsProject.instance().addMapLayer(layer)
        return ToolResult(True, data={"layer_id": layer.id()}, message=f"已加载WMS图层: {layer_name}")

    @staticmethod
    def list_layers() -> ToolResult:
        """列出当前项目所有图层"""
        if not QGIS_AVAILABLE:
            return ToolResult(False, message="QGIS 环境不可用")
        layers = []
        for lid, layer in QgsProject.instance().mapLayers().items():
            layers.append({
                "id": lid,
                "name": layer.name(),
                "type": "vector" if layer.type() == QgsMapLayer.VectorLayer else "raster",
                "crs": layer.crs().authid(),
                "visible": QgsProject.instance().layerTreeRoot().findLayer(lid).isVisible()
                    if QgsProject.instance().layerTreeRoot().findLayer(lid) else True,
            })
        return ToolResult(True, data={"layers": layers, "count": len(layers)},
                          message=f"当前项目共 {len(layers)} 个图层")

    @staticmethod
    def get_layer_info(layer_name: str) -> ToolResult:
        """获取图层详细信息"""
        if not QGIS_AVAILABLE:
            return ToolResult(False, message="QGIS 环境不可用")
        layer = None
        for lyr in QgsProject.instance().mapLayers().values():
            if lyr.name() == layer_name:
                layer = lyr
                break
        if not layer:
            return ToolResult(False, message=f"找不到图层: {layer_name}")

        info = {
            "name": layer.name(), "id": layer.id(),
            "crs": layer.crs().authid(),
            "extent": layer.extent().toString(),
        }
        if layer.type() == QgsMapLayer.VectorLayer:
            info["feature_count"] = layer.featureCount()
            info["fields"] = [{"name": f.name(), "type": f.typeName()} for f in layer.fields()]
            info["geometry_type"] = QgsWkbTypes.displayString(layer.wkbType())
        return ToolResult(True, data=info, message=f"图层信息: {layer_name}")


# ============================================================
# 空间分析工具
# ============================================================
class SpatialAnalysisTools:
    """空间分析工具集"""

    @staticmethod
    def buffer(layer_name: str, distance: float, output_path: str = "") -> ToolResult:
        """创建缓冲区"""
        if not QGIS_AVAILABLE:
            return ToolResult(False, message="QGIS 环境不可用")
        layer = _get_layer(layer_name)
        if not layer:
            return ToolResult(False, message=f"找不到图层: {layer_name}")

        output = output_path or "memory:"
        result = processing.run("native:buffer", {
            "INPUT": layer, "DISTANCE": distance, "SEGMENTS": 5,
            "END_CAP_STYLE": 0, "JOIN_STYLE": 0, "MITER_LIMIT": 2,
            "DISSOLVE": False, "OUTPUT": output
        })
        out_layer = result["OUTPUT"]
        if isinstance(out_layer, QgsVectorLayer):
            QgsProject.instance().addMapLayer(out_layer)
            name = out_layer.name()
        else:
            name = output
        return ToolResult(True, data={"output": str(out_layer)},
                          message=f"缓冲区分析完成，距离={distance}，输出: {name}")

    @staticmethod
    def clip(input_layer: str, overlay_layer: str, output_path: str = "") -> ToolResult:
        """裁剪图层"""
        if not QGIS_AVAILABLE:
            return ToolResult(False, message="QGIS 环境不可用")
        in_lyr = _get_layer(input_layer)
        ov_lyr = _get_layer(overlay_layer)
        if not in_lyr:
            return ToolResult(False, message=f"找不到输入图层: {input_layer}")
        if not ov_lyr:
            return ToolResult(False, message=f"找不到裁剪图层: {overlay_layer}")

        output = output_path or "memory:"
        result = processing.run("native:clip", {
            "INPUT": in_lyr, "OVERLAY": ov_lyr, "OUTPUT": output
        })
        out_layer = result["OUTPUT"]
        if isinstance(out_layer, QgsVectorLayer):
            QgsProject.instance().addMapLayer(out_layer)
        return ToolResult(True, data={"output": str(out_layer)},
                          message=f"裁剪完成: {input_layer} ∩ {overlay_layer}")

    @staticmethod
    def intersect(input_layer: str, overlay_layer: str, output_path: str = "") -> ToolResult:
        """空间叠加分析（交集）"""
        if not QGIS_AVAILABLE:
            return ToolResult(False, message="QGIS 环境不可用")
        in_lyr = _get_layer(input_layer)
        ov_lyr = _get_layer(overlay_layer)
        if not in_lyr or not ov_lyr:
            return ToolResult(False, message="找不到指定图层")

        output = output_path or "memory:"
        result = processing.run("native:intersection", {
            "INPUT": in_lyr, "OVERLAY": ov_lyr, "OUTPUT": output
        })
        out_layer = result["OUTPUT"]
        if isinstance(out_layer, QgsVectorLayer):
            QgsProject.instance().addMapLayer(out_layer)
        return ToolResult(True, data={"output": str(out_layer)}, message="叠加分析完成")

    @staticmethod
    def dissolve(layer_name: str, field: str = "", output_path: str = "") -> ToolResult:
        """融合图层"""
        if not QGIS_AVAILABLE:
            return ToolResult(False, message="QGIS 环境不可用")
        layer = _get_layer(layer_name)
        if not layer:
            return ToolResult(False, message=f"找不到图层: {layer_name}")

        output = output_path or "memory:"
        params = {"INPUT": layer, "OUTPUT": output}
        if field:
            params["FIELD"] = [field]
        result = processing.run("native:dissolve", params)
        out_layer = result["OUTPUT"]
        if isinstance(out_layer, QgsVectorLayer):
            QgsProject.instance().addMapLayer(out_layer)
        return ToolResult(True, data={"output": str(out_layer)}, message=f"融合完成: {layer_name}")

    @staticmethod
    def calculate_area(layer_name: str) -> ToolResult:
        """计算要素面积统计"""
        if not QGIS_AVAILABLE:
            return ToolResult(False, message="QGIS 环境不可用")
        layer = _get_layer(layer_name)
        if not layer:
            return ToolResult(False, message=f"找不到图层: {layer_name}")

        da = QgsDistanceArea()
        da.setSourceCrs(layer.crs(), QgsProject.instance().transformContext())
        da.setEllipsoid(QgsProject.instance().ellipsoid())

        areas = []
        for feat in layer.getFeatures():
            if feat.geometry():
                areas.append(da.measureArea(feat.geometry()))

        if not areas:
            return ToolResult(False, message="没有可计算的要素")

        total = sum(areas)
        return ToolResult(True, data={
            "count": len(areas),
            "total_m2": round(total, 2),
            "total_km2": round(total / 1e6, 4),
            "mean_m2": round(total / len(areas), 2),
            "min_m2": round(min(areas), 2),
            "max_m2": round(max(areas), 2),
        }, message=f"面积统计完成，共 {len(areas)} 个要素，总面积 {total/1e6:.4f} km²")

    @staticmethod
    def reproject(layer_name: str, target_crs: str, output_path: str = "") -> ToolResult:
        """重投影图层"""
        if not QGIS_AVAILABLE:
            return ToolResult(False, message="QGIS 环境不可用")
        layer = _get_layer(layer_name)
        if not layer:
            return ToolResult(False, message=f"找不到图层: {layer_name}")

        crs = QgsCoordinateReferenceSystem(target_crs)
        if not crs.isValid():
            return ToolResult(False, message=f"无效的坐标系: {target_crs}")

        output = output_path or "memory:"
        result = processing.run("native:reprojectlayer", {
            "INPUT": layer, "TARGET_CRS": crs, "OUTPUT": output
        })
        out_layer = result["OUTPUT"]
        if isinstance(out_layer, QgsVectorLayer):
            QgsProject.instance().addMapLayer(out_layer)
        return ToolResult(True, data={"output": str(out_layer), "crs": target_crs},
                          message=f"重投影完成: {layer_name} → {target_crs}")

    @staticmethod
    def spatial_join(input_layer: str, join_layer: str, predicate: str = "intersects",
                     output_path: str = "") -> ToolResult:
        """空间连接"""
        if not QGIS_AVAILABLE:
            return ToolResult(False, message="QGIS 环境不可用")
        in_lyr = _get_layer(input_layer)
        jn_lyr = _get_layer(join_layer)
        if not in_lyr or not jn_lyr:
            return ToolResult(False, message="找不到指定图层")

        predicate_map = {"intersects": 0, "contains": 1, "within": 5, "touches": 3}
        pred_val = predicate_map.get(predicate, 0)
        output = output_path or "memory:"
        result = processing.run("native:joinattributesbylocation", {
            "INPUT": in_lyr, "JOIN": jn_lyr, "PREDICATE": [pred_val],
            "METHOD": 0, "OUTPUT": output
        })
        out_layer = result["OUTPUT"]
        if isinstance(out_layer, QgsVectorLayer):
            QgsProject.instance().addMapLayer(out_layer)
        return ToolResult(True, data={"output": str(out_layer)}, message="空间连接完成")


# ============================================================
# 输出工具
# ============================================================
class OutputTools:
    """地图输出与报告生成工具"""

    @staticmethod
    def export_layer(layer_name: str, output_path: str, format: str = "GPKG") -> ToolResult:
        """导出图层到文件"""
        if not QGIS_AVAILABLE:
            return ToolResult(False, message="QGIS 环境不可用")
        layer = _get_layer(layer_name)
        if not layer:
            return ToolResult(False, message=f"找不到图层: {layer_name}")

        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = format
        error, msg, _, _ = QgsVectorFileWriter.writeAsVectorFormatV3(
            layer, output_path, QgsProject.instance().transformContext(), options
        )
        if error != QgsVectorFileWriter.NoError:
            return ToolResult(False, message=f"导出失败: {msg}")
        return ToolResult(True, data={"path": output_path},
                          message=f"图层已导出: {output_path}")

    @staticmethod
    def export_map_image(output_path: str, width: int = 1920, height: int = 1080,
                         dpi: int = 96) -> ToolResult:
        """导出当前地图为图片"""
        if not QGIS_AVAILABLE:
            return ToolResult(False, message="QGIS 环境不可用")
        try:
            from qgis.core import QgsMapRendererParallelJob, QgsMapSettings
            from qgis.PyQt.QtCore import QSize
            from qgis.PyQt.QtGui import QImage, QPainter

            settings = QgsMapSettings()
            settings.setLayers(list(QgsProject.instance().mapLayers().values()))
            settings.setOutputSize(QSize(width, height))
            settings.setOutputDpi(dpi)
            settings.setExtent(QgsProject.instance().viewSettings().fullExtent()
                               if hasattr(QgsProject.instance(), 'viewSettings') else
                               settings.fullExtent())

            image = QImage(QSize(width, height), QImage.Format_ARGB32_Premultiplied)
            image.fill(0xFFFFFFFF)
            painter = QPainter(image)
            job = QgsMapRendererParallelJob(settings)
            job.start()
            job.waitForFinished()
            painter.drawImage(0, 0, job.renderedImage())
            painter.end()
            image.save(output_path)
            return ToolResult(True, data={"path": output_path},
                              message=f"地图已导出: {output_path}")
        except Exception as e:
            return ToolResult(False, message=f"导出地图失败: {str(e)}")

    @staticmethod
    def generate_report(title: str, layer_name: str, output_path: str) -> ToolResult:
        """生成 HTML 分析报告"""
        if not QGIS_AVAILABLE:
            return ToolResult(False, message="QGIS 环境不可用")
        layer = _get_layer(layer_name)
        if not layer:
            return ToolResult(False, message=f"找不到图层: {layer_name}")

        rows = ""
        fields = [f.name() for f in layer.fields()]
        for feat in layer.getFeatures():
            attrs = feat.attributes()
            row_html = "".join(f"<td>{v}</td>" for v in attrs)
            rows += f"<tr>{row_html}</tr>\n"

        header_html = "".join(f"<th>{f}</th>" for f in fields)
        extent = layer.extent()

        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><title>{title}</title>
<style>
  body{{font-family:Arial,sans-serif;margin:2rem;color:#333}}
  h1{{color:#2c5f2e}} table{{border-collapse:collapse;width:100%}}
  th{{background:#2c5f2e;color:white;padding:8px}} td{{padding:6px;border:1px solid #ddd}}
  tr:nth-child(even){{background:#f5f5f5}} .info{{background:#e8f5e9;padding:1rem;border-radius:6px;margin:1rem 0}}
</style></head>
<body>
<h1>{title}</h1>
<div class="info">
  <b>图层名称:</b> {layer.name()}<br>
  <b>要素数量:</b> {layer.featureCount()}<br>
  <b>坐标系统:</b> {layer.crs().authid()}<br>
  <b>空间范围:</b> {extent.xMinimum():.4f}, {extent.yMinimum():.4f} → {extent.xMaximum():.4f}, {extent.yMaximum():.4f}
</div>
<h2>属性数据</h2>
<table><thead><tr>{header_html}</tr></thead><tbody>{rows}</tbody></table>
</body></html>"""

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html)
        return ToolResult(True, data={"path": output_path},
                          message=f"报告已生成: {output_path}")


# ============================================================
# 代码执行工具
# ============================================================
class CodeExecutionTools:
    """PyQGIS 代码执行工具"""

    @staticmethod
    def execute_python(code: str) -> ToolResult:
        """在QGIS环境中执行Python代码"""
        import io
        import sys
        from contextlib import redirect_stdout, redirect_stderr

        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()

        try:
            # 注入QGIS常用变量
            exec_globals = {
                "__builtins__": __builtins__,
            }
            if QGIS_AVAILABLE:
                exec_globals.update({
                    "iface": None,  # 由agent注入
                    "QgsProject": QgsProject,
                    "processing": processing if QGIS_AVAILABLE else None,
                })

            with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
                exec(code, exec_globals)

            stdout = stdout_buf.getvalue()
            stderr = stderr_buf.getvalue()
            return ToolResult(True,
                data={"stdout": stdout, "stderr": stderr},
                message=f"代码执行成功\n{stdout}" + (f"\n[stderr]: {stderr}" if stderr else ""))
        except Exception as e:
            return ToolResult(False,
                data={"error": str(e), "stderr": stderr_buf.getvalue()},
                message=f"代码执行失败: {str(e)}")

    @staticmethod
    def run_processing_algorithm(algorithm: str, params: Dict) -> ToolResult:
        """运行QGIS Processing算法"""
        if not QGIS_AVAILABLE:
            return ToolResult(False, message="QGIS 环境不可用")
        try:
            result = processing.run(algorithm, params)
            return ToolResult(True, data=result, message=f"算法 {algorithm} 执行成功")
        except Exception as e:
            return ToolResult(False, message=f"算法执行失败: {str(e)}")


# ============================================================
# 工具注册表
# ============================================================
ALL_TOOLS = {
    # 数据加载
    "load_vector": DataLoadTools.load_vector,
    "load_raster": DataLoadTools.load_raster,
    "load_wms": DataLoadTools.load_wms,
    "list_layers": DataLoadTools.list_layers,
    "get_layer_info": DataLoadTools.get_layer_info,
    # 空间分析
    "buffer": SpatialAnalysisTools.buffer,
    "clip": SpatialAnalysisTools.clip,
    "intersect": SpatialAnalysisTools.intersect,
    "dissolve": SpatialAnalysisTools.dissolve,
    "calculate_area": SpatialAnalysisTools.calculate_area,
    "reproject": SpatialAnalysisTools.reproject,
    "spatial_join": SpatialAnalysisTools.spatial_join,
    # 输出
    "export_layer": OutputTools.export_layer,
    "export_map_image": OutputTools.export_map_image,
    "generate_report": OutputTools.generate_report,
    # 代码执行
    "execute_python": CodeExecutionTools.execute_python,
    "run_processing_algorithm": CodeExecutionTools.run_processing_algorithm,
}


# LLM Function Calling 格式的工具定义
TOOL_SCHEMAS = [
    {"type": "function", "function": {"name": "load_vector", "description": "加载矢量图层（Shapefile/GeoJSON/GPKG等）到QGIS项目",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "文件完整路径"},
            "layer_name": {"type": "string", "description": "图层名称（可选）"}},
        "required": ["path"]}}},
    {"type": "function", "function": {"name": "load_raster", "description": "加载栅格图层（GeoTIFF/IMG等）到QGIS项目",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "文件完整路径"},
            "layer_name": {"type": "string", "description": "图层名称（可选）"}},
        "required": ["path"]}}},
    {"type": "function", "function": {"name": "list_layers", "description": "列出当前QGIS项目中的所有图层",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "get_layer_info", "description": "获取指定图层的详细信息（字段、要素数、范围等）",
        "parameters": {"type": "object", "properties": {
            "layer_name": {"type": "string", "description": "图层名称"}},
        "required": ["layer_name"]}}},
    {"type": "function", "function": {"name": "buffer", "description": "对矢量图层创建缓冲区",
        "parameters": {"type": "object", "properties": {
            "layer_name": {"type": "string", "description": "输入图层名称"},
            "distance": {"type": "number", "description": "缓冲距离（图层单位）"},
            "output_path": {"type": "string", "description": "输出路径（可选，空则存入内存）"}},
        "required": ["layer_name", "distance"]}}},
    {"type": "function", "function": {"name": "clip", "description": "用一个图层裁剪另一个图层",
        "parameters": {"type": "object", "properties": {
            "input_layer": {"type": "string", "description": "被裁剪图层"},
            "overlay_layer": {"type": "string", "description": "裁剪边界图层"},
            "output_path": {"type": "string", "description": "输出路径（可选）"}},
        "required": ["input_layer", "overlay_layer"]}}},
    {"type": "function", "function": {"name": "intersect", "description": "两图层空间叠加取交集",
        "parameters": {"type": "object", "properties": {
            "input_layer": {"type": "string"},
            "overlay_layer": {"type": "string"},
            "output_path": {"type": "string"}},
        "required": ["input_layer", "overlay_layer"]}}},
    {"type": "function", "function": {"name": "dissolve", "description": "融合图层要素（可按字段分组）",
        "parameters": {"type": "object", "properties": {
            "layer_name": {"type": "string"},
            "field": {"type": "string", "description": "分组字段（可选，空则全部融合）"},
            "output_path": {"type": "string"}},
        "required": ["layer_name"]}}},
    {"type": "function", "function": {"name": "calculate_area", "description": "计算图层所有要素的面积统计信息",
        "parameters": {"type": "object", "properties": {
            "layer_name": {"type": "string"}},
        "required": ["layer_name"]}}},
    {"type": "function", "function": {"name": "reproject", "description": "将图层重投影到指定坐标系",
        "parameters": {"type": "object", "properties": {
            "layer_name": {"type": "string"},
            "target_crs": {"type": "string", "description": "目标坐标系，如 EPSG:4326"},
            "output_path": {"type": "string"}},
        "required": ["layer_name", "target_crs"]}}},
    {"type": "function", "function": {"name": "export_layer", "description": "将图层导出到文件",
        "parameters": {"type": "object", "properties": {
            "layer_name": {"type": "string"},
            "output_path": {"type": "string", "description": "输出文件完整路径"},
            "format": {"type": "string", "description": "格式: GPKG/ESRI Shapefile/GeoJSON", "default": "GPKG"}},
        "required": ["layer_name", "output_path"]}}},
    {"type": "function", "function": {"name": "generate_report", "description": "为图层生成HTML分析报告",
        "parameters": {"type": "object", "properties": {
            "title": {"type": "string", "description": "报告标题"},
            "layer_name": {"type": "string"},
            "output_path": {"type": "string", "description": "HTML报告输出路径"}},
        "required": ["title", "layer_name", "output_path"]}}},
    {"type": "function", "function": {"name": "execute_python", "description": "在QGIS环境中执行PyQGIS Python代码",
        "parameters": {"type": "object", "properties": {
            "code": {"type": "string", "description": "Python代码字符串"}},
        "required": ["code"]}}},
    {"type": "function", "function": {"name": "run_processing_algorithm", "description": "运行QGIS Processing框架中的任意算法",
        "parameters": {"type": "object", "properties": {
            "algorithm": {"type": "string", "description": "算法ID，如 native:buffer"},
            "params": {"type": "object", "description": "算法参数字典"}},
        "required": ["algorithm", "params"]}}},
]


# ============================================================
# 辅助函数
# ============================================================
def _get_layer(name: str):
    """按名称查找图层"""
    if not QGIS_AVAILABLE:
        return None
    for layer in QgsProject.instance().mapLayers().values():
        if layer.name() == name:
            return layer
    return None
