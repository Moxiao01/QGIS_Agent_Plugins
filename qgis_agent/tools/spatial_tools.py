# -*- coding: utf-8 -*-
"""QGIS spatial tools exposed to the LLM function-calling loop."""

import html
import json
import os
from datetime import datetime
from typing import Any, Dict, Optional
from urllib.parse import urlencode

try:
    import processing
    from qgis.core import (
        QgsCoordinateReferenceSystem,
        QgsDistanceArea,
        QgsMapLayer,
        QgsMapRendererParallelJob,
        QgsMapSettings,
        QgsProject,
        QgsRasterLayer,
        QgsRectangle,
        QgsVectorFileWriter,
        QgsVectorLayer,
        QgsWkbTypes,
    )
    from qgis.PyQt.QtCore import QSize
    QGIS_AVAILABLE = True
except ImportError:
    QGIS_AVAILABLE = False


_IFACE = None
_CONFIG = None


def set_runtime_context(iface=None, config=None) -> None:
    """Provide QGIS and configuration context to built-in tools."""
    global _IFACE, _CONFIG
    _IFACE = iface
    _CONFIG = config


class ToolResult:
    """Serializable result returned by every built-in tool."""

    def __init__(self, success: bool, data: Any = None, message: str = ""):
        self.success = success
        self.data = data
        self.message = message

    def to_dict(self) -> Dict[str, Any]:
        return {"success": self.success, "data": self.data, "message": self.message}

    def __str__(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, default=str)


def _require_qgis() -> Optional[ToolResult]:
    if not QGIS_AVAILABLE:
        return ToolResult(False, message="QGIS 环境不可用")
    return None


def _get_layer(name_or_id: str):
    if not QGIS_AVAILABLE:
        return None
    query = str(name_or_id or "").strip()
    if not query:
        return None
    project = QgsProject.instance()
    by_id = project.mapLayer(query)
    if by_id:
        return by_id
    exact = [layer for layer in project.mapLayers().values() if layer.name() == query]
    if len(exact) > 1:
        candidates = ", ".join(f"{layer.name()} [{layer.id()}]" for layer in exact[:10])
        raise ValueError(f"图层名称不唯一，请使用图层 ID: {candidates}")
    if exact:
        return exact[0]
    folded = query.casefold()
    matches = [layer for layer in project.mapLayers().values() if layer.name().casefold() == folded]
    if len(matches) > 1:
        candidates = ", ".join(f"{layer.name()} [{layer.id()}]" for layer in matches[:10])
        raise ValueError(f"图层名称不唯一，请使用图层 ID: {candidates}")
    return matches[0] if matches else None


def _ensure_parent(path: str, allow_existing: bool = False) -> str:
    """Resolve an output path, enforce the configured root, and prevent overwrite."""
    raw = str(path or "").strip()
    if not raw:
        raise ValueError("输出路径不能为空")
    expanded = os.path.expandvars(os.path.expanduser(raw))
    output_root = getattr(_CONFIG, "output_dir", "") if _CONFIG is not None else ""
    if not os.path.isabs(expanded) and output_root:
        expanded = os.path.join(output_root, expanded)
    expanded = os.path.abspath(expanded)

    if _CONFIG is not None and getattr(_CONFIG, "restrict_output_paths", True):
        root = os.path.realpath(os.path.abspath(output_root))
        candidate = os.path.realpath(expanded)
        try:
            inside_root = os.path.commonpath([root, candidate]) == root
        except ValueError:
            inside_root = False
        if not inside_root:
            raise ValueError(
                f"输出路径超出允许目录: {expanded}; "
                f"当前允许目录: {root}"
            )

    overwrite_allowed = bool(
        allow_existing or (_CONFIG is not None and getattr(_CONFIG, "allow_overwrite", False))
    )
    if os.path.exists(expanded) and not overwrite_allowed:
        raise FileExistsError(f"输出已存在，默认拒绝覆盖: {expanded}")
    parent = os.path.dirname(expanded)
    if parent:
        os.makedirs(parent, exist_ok=True)
    return expanded


