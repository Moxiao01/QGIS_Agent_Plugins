# -*- coding: utf-8 -*-
"""QGIS Agent reasoning loop."""

import copy
import json
from typing import Any, Callable, Dict, List, Optional

from .config import AgentConfig
from .llm_client import LLMClient
from .run_logger import TaskRunLogger
from ..tools.spatial_tools import ALL_TOOLS, TOOL_SCHEMAS, set_runtime_context


SYSTEM_PROMPT = """你是一个专业的 QGIS 地理信息系统智能助手（QGIS Agent）。
你能够理解用户的自然语言地理分析需求，并通过调用工具完成以下任务：
- 加载矢量、栅格和 WMS 图层
- 空间分析（缓冲区、裁剪、叠加、融合、重投影、空间连接）
- 属性统计与面积计算
- 地图导出与 HTML 报告生成
- 在用户明确确认后执行自定义 PyQGIS 代码

工作准则：
1. 先理解用户意图；缺少输入图层、距离、字段或输出路径时先询问澄清。
2. 将复杂任务拆成多个工具调用，并在每次调用后检查 success 字段。
3. 不要捏造图层、字段、路径、坐标系或处理结果；优先调用 list_layers/get_layer_info 获取事实。
4. 路径使用绝对路径；涉及距离或面积时说明坐标系和单位。
5. 工具失败时解释原因，不要反复使用完全相同的错误参数。
6. execute_python 与任意 Processing 调用具有风险，只在必要时使用。
7. 最终用简洁中文总结完成内容、输出位置和任何限制。

请始终用中文回复用户。"""


class AgentMessage:
    """Provider-neutral message stored in the local conversation history."""

    def __init__(self, role: str, content: str, tool_calls=None, tool_call_id=None, name=None):
        self.role = role
        self.content = content
        self.tool_calls = tool_calls or []
        self.tool_call_id = tool_call_id
        self.name = name

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {"role": self.role, "content": self.content or ""}
        if self.tool_calls:
            data["tool_calls"] = [
                {
                    "id": call["id"],
                    "type": "function",
                    "function": {
                        "name": call["name"],
                        "arguments": json.dumps(call.get("arguments") or {}, ensure_ascii=False),
                    },
                }
                for call in self.tool_calls
            ]
        if self.tool_call_id:
            data["tool_call_id"] = self.tool_call_id
        if self.name:
            data["name"] = self.name
        return data


