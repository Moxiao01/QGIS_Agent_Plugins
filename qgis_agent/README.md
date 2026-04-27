# QGIS Agent 🌍

> 基于大语言模型的 QGIS 智能地理分析助手插件

## 功能特性

- 🗣️ **自然语言交互** — 用中文描述您的地理分析需求
- 🔄 **多轮推理** — Agent 自动分解复杂任务，循环调用工具
- 🗺️ **空间分析** — 缓冲区、裁剪、叠加、融合、重投影等
- 📂 **数据加载** — 支持矢量、栅格、WMS 图层
- 📊 **报告生成** — 自动生成 HTML 属性分析报告
- 🐍 **代码执行** — 生成并执行 PyQGIS 代码
- 🔌 **多 LLM 支持** — 兼容 OpenAI 格式 API（DeepSeek/通义千问/Moonshot/本地 Ollama 等）

## 安装方法

### 方法一：手动安装（推荐）

1. 将整个 `qgis_agent` 文件夹复制到 QGIS 插件目录：
   - Windows: `C:\Users\<用户名>\AppData\Roaming\QGIS\QGIS3\profiles\default\python\plugins\`
   - macOS: `~/Library/Application Support/QGIS/QGIS3/profiles/default/python/plugins/`
   - Linux: `~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/`

2. 启动 QGIS → 插件菜单 → 管理并安装插件 → 已安装 → 勾选 **QGIS Agent**

### 方法二：ZIP 安装

将 `qgis_agent` 文件夹打包为 `qgis_agent.zip`，在插件管理器中选择"从 ZIP 安装"。

## 快速开始

1. 安装并启用插件后，点击工具栏的 **QGIS Agent** 按钮
2. 点击右上角 **⚙ 设置**，填入 LLM API 信息
3. 点击 **🔌 测试连接** 确认配置正确
4. 在对话框输入您的需求，例如：
   - `"加载 D:\data\roads.shp 并显示"`
   - `"对道路图层创建 500 米缓冲区"`
   - `"计算绿地图层的总面积并生成报告"`

## 支持的 LLM 后端

| 后端 | Provider 设置 | 说明 |
|------|--------------|------|
| DeepSeek | openai_compatible | base=https://api.deepseek.com/v1, model=deepseek-chat |
| 通义千问 | openai_compatible | base=https://dashscope.aliyuncs.com/compatible-mode/v1 |
| Moonshot | openai_compatible | base=https://api.moonshot.cn/v1 |
| 智谱 GLM | openai_compatible | base=https://open.bigmodel.cn/api/paas/v4 |
| Ollama 本地 | ollama | base=http://localhost:11434 |
| Anthropic Claude | anthropic | 需要 Anthropic API Key |
| OpenAI | openai_compatible | base=https://api.openai.com/v1 |

## 项目结构

```
qgis_agent/
├── __init__.py          # QGIS 插件入口
├── plugin.py            # 插件主类（生命周期管理）
├── metadata.txt         # 插件元数据
├── core/
│   ├── agent.py         # Agent 核心推理引擎
│   ├── config.py        # 配置管理
│   └── llm_client.py    # LLM 多后端客户端
├── tools/
│   └── spatial_tools.py # 空间分析工具集（16个工具）
└── ui/
    ├── main_panel.py    # 主对话面板
    └── settings_dialog.py # 设置对话框
```

## 扩展自定义工具

```python
# 在插件加载后注册自定义工具
from qgis_agent.tools.spatial_tools import ALL_TOOLS

def my_custom_tool(layer_name: str) -> dict:
    # 自定义逻辑...
    return {"success": True, "message": "完成"}

agent.register_tool("my_tool", my_custom_tool, schema={
    "type": "function",
    "function": {
        "name": "my_tool",
        "description": "我的自定义工具",
        "parameters": {"type": "object", "properties": {
            "layer_name": {"type": "string"}
        }, "required": ["layer_name"]}
    }
})
```

## 系统要求

- QGIS 3.16 或更高版本
- Python 3.7+
- 网络访问（连接 LLM API），或 Ollama 本地部署

## 许可证

MIT License
