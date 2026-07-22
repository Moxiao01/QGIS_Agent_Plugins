import json

from qgis.core import (
    QgsApplication,
    QgsFeature,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsVectorLayer,
)


QgsApplication.setPrefixPath(__import__("os").environ["QGIS_PREFIX_PATH"], True)
app = QgsApplication([], False)
app.initQgis()

try:
    from processing.core.Processing import Processing
    Processing.initialize()

    from qgis_agent import classFactory
    from qgis_agent.tools.spatial_tools import (
        ALL_TOOLS,
        TOOL_SCHEMAS,
        DataLoadTools,
        SpatialAnalysisTools,
    )

    layer = QgsVectorLayer("Polygon?crs=EPSG:4326&field=name:string", "areas", "memory")
    feature = QgsFeature(layer.fields())
    feature.setAttributes(["a"])
    feature.setGeometry(QgsGeometry.fromPolygonXY([[
        QgsPointXY(0, 0), QgsPointXY(0, 1), QgsPointXY(1, 1),
        QgsPointXY(1, 0), QgsPointXY(0, 0),
    ]]))
    layer.dataProvider().addFeature(feature)
    layer.updateExtents()
    QgsProject.instance().addMapLayer(layer)

    layers = DataLoadTools.list_layers().to_dict()
    area = SpatialAnalysisTools.calculate_area("areas").to_dict()
    buffer_result = SpatialAnalysisTools.buffer("areas", 500).to_dict()
    schema_names = {(item.get("function") or item)["name"] for item in TOOL_SCHEMAS}
    plugin_instance = classFactory(None)

    checks = {
        "list_layers": layers["success"] and layers["data"]["count"] == 1,
        "calculate_area": area["success"] and area["data"]["count"] == 1,
        "geographic_buffer_rejected": (
            not buffer_result["success"]
            and buffer_result["data"]["error_code"] == "GEOGRAPHIC_CRS_DISTANCE"
        ),
        "tool_registry_matches_schema": set(ALL_TOOLS) == schema_names and len(ALL_TOOLS) == 17,
        "plugin_entrypoint_loads": (
            plugin_instance.__class__.__name__ == "QGISAgentPlugin"
            and plugin_instance.panel is None
            and plugin_instance.agent is None
        ),
    }
    output = {
        "success": all(checks.values()),
        "checks": checks,
        "details": {"layers": layers, "area": area, "buffer": buffer_result},
    }
    print(json.dumps(output, ensure_ascii=True, default=str))
    if not output["success"]:
        raise SystemExit(1)
finally:
    QgsProject.instance().clear()
    app.exitQgis()