class QGISAgent:
    """LLM tool-calling loop with bounded memory and injectable tool execution."""

    def __init__(self, iface, config: AgentConfig):
        self.iface = iface
        self.config = config
        self.llm = LLMClient(config)
        self.history: List[AgentMessage] = []
        self.tools = ALL_TOOLS.copy()
        self.tool_schemas = copy.deepcopy(TOOL_SCHEMAS)
        self._apply_tool_policy()
        set_runtime_context(iface, config)

        self._on_thinking: Optional[Callable[[str], None]] = None
        self._on_tool_call: Optional[Callable[[str, Dict[str, Any]], None]] = None
        self._on_tool_result: Optional[Callable[[str, str], None]] = None
        self._on_response: Optional[Callable[[str], None]] = None
        self._on_error: Optional[Callable[[str], None]] = None
        self._confirm_execute: Optional[Callable[[str], bool]] = None
        self._tool_executor: Optional[Callable[[str, Dict[str, Any], Callable[..., Any]], Any]] = None
        self.run_logger = TaskRunLogger(config)
        self.last_run_log_path: Optional[str] = None


    def _apply_tool_policy(self) -> None:
        """Hide high-risk tools unless the user explicitly enables them."""
        disabled = set()
        if not self.config.enable_python_tool:
            disabled.add("execute_python")
        if not self.config.enable_generic_processing:
            disabled.add("run_processing_algorithm")
        if not disabled:
            return
        self.tool_schemas = [
            item for item in self.tool_schemas
            if (item.get("function") or item).get("name") not in disabled
        ]

    def refresh_tool_policy(self) -> None:
        """Rebuild built-in tool exposure after settings change."""
        custom_schemas = [
            copy.deepcopy(item) for item in self.tool_schemas
            if (item.get("function") or item).get("name") not in ALL_TOOLS
        ]
        self.tool_schemas = copy.deepcopy(TOOL_SCHEMAS) + custom_schemas
        self._apply_tool_policy()
        set_runtime_context(self.iface, self.config)

    def on_thinking(self, fn):
        self._on_thinking = fn

    def on_tool_call(self, fn):
        self._on_tool_call = fn

    def on_tool_result(self, fn):
        self._on_tool_result = fn

    def on_response(self, fn):
        self._on_response = fn

    def on_error(self, fn):
        self._on_error = fn

    def set_confirm_execute(self, fn):
        self._confirm_execute = fn

    def set_tool_executor(self, fn):
        """Set an optional dispatcher used to marshal QGIS work to the GUI thread."""
        self._tool_executor = fn

    @staticmethod
    def _emit(callback, *args) -> None:
        if callback:
            callback(*args)

    def chat(self, user_input: str) -> str:
        """Process one user turn and return the final assistant text."""
        self.last_run_log_path = None
        user_input = str(user_input or "").strip()
        if not user_input:
            return "请输入需要执行的地理分析任务。"
        if not self.config.is_configured:
            message = "⚠️ 请先在设置中配置 LLM API Key、API 地址和模型名称。"
            self._emit(self._on_error, message)
            return message

        self.last_run_log_path = self.run_logger.start(user_input)
        if not self.config.enable_memory:
            self.history.clear()
        self.history.append(AgentMessage("user", user_input))

        for iteration in range(1, self.config.max_iterations + 1):
            self._emit(self._on_thinking, f"[推理轮次 {iteration}] 正在思考...")
            self.run_logger.event(
                "llm_request",
                iteration=iteration,
                message_count=len(self.history),
                exposed_tools=len(self.tool_schemas),
            )
            try:
                response = self.llm.chat(
                    messages=[message.to_dict() for message in self.history],
                    system=SYSTEM_PROMPT,
                    tools=self.tool_schemas,
                )
            except Exception as exc:
                error = f"LLM 调用失败: {exc}"
                self._emit(self._on_error, error)
                self.run_logger.finish("failed", error=error)
                self._finish_turn()
                return error

            content = str(response.get("content") or "")
            tool_calls = response.get("tool_calls") or []
            self.run_logger.event(
                "llm_response",
                iteration=iteration,
                tool_call_count=len(tool_calls),
                usage=response.get("usage") or {},
                content=content,
            )
            if not tool_calls:
                final = content.strip() or "模型没有返回可显示的内容，请重试或更换模型。"
                self.history.append(AgentMessage("assistant", final))
                self._emit(self._on_response, final)
                self.run_logger.finish("success", response=final)
                self._finish_turn()
                return final

            normalized_calls = []
            for call in tool_calls:
                if not isinstance(call, dict) or not call.get("name"):
                    continue
                normalized_calls.append({
                    "id": str(call.get("id") or f"call_{iteration}_{len(normalized_calls)}"),
                    "name": str(call["name"]),
                    "arguments": call.get("arguments") if isinstance(call.get("arguments"), dict) else {},
                })
            if not normalized_calls:
                error = "模型返回了无效的工具调用。"
                self._emit(self._on_error, error)
                self.run_logger.finish("failed", error=error)
                self._finish_turn()
                return error

            self.history.append(AgentMessage("assistant", content, tool_calls=normalized_calls))
            for call in normalized_calls:
                name = call["name"]
                arguments = call["arguments"]
                tool_id = call["id"]
                self._emit(self._on_tool_call, name, arguments)
                self.run_logger.event(
                    "tool_call",
                    iteration=iteration,
                    tool_call_id=tool_id,
                    tool=name,
                    arguments=arguments,
                )

                if name == "execute_python" and not self.config.enable_python_tool:
                    result_text = json.dumps(
                        {"success": False, "message": "Python 执行工具未启用。"},
                        ensure_ascii=False,
                    )
                    self._record_tool_result(tool_id, name, result_text)
                    continue

                if name == "run_processing_algorithm" and not self.config.enable_generic_processing:
                    result_text = json.dumps(
                        {"success": False, "message": "通用 Processing 工具未启用。"},
                        ensure_ascii=False,
                    )
                    self._record_tool_result(tool_id, name, result_text)
                    continue

                if name == "execute_python" and not self.config.enable_auto_execute:
                    confirmed = False
                    if self._confirm_execute:
                        try:
                            confirmed = bool(self._confirm_execute(str(arguments.get("code", ""))))
                        except Exception:
                            confirmed = False
                    if not confirmed:
                        result_text = json.dumps(
                            {"success": False, "message": "用户未授权执行 Python 代码。"},
                            ensure_ascii=False,
                        )
                        self._record_tool_result(tool_id, name, result_text)
                        continue

                result_text = self._execute_tool(name, arguments)
                self._record_tool_result(tool_id, name, result_text)

        final = "达到最大推理轮次，任务可能未完全完成。请查看工具日志并缩小任务范围后重试。"
        self.history.append(AgentMessage("assistant", final))
        self._emit(self._on_response, final)
        self.run_logger.finish("iteration_limit", response=final)
        self._finish_turn()
        return final

    def _record_tool_result(self, tool_id: str, name: str, result_text: str) -> None:
        self.history.append(AgentMessage(
            "tool", result_text, tool_call_id=tool_id, name=name
        ))
        self._emit(self._on_tool_result, name, result_text)
        self.run_logger.tool_result(name, result_text)

    def _execute_tool(self, name: str, args: Dict[str, Any]) -> str:
        fn = self.tools.get(name)
        if not fn:
            return json.dumps({"success": False, "message": f"工具不存在: {name}"}, ensure_ascii=False)
        try:
            result = self._tool_executor(name, args, fn) if self._tool_executor else fn(**args)
            if hasattr(result, "to_dict"):
                result = result.to_dict()
            if isinstance(result, (dict, list, tuple)):
                return json.dumps(result, ensure_ascii=False, indent=2, default=str)
            return str(result)
        except TypeError as exc:
            return json.dumps(
                {"success": False, "message": f"工具参数错误: {exc}"},
                ensure_ascii=False,
            )
        except Exception as exc:
            return json.dumps(
                {"success": False, "message": f"工具执行异常: {exc}"},
                ensure_ascii=False,
            )

    def _finish_turn(self) -> None:
        if not self.config.enable_memory:
            self.history.clear()
            return
        self._trim_history()

    def _trim_history(self) -> None:
        """Keep recent complete user turns without splitting tool-call sequences."""
        limit = max(1, int(self.config.max_history))
        if len(self.history) <= limit:
            return
        starts = [index for index, message in enumerate(self.history) if message.role == "user"]
        if not starts:
            self.history = self.history[-limit:]
            return

        chosen_start = starts[-1]
        for start in reversed(starts[:-1]):
            if len(self.history) - start > limit:
                break
            chosen_start = start
        self.history = self.history[chosen_start:]

    def clear_history(self) -> None:
        self.history.clear()

    def register_tool(self, name: str, fn: Callable, schema: Optional[Dict[str, Any]] = None) -> None:
        """Register or replace a tool on this agent instance."""
        self.tools[name] = fn
        if not schema:
            return
        self.tool_schemas = [
            item for item in self.tool_schemas
            if (item.get("function") or item).get("name") != name
        ]
        self.tool_schemas.append(copy.deepcopy(schema))