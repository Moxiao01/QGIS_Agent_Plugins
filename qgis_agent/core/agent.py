# -*- coding: utf-8 -*-
"""
QGIS Agent 核心推理引擎
实现：感知 → 规划 → 工具调用 → 结果解析 → 反馈循环
"""
import json
from typing import List, Dict, Callable, Optional

from .config import AgentConfig
from .llm_client import LLMClient
from ..tools.spatial_tools import ALL_TOOLS, TOOL_SCHEMAS


SYSTEM_PROMPT = """你是一个专业的 QGIS 地理信息系统智能助手（QGIS Agent）。
你能够理解用户的自然语言地理分析需求，并通过调用工具完成以下任务：
- 加载矢量/栅格/WMS图层
- 空间分析（缓冲区、裁剪、叠加、融合、重投影）
- 属性统计与面积计算
- 地图导出与报告生成
- 执行自定义 PyQGIS 代码

工作准则：
1. 先理解用户意图，必要时询问澄清
2. 将复杂任务分解为多个工具调用步骤
3. 每次工具调用后，分析结果并决定下一步
4. 出错时主动诊断原因并尝试修复
5. 最终用简洁的中文向用户解释结果
6. 路径使用绝对路径，坐标系使用 EPSG 代码

请始终用中文回复用户。"""


class AgentMessage:
    """对话消息"""
    def __init__(self, role: str, content: str, tool_calls=None, tool_call_id=None, name=None):
        self.role = role
        self.content = content
        self.tool_calls = tool_calls or []
        self.tool_call_id = tool_call_id
        self.name = name

    def to_dict(self) -> Dict:
        d = {"role": self.role, "content": self.content or ""}
        if self.tool_calls:
            d["tool_calls"] = [
                {"id": tc["id"], "type": "function",
                 "function": {"name": tc["name"], "arguments": json.dumps(tc["arguments"], ensure_ascii=False)}}
                for tc in self.tool_calls
            ]
        if self.tool_call_id:
            d["tool_call_id"] = self.tool_call_id
        if self.name:
            d["name"] = self.name
        return d


class QGISAgent:
    """QGIS Agent 核心类"""

    def __init__(self, iface, config: AgentConfig):
        self.iface = iface
        self.config = config
        self.llm = LLMClient(config)
        self.history: List[AgentMessage] = []
        self.tools = ALL_TOOLS.copy()

        # 回调注册
        self._on_thinking: Optional[Callable] = None    # 推理状态回调
        self._on_tool_call: Optional[Callable] = None   # 工具调用回调
        self._on_tool_result: Optional[Callable] = None # 工具结果回调
        self._on_response: Optional[Callable] = None    # 最终回复回调
        self._on_error: Optional[Callable] = None       # 错误回调
        self._confirm_execute: Optional[Callable] = None # 代码执行确认回调

    # ------------------------------------------------------------------ #
    #  回调注册                                                            #
    # ------------------------------------------------------------------ #
    def on_thinking(self, fn): self._on_thinking = fn
    def on_tool_call(self, fn): self._on_tool_call = fn
    def on_tool_result(self, fn): self._on_tool_result = fn
    def on_response(self, fn): self._on_response = fn
    def on_error(self, fn): self._on_error = fn
    def set_confirm_execute(self, fn): self._confirm_execute = fn

    # ------------------------------------------------------------------ #
    #  主入口                                                              #
    # ------------------------------------------------------------------ #
    def chat(self, user_input: str) -> str:
        """
        处理用户输入，返回最终回复文本
        推理循环：LLM → 工具调用 → 结果 → LLM → ... → 最终回复
        """
        if not self.config.is_configured:
            msg = "⚠️ 请先在设置中配置 LLM API Key 和模型名称。"
            if self._on_error:
                self._on_error(msg)
            return msg

        # 添加用户消息
        self.history.append(AgentMessage("user", user_input))

        iteration = 0
        while iteration < self.config.max_iterations:
            iteration += 1

            if self._on_thinking:
                self._on_thinking(f"[推理轮次 {iteration}] 正在思考...")

            # 构建消息列表
            messages = [m.to_dict() for m in self.history]

            try:
                response = self.llm.chat(
                    messages=messages,
                    system=SYSTEM_PROMPT,
                    tools=TOOL_SCHEMAS,
                )
            except Exception as e:
                err = f"LLM 调用失败: {str(e)}"
                if self._on_error:
                    self._on_error(err)
                return err

            content = response.get("content", "")
            tool_calls = response.get("tool_calls", [])

            # 无工具调用 → 最终回复
            if not tool_calls:
                self.history.append(AgentMessage("assistant", content))
                if self._on_response:
                    self._on_response(content)
                return content

            # 有工具调用 → 执行工具
            self.history.append(AgentMessage("assistant", content, tool_calls=tool_calls))

            for tc in tool_calls:
                tool_name = tc["name"]
                tool_args = tc["arguments"]
                tool_id = tc["id"]

                if self._on_tool_call:
                    self._on_tool_call(tool_name, tool_args)

                # 安全确认（代码执行类）
                if tool_name == "execute_python" and not self.config.enable_auto_execute:
                    if self._confirm_execute:
                        confirmed = self._confirm_execute(tool_args.get("code", ""))
                        if not confirmed:
                            result_str = "用户取消了代码执行。"
                            self.history.append(AgentMessage(
                                "tool", result_str, tool_call_id=tool_id, name=tool_name))
                            if self._on_tool_result:
                                self._on_tool_result(tool_name, result_str)
                            continue

                # 执行工具
                result_str = self._execute_tool(tool_name, tool_args)

                if self._on_tool_result:
                    self._on_tool_result(tool_name, result_str)

                self.history.append(AgentMessage(
                    "tool", result_str, tool_call_id=tool_id, name=tool_name))

        # 超出最大轮次
        final = "达到最大推理轮次，分析可能未完全完成，请重新描述您的需求。"
        if self._on_response:
            self._on_response(final)
        return final

    def _execute_tool(self, name: str, args: Dict) -> str:
        """执行工具并返回结果字符串"""
        fn = self.tools.get(name)
        if not fn:
            return f"工具 '{name}' 不存在"
        try:
            result = fn(**args)
            if hasattr(result, "to_dict"):
                return json.dumps(result.to_dict(), ensure_ascii=False, indent=2)
            return str(result)
        except Exception as e:
            return json.dumps({"success": False, "message": f"工具执行异常: {str(e)}"})

    def clear_history(self):
        """清除对话历史"""
        self.history.clear()

    def register_tool(self, name: str, fn: Callable, schema: Dict = None):
        """注册自定义工具"""
        self.tools[name] = fn
        if schema:
            TOOL_SCHEMAS.append(schema)