def _layer_data(layer) -> Dict[str, Any]:
    data = {
        "layer_id": layer.id(),
        "name": layer.name(),
        "source": layer.source(),
        "crs": layer.crs().authid(),
    }
    if layer.type() == QgsMapLayer.VectorLayer:
        data.update({
            "type": "vector",
            "feature_count": layer.featureCount(),
            "geometry_type": QgsWkbTypes.displayString(layer.wkbType()),
        })
    else:
        data.update({"type": "raster", "width": layer.width(), "height": layer.height()})
    return data


def _add_layer_once(layer, preferred_name: str = ""):
    if preferred_name:
        layer.setName(preferred_name)
    project = QgsProject.instance()
    if project.mapLayer(layer.id()) is None:
        project.addMapLayer(layer)
    return layer


def _publish_output(value: Any, preferred_name: str = "") -> Dict[str, Any]:
    if isinstance(value, (QgsVectorLayer, QgsRasterLayer)):
        return _layer_data(_add_layer_once(value, preferred_name))
    if isinstance(value, str):
        path = value
        if os.path.exists(path):
            vector = QgsVectorLayer(path, preferred_name or os.path.splitext(os.path.basename(path))[0], "ogr")
            if vector.isValid():
                return _layer_data(_add_layer_once(vector))
            raster = QgsRasterLayer(path, preferred_name or os.path.splitext(os.path.basename(path))[0])
            if raster.isValid():
                return _layer_data(_add_layer_once(raster))
        return {"output": path}
    return {"output": str(value)}


def _run_vector_algorithm(algorithm: str, params: Dict[str, Any], output_name: str, success_message: str) -> ToolResult:
    try:
        result = processing.run(algorithm, params)
        value = result.get("OUTPUT")
        return ToolResult(True, data=_publish_output(value, output_name), message=success_message)
    except Exception as exc:
        return ToolResult(False, message=f"处理失败（{algorithm}）: {exc}")


