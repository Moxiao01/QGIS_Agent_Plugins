# QGIS Agent 验证基线

> 更新日期：2026-07-22。这份文档记录当前可重复的测试范围，不把单元测试通过率当作真实 LLM 任务成功率。

## 当前结果

| 层级 | 结果 | 覆盖范围 |
| --- | --- | --- |
| 普通 Python 单元测试 | 23/23 通过（100%） | 配置、LLM 协议转换、HTTP 重试、Agent 工具循环、权限策略、输出路径、JSONL 日志、评测数据格式 |
| QGIS 3.44.11 集成探针 | 5/5 通过（100%） | 图层列表、面积计算、地理坐标系缓冲拒绝、工具/Schema 一致性、插件 classFactory 入口加载 |
| 真实 LLM 端到端任务 | 尚未形成稳定样本量 | 需要按模型、数据集和 QGIS 版本分开统计 |

上表的 100% 只表示已编写的确定性检查全部通过，不代表用户自然语言任务已达到 100% 成功率。

## 完整任务：经纬度面图层的 500 米缓冲

### 1. 用户输入

```text
将 areas 图层按 500 米缓冲，保存为 areas_buffer.gpkg。
```

### 2. 预期工具链

1. `get_layer_info("areas")`：确认图层类型和 CRS。
2. `buffer("areas", 500, ...)`：首次调用应返回 `GEOGRAPHIC_CRS_DISTANCE`，不允许把 500 度当成 500 米。
3. Agent 根据 `suggested_action=reproject` 修正方案，调用 `reproject` 生成适合米制距离的中间图层。
4. 对重投影图层再次调用 `buffer`。
5. Agent 检查最后一次工具结果的 `success`、输出路径和图层信息，再给出完成结论。

### 3. 失败重试边界

- HTTP `429/500/502/503/504`、连接失败和超时：按配置次数做指数退避重试。
- HTTP `400/401/403/404`：默认不重试，因为这类错误通常需要修正配置或参数。
- 工具返回 `success=false`：由 Agent 根据错误码修正参数；不应原样无限重复。
- 达到 `max_iterations`：任务标记为 `iteration_limit`，不计为验收通过。

### 4. 结果验收

最小验收条件：

- 所有工具结果中没有未处理的 `success=false`；
- 输出路径在配置的输出根目录下；
- 输出文件存在且能被 QGIS 重新加载；
- 输出为矢量图层，要素数不小于 1；
- 最终回复明确列出输出位置、CRS 和限制。

JSONL 日志中 `task_finished.accepted` 只是基础验收标志：任务正常结束且没有记录到失败工具。它不能替代几何正确性和业务正确性检查。

## 异常数据集

`tests/data/task_cases.json` 当前包含：

- 地理 CRS 缓冲后重投影重试；
- 重名图层；
- 输出路径逃逸；
- 已有输出覆盖；
- Processing 白名单拒绝；
- Python 执行未授权；
- LLM `429 -> 503 -> 200` 传输层重试。

## 日志和隐私

开启“保存本地 JSONL 任务日志”后，每个用户任务生成一个文件：

```text
<output_dir>/logs/task_<timestamp>_<run_id>.jsonl
```

日志记录 `task_started`、`llm_request`、`llm_response`、`tool_call`、`tool_result` 和 `task_finished`。任务结束后，插件面板的“日志”页会显示本次 JSONL 文件路径。常见密钥字段会被脱敏，但日志仍可能包含用户输入、本地路径、图层名称和工具参数，不应直接对外分享。

## 插件部署边界

- 普通 Python 单元测检查 `metadata.txt`、`icon.svg` 和插件必需入口文件是否存在。
- QGIS 集成探针在 QGIS 自带 Python 中调用 `qgis_agent.classFactory(None)`，确认插件入口和 QGIS/PyQt 依赖可加载。
- 探针使用独立临时 profile，不读写真实 QGIS 工程。
- 当前尚未自动化覆盖“从 ZIP 安装 -> 启用 -> 打开 Dock 面板 -> 卸载”的完整 GUI 生命周期，发布前仍需人工验证。

## 运行命令

```powershell
python -m unittest discover -s tests -v
python -m compileall -q qgis_agent tests
python tests/run_qgis_probe.py --qgis-root D:\QGIS3.44.11
```

QGIS 探针使用独立临时 profile，不依赖当前 QGIS 用户工程，也不会把测试图层写入真实项目。