class DataLoadTools:
    """Data loading and project inspection tools."""

    @staticmethod
    def load_vector(path: str, layer_name: str = "") -> ToolResult:
        unavailable = _require_qgis()
        if unavailable:
            return unavailable
        expanded = os.path.abspath(os.path.expandvars(os.path.expanduser(path)))
        if not os.path.exists(expanded):
            return ToolResult(False, message=f"文件不存在: {expanded}")
        name = layer_name or os.path.splitext(os.path.basename(expanded))[0]
        layer = QgsVectorLayer(expanded, name, "ogr")
        if not layer.isValid():
            return ToolResult(False, message=f"无法加载矢量图层: {expanded}")
        _add_layer_once(layer)
        return ToolResult(True, data=_layer_data(layer), message=f"已加载矢量图层: {name}")

    @staticmethod
    def load_raster(path: str, layer_name: str = "") -> ToolResult:
        unavailable = _require_qgis()
        if unavailable:
            return unavailable
        expanded = os.path.abspath(os.path.expandvars(os.path.expanduser(path)))
        if not os.path.exists(expanded):
            return ToolResult(False, message=f"文件不存在: {expanded}")
        name = layer_name or os.path.splitext(os.path.basename(expanded))[0]
        layer = QgsRasterLayer(expanded, name)
        if not layer.isValid():
            return ToolResult(False, message=f"无法加载栅格图层: {expanded}")
        _add_layer_once(layer)
        return ToolResult(True, data=_layer_data(layer), message=f"已加载栅格图层: {name}")

    @staticmethod
    def load_wms(
        url: str,
        layers: str = "",
        layer_name: str = "WMS图层",
        crs: str = "EPSG:3857",
        image_format: str = "image/png",
    ) -> ToolResult:
        unavailable = _require_qgis()
        if unavailable:
            return unavailable
        if not url.strip():
            return ToolResult(False, message="WMS 服务 URL 不能为空")
        if url.startswith("url=") or "&layers=" in url:
            uri = url
        else:
            if not layers.strip():
                return ToolResult(False, message="请提供 WMS 图层名（layers 参数）")
            uri = urlencode({
                "url": url.strip(),
                "layers": layers.strip(),
                "styles": "",
                "format": image_format,
                "crs": crs,
            })
        layer = QgsRasterLayer(uri, layer_name, "wms")
        if not layer.isValid():
            return ToolResult(False, message=f"无法连接 WMS 或图层参数无效: {url}")
        _add_layer_once(layer)
        return ToolResult(True, data=_layer_data(layer), message=f"已加载 WMS 图层: {layer_name}")

    @staticmethod
    def list_layers() -> ToolResult:
        unavailable = _require_qgis()
        if unavailable:
            return unavailable
        project = QgsProject.instance()
        layers = []
        for layer_id, layer in project.mapLayers().items():
            node = project.layerTreeRoot().findLayer(layer_id)
            item = _layer_data(layer)
            item["visible"] = node.isVisible() if node else True
            layers.append(item)
        return ToolResult(True, data={"layers": layers, "count": len(layers)}, message=f"当前项目共 {len(layers)} 个图层")

    @staticmethod
    def get_layer_info(layer_name: str) -> ToolResult:
        unavailable = _require_qgis()
        if unavailable:
            return unavailable
        layer = _get_layer(layer_name)
        if not layer:
            return ToolResult(False, message=f"找不到图层: {layer_name}")
        info = _layer_data(layer)
        extent = layer.extent()
        info["extent"] = {
            "xmin": extent.xMinimum(), "ymin": extent.yMinimum(),
            "xmax": extent.xMaximum(), "ymax": extent.yMaximum(),
        }
        if layer.type() == QgsMapLayer.VectorLayer:
            info["fields"] = [{"name": field.name(), "type": field.typeName()} for field in layer.fields()]
        return ToolResult(True, data=info, message=f"已读取图层信息: {layer.name()}")


class SpatialAnalysisTools:
    """Common vector processing and measurement tools."""

    @staticmethod
    def buffer(layer_name: str, distance: float, output_path: str = "") -> ToolResult:
        unavailable = _require_qgis()
        if unavailable:
            return unavailable
        layer = _get_layer(layer_name)
        if not layer or layer.type() != QgsMapLayer.VectorLayer:
            return ToolResult(False, message=f"找不到矢量图层: {layer_name}")
        if distance <= 0:
            return ToolResult(False, message="缓冲距离必须大于 0")
        if layer.crs().isGeographic():
            return ToolResult(
                False,
                data={"error_code": "GEOGRAPHIC_CRS_DISTANCE", "crs": layer.crs().authid(), "suggested_action": "reproject"},
                message="输入图层为地理坐标系，不能直接按米执行缓冲；请先重投影到合适的投影坐标系。",
            )
        output = _ensure_parent(output_path) if output_path else "memory:"
        return _run_vector_algorithm("native:buffer", {
            "INPUT": layer, "DISTANCE": distance, "SEGMENTS": 8,
            "END_CAP_STYLE": 0, "JOIN_STYLE": 0, "MITER_LIMIT": 2,
            "DISSOLVE": False, "OUTPUT": output,
        }, f"{layer.name()}_buffer", f"缓冲区完成，距离 {distance}（图层单位）")

    @staticmethod
    def clip(input_layer: str, overlay_layer: str, output_path: str = "") -> ToolResult:
        unavailable = _require_qgis()
        if unavailable:
            return unavailable
        source = _get_layer(input_layer)
        overlay = _get_layer(overlay_layer)
        if not source or source.type() != QgsMapLayer.VectorLayer:
            return ToolResult(False, message=f"找不到矢量输入图层: {input_layer}")
        if not overlay or overlay.type() != QgsMapLayer.VectorLayer:
            return ToolResult(False, message=f"找不到矢量裁剪图层: {overlay_layer}")
        if QgsWkbTypes.geometryType(overlay.wkbType()) != QgsWkbTypes.PolygonGeometry:
            return ToolResult(False, message="裁剪图层必须是面图层")
        output = _ensure_parent(output_path) if output_path else "memory:"
        return _run_vector_algorithm("native:clip", {"INPUT": source, "OVERLAY": overlay, "OUTPUT": output}, f"{source.name()}_clip", f"裁剪完成: {source.name()} ∩ {overlay.name()}")

    @staticmethod
    def intersect(input_layer: str, overlay_layer: str, output_path: str = "") -> ToolResult:
        unavailable = _require_qgis()
        if unavailable:
            return unavailable
        source = _get_layer(input_layer)
        overlay = _get_layer(overlay_layer)
        if not source or not overlay or source.type() != QgsMapLayer.VectorLayer or overlay.type() != QgsMapLayer.VectorLayer:
            return ToolResult(False, message="找不到指定的矢量输入或叠加图层")
        output = _ensure_parent(output_path) if output_path else "memory:"
        return _run_vector_algorithm("native:intersection", {
            "INPUT": source, "OVERLAY": overlay, "INPUT_FIELDS": [],
            "OVERLAY_FIELDS": [], "OVERLAY_FIELDS_PREFIX": "", "OUTPUT": output,
        }, f"{source.name()}_intersection", "交集分析完成")

    @staticmethod
    def dissolve(layer_name: str, field: str = "", output_path: str = "") -> ToolResult:
        unavailable = _require_qgis()
        if unavailable:
            return unavailable
        layer = _get_layer(layer_name)
        if not layer or layer.type() != QgsMapLayer.VectorLayer:
            return ToolResult(False, message=f"找不到矢量图层: {layer_name}")
        if field and layer.fields().indexOf(field) < 0:
            return ToolResult(False, message=f"字段不存在: {field}")
        output = _ensure_parent(output_path) if output_path else "memory:"
        return _run_vector_algorithm("native:dissolve", {
            "INPUT": layer, "FIELD": [field] if field else [], "SEPARATE_DISJOINT": False, "OUTPUT": output,
        }, f"{layer.name()}_dissolve", f"融合完成: {layer.name()}")

    @staticmethod
    def calculate_area(layer_name: str) -> ToolResult:
        unavailable = _require_qgis()
        if unavailable:
            return unavailable
        layer = _get_layer(layer_name)
        if not layer or layer.type() != QgsMapLayer.VectorLayer:
            return ToolResult(False, message=f"找不到矢量图层: {layer_name}")
        if QgsWkbTypes.geometryType(layer.wkbType()) != QgsWkbTypes.PolygonGeometry:
            return ToolResult(False, message="面积统计仅适用于面图层")

        calculator = QgsDistanceArea()
        calculator.setSourceCrs(layer.crs(), QgsProject.instance().transformContext())
        ellipsoid = QgsProject.instance().ellipsoid() or "WGS84"
        calculator.setEllipsoid(ellipsoid)
        areas = []
        for feature in layer.getFeatures():
            geometry = feature.geometry()
            if geometry and not geometry.isEmpty():
                areas.append(abs(calculator.measureArea(geometry)))
        if not areas:
            return ToolResult(False, message="图层中没有可计算面积的有效几何")
        total = sum(areas)
        return ToolResult(True, data={
            "count": len(areas), "ellipsoid": ellipsoid,
            "total_m2": round(total, 3), "total_km2": round(total / 1_000_000, 6),
            "mean_m2": round(total / len(areas), 3),
            "min_m2": round(min(areas), 3), "max_m2": round(max(areas), 3),
        }, message=f"面积统计完成，总面积 {total / 1_000_000:.6f} km²")

    @staticmethod
    def reproject(layer_name: str, target_crs: str, output_path: str = "") -> ToolResult:
        unavailable = _require_qgis()
        if unavailable:
            return unavailable
        layer = _get_layer(layer_name)
        if not layer or layer.type() != QgsMapLayer.VectorLayer:
            return ToolResult(False, message=f"找不到矢量图层: {layer_name}")
        crs = QgsCoordinateReferenceSystem(target_crs)
        if not crs.isValid():
            return ToolResult(False, message=f"无效坐标系: {target_crs}")
        output = _ensure_parent(output_path) if output_path else "memory:"
        return _run_vector_algorithm("native:reprojectlayer", {
            "INPUT": layer, "TARGET_CRS": crs, "CONVERT_CURVED_GEOMETRIES": False, "OUTPUT": output,
        }, f"{layer.name()}_{crs.authid().replace(':', '_')}", f"重投影完成: {crs.authid()}")

    @staticmethod
    def spatial_join(
        input_layer: str,
        join_layer: str,
        predicate: str = "intersects",
        output_path: str = "",
    ) -> ToolResult:
        unavailable = _require_qgis()
        if unavailable:
            return unavailable
        source = _get_layer(input_layer)
        join = _get_layer(join_layer)
        if not source or not join or source.type() != QgsMapLayer.VectorLayer or join.type() != QgsMapLayer.VectorLayer:
            return ToolResult(False, message="找不到指定的矢量输入或连接图层")
        predicate_map = {
            "intersects": 0, "contains": 1, "disjoint": 2, "equals": 3,
            "touches": 4, "overlaps": 5, "within": 6, "crosses": 7,
        }
        key = predicate.strip().lower()
        if key not in predicate_map:
            return ToolResult(False, message=f"不支持的空间关系: {predicate}")
        output = _ensure_parent(output_path) if output_path else "memory:"
        return _run_vector_algorithm("native:joinattributesbylocation", {
            "INPUT": source, "JOIN": join, "PREDICATE": [predicate_map[key]],
            "JOIN_FIELDS": [], "METHOD": 0, "DISCARD_NONMATCHING": False,
            "PREFIX": "", "OUTPUT": output,
        }, f"{source.name()}_spatial_join", f"空间连接完成（{key}）")


class OutputTools:
    """Layer, map and report output tools."""

    @staticmethod
    def export_layer(layer_name: str, output_path: str, format: str = "GPKG") -> ToolResult:
        unavailable = _require_qgis()
        if unavailable:
            return unavailable
        layer = _get_layer(layer_name)
        if not layer or layer.type() != QgsMapLayer.VectorLayer:
            return ToolResult(False, message=f"找不到矢量图层: {layer_name}")
        path = _ensure_parent(output_path)
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = format
        if format.upper() == "GPKG":
            options.layerName = os.path.splitext(os.path.basename(path))[0]
        try:
            error, message, _, _ = QgsVectorFileWriter.writeAsVectorFormatV3(
                layer, path, QgsProject.instance().transformContext(), options
            )
        except Exception as exc:
            return ToolResult(False, message=f"导出失败: {exc}")
        if error != QgsVectorFileWriter.NoError:
            return ToolResult(False, message=f"导出失败: {message}")
        return ToolResult(True, data={"path": path, "format": format}, message=f"图层已导出: {path}")

    @staticmethod
    def export_map_image(output_path: str, width: int = 1920, height: int = 1080, dpi: int = 96) -> ToolResult:
        unavailable = _require_qgis()
        if unavailable:
            return unavailable
        if width < 64 or height < 64 or width > 16384 or height > 16384:
            return ToolResult(False, message="图片宽高必须在 64 到 16384 像素之间")
        path = _ensure_parent(output_path)
        try:
            if _IFACE is not None and hasattr(_IFACE, "mapCanvas"):
                canvas_settings = _IFACE.mapCanvas().mapSettings()
                settings = QgsMapSettings(canvas_settings)
                settings.setExtent(_IFACE.mapCanvas().extent())
            else:
                settings = QgsMapSettings()
                layers = list(QgsProject.instance().mapLayers().values())
                settings.setLayers(layers)
                extent = QgsRectangle()
                for layer in layers:
                    extent.combineExtentWith(layer.extent())
                if extent.isEmpty():
                    return ToolResult(False, message="当前项目没有可导出的地图范围")
                settings.setExtent(extent)
            settings.setOutputSize(QSize(int(width), int(height)))
            settings.setOutputDpi(int(dpi))
            job = QgsMapRendererParallelJob(settings)
            job.start()
            job.waitForFinished()
            image = job.renderedImage()
            if image.isNull() or not image.save(path):
                return ToolResult(False, message=f"无法保存地图图片: {path}")
            return ToolResult(True, data={"path": path, "width": width, "height": height, "dpi": dpi}, message=f"地图已导出: {path}")
        except Exception as exc:
            return ToolResult(False, message=f"导出地图失败: {exc}")

    @staticmethod
    def generate_report(title: str, layer_name: str, output_path: str, max_features: int = 1000) -> ToolResult:
        unavailable = _require_qgis()
        if unavailable:
            return unavailable
        layer = _get_layer(layer_name)
        if not layer or layer.type() != QgsMapLayer.VectorLayer:
            return ToolResult(False, message=f"找不到矢量图层: {layer_name}")
        max_features = min(10000, max(1, int(max_features)))
        path = _ensure_parent(output_path)
        fields = [field.name() for field in layer.fields()]
        rows = []
        for index, feature in enumerate(layer.getFeatures()):
            if index >= max_features:
                break
            cells = "".join(f"<td>{html.escape(str(value))}</td>" for value in feature.attributes())
            rows.append(f"<tr>{cells}</tr>")
        feature_count = int(layer.featureCount())
        truncated = feature_count > len(rows)
        extent = layer.extent()
        safe_title = html.escape(title)
        safe_name = html.escape(layer.name())
        headers = "".join(f"<th>{html.escape(field)}</th>" for field in fields)
        notice = f"<p class='warning'>仅展示前 {len(rows)} 条记录，共 {feature_count} 条。</p>" if truncated else ""
        document = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{safe_title}</title><style>
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;margin:2rem;color:#243238}}
h1{{color:#246b45}} .info{{background:#eef7f1;padding:1rem;border-radius:8px;line-height:1.8}}
.warning{{background:#fff4d6;padding:.75rem;border-left:4px solid #e3a008}}
table{{border-collapse:collapse;width:100%;font-size:14px}} th{{background:#246b45;color:#fff}}
th,td{{padding:7px;border:1px solid #d9e1dc;text-align:left}} tr:nth-child(even){{background:#f7faf8}}
</style></head><body><h1>{safe_title}</h1><div class="info">
<b>图层：</b>{safe_name}<br><b>要素数：</b>{feature_count}<br><b>坐标系：</b>{html.escape(layer.crs().authid())}<br>
<b>范围：</b>{extent.xMinimum():.6f}, {extent.yMinimum():.6f} → {extent.xMaximum():.6f}, {extent.yMaximum():.6f}<br>
<b>生成时间：</b>{datetime.now().astimezone().isoformat(timespec='seconds')}</div>{notice}
<h2>属性数据</h2><table><thead><tr>{headers}</tr></thead><tbody>{''.join(rows)}</tbody></table></body></html>"""
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(document)
        except OSError as exc:
            return ToolResult(False, message=f"写入报告失败: {exc}")
        return ToolResult(True, data={"path": path, "rows": len(rows), "truncated": truncated}, message=f"报告已生成: {path}")


class CodeExecutionTools:
    """Explicitly confirmed PyQGIS and Processing execution tools."""

    @staticmethod
    def execute_python(code: str) -> ToolResult:
        if _CONFIG is None or not getattr(_CONFIG, "enable_python_tool", False):
            return ToolResult(False, message="Python 执行工具未启用")
        import io
        from contextlib import redirect_stderr, redirect_stdout

        if not str(code).strip():
            return ToolResult(False, message="Python 代码不能为空")
        stdout_buffer = io.StringIO()
        stderr_buffer = io.StringIO()
        globals_dict: Dict[str, Any] = {"__builtins__": __builtins__, "iface": _IFACE}
        if QGIS_AVAILABLE:
            import qgis.core as qgis_core
            globals_dict.update({"QgsProject": QgsProject, "processing": processing, "qgis_core": qgis_core})
        try:
            with redirect_stdout(stdout_buffer), redirect_stderr(stderr_buffer):
                exec(code, globals_dict)
        except Exception as exc:
            return ToolResult(False, data={"stderr": stderr_buffer.getvalue()}, message=f"代码执行失败: {exc}")
        stdout = stdout_buffer.getvalue()
        stderr = stderr_buffer.getvalue()
        return ToolResult(True, data={"stdout": stdout, "stderr": stderr}, message="代码执行成功")

    @staticmethod
    def run_processing_algorithm(algorithm: str, params: Dict[str, Any]) -> ToolResult:
        if _CONFIG is None or not getattr(_CONFIG, "enable_generic_processing", False):
            return ToolResult(False, message="通用 Processing 工具未启用")
        algorithm = str(algorithm or "").strip()
        if not algorithm:
            return ToolResult(False, message="Processing 算法 ID 不能为空")
        allowed = {
            str(item).strip()
            for item in (getattr(_CONFIG, "allowed_processing_algorithms", []) or [])
            if str(item).strip()
        }
        if algorithm not in allowed:
            return ToolResult(
                False,
                data={"error_code": "PROCESSING_ALGORITHM_NOT_ALLOWED", "algorithm": algorithm},
                message=f"Processing 算法不在白名单中: {algorithm}",
            )
        if not isinstance(params, dict):
            return ToolResult(False, message="params 必须是对象")
        unavailable = _require_qgis()
        if unavailable:
            return unavailable

        def resolve(value):
            if isinstance(value, str):
                return _get_layer(value) or value
            if isinstance(value, list):
                return [resolve(item) for item in value]
            if isinstance(value, dict):
                return {key: resolve(item) for key, item in value.items()}
            return value

        try:
            resolved = resolve(params)
            for key, value in list(resolved.items()):
                if key.upper().startswith("OUTPUT") and isinstance(value, str) and value not in {"TEMPORARY_OUTPUT", "memory:"}:
                    resolved[key] = _ensure_parent(value)
            result = processing.run(algorithm, resolved)
        except Exception as exc:
            return ToolResult(False, message=f"算法执行失败（{algorithm}）: {exc}")
        serializable = {}
        for key, value in result.items():
            serializable[key] = _publish_output(value, f"{algorithm.replace(':', '_')}_{key.lower()}")
        return ToolResult(True, data=serializable, message=f"算法执行成功: {algorithm}")


ALL_TOOLS = {
    "load_vector": DataLoadTools.load_vector,
    "load_raster": DataLoadTools.load_raster,
    "load_wms": DataLoadTools.load_wms,
    "list_layers": DataLoadTools.list_layers,
    "get_layer_info": DataLoadTools.get_layer_info,
    "buffer": SpatialAnalysisTools.buffer,
    "clip": SpatialAnalysisTools.clip,
    "intersect": SpatialAnalysisTools.intersect,
    "dissolve": SpatialAnalysisTools.dissolve,
    "calculate_area": SpatialAnalysisTools.calculate_area,
    "reproject": SpatialAnalysisTools.reproject,
    "spatial_join": SpatialAnalysisTools.spatial_join,
    "export_layer": OutputTools.export_layer,
    "export_map_image": OutputTools.export_map_image,
    "generate_report": OutputTools.generate_report,
    "execute_python": CodeExecutionTools.execute_python,
    "run_processing_algorithm": CodeExecutionTools.run_processing_algorithm,
}


def _schema(name, description, properties=None, required=None):
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties or {}, "required": required or []},
    }}


TOOL_SCHEMAS = [
    _schema("load_vector", "加载 Shapefile、GeoJSON、GPKG 等矢量数据", {
        "path": {"type": "string", "description": "文件绝对路径"},
        "layer_name": {"type": "string", "description": "可选图层名"}}, ["path"]),
    _schema("load_raster", "加载 GeoTIFF、IMG 等栅格数据", {
        "path": {"type": "string", "description": "文件绝对路径"},
        "layer_name": {"type": "string", "description": "可选图层名"}}, ["path"]),
    _schema("load_wms", "加载 WMS 网络地图图层", {
        "url": {"type": "string", "description": "WMS 服务 URL"},
        "layers": {"type": "string", "description": "服务中的图层名"},
        "layer_name": {"type": "string"}, "crs": {"type": "string", "default": "EPSG:3857"},
        "image_format": {"type": "string", "default": "image/png"}}, ["url", "layers"]),
    _schema("list_layers", "列出当前 QGIS 项目的全部图层"),
    _schema("get_layer_info", "读取图层字段、范围、坐标系和要素数", {
        "layer_name": {"type": "string", "description": "图层名称或 ID"}}, ["layer_name"]),
    _schema("buffer", "对矢量图层创建缓冲区；距离单位是图层坐标单位", {
        "layer_name": {"type": "string"}, "distance": {"type": "number"},
        "output_path": {"type": "string"}}, ["layer_name", "distance"]),
    _schema("clip", "使用面图层裁剪输入图层", {
        "input_layer": {"type": "string"}, "overlay_layer": {"type": "string"},
        "output_path": {"type": "string"}}, ["input_layer", "overlay_layer"]),
    _schema("intersect", "计算两个矢量图层的交集", {
        "input_layer": {"type": "string"}, "overlay_layer": {"type": "string"},
        "output_path": {"type": "string"}}, ["input_layer", "overlay_layer"]),
    _schema("dissolve", "融合图层全部要素或按字段分组融合", {
        "layer_name": {"type": "string"}, "field": {"type": "string"},
        "output_path": {"type": "string"}}, ["layer_name"]),
    _schema("calculate_area", "按椭球计算面图层面积统计", {
        "layer_name": {"type": "string"}}, ["layer_name"]),
    _schema("reproject", "将矢量图层重投影到目标 CRS", {
        "layer_name": {"type": "string"}, "target_crs": {"type": "string", "description": "如 EPSG:3857"},
        "output_path": {"type": "string"}}, ["layer_name", "target_crs"]),
    _schema("spatial_join", "按空间关系连接两个图层的属性", {
        "input_layer": {"type": "string"}, "join_layer": {"type": "string"},
        "predicate": {"type": "string", "enum": ["intersects", "contains", "disjoint", "equals", "touches", "overlaps", "within", "crosses"]},
        "output_path": {"type": "string"}}, ["input_layer", "join_layer"]),
    _schema("export_layer", "将矢量图层导出为 GPKG、Shapefile 或 GeoJSON", {
        "layer_name": {"type": "string"}, "output_path": {"type": "string"},
        "format": {"type": "string", "default": "GPKG"}}, ["layer_name", "output_path"]),
    _schema("export_map_image", "导出当前地图画布为图片", {
        "output_path": {"type": "string"}, "width": {"type": "integer", "default": 1920},
        "height": {"type": "integer", "default": 1080}, "dpi": {"type": "integer", "default": 96}}, ["output_path"]),
    _schema("generate_report", "生成经过 HTML 转义的图层属性报告", {
        "title": {"type": "string"}, "layer_name": {"type": "string"},
        "output_path": {"type": "string"}, "max_features": {"type": "integer", "default": 1000}},
        ["title", "layer_name", "output_path"]),
    _schema("execute_python", "执行自定义 PyQGIS Python 代码；默认需要用户确认", {
        "code": {"type": "string"}}, ["code"]),
    _schema("run_processing_algorithm", "运行指定 QGIS Processing 算法", {
        "algorithm": {"type": "string", "description": "如 native:buffer"},
        "params": {"type": "object"}}, ["algorithm", "params"]),
]