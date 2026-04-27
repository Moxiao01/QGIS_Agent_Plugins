# -*- coding: utf-8 -*-
"""
LLM 客户端
支持 OpenAI兼容接口 / Anthropic / Ollama
"""
import json
import urllib.request
import urllib.error
from typing import List, Dict, Optional, Generator

from .config import AgentConfig


class LLMClient:
    """统一LLM客户端，支持多后端"""

    def __init__(self, config: AgentConfig):
        self.config = config

    def chat(
        self,
        messages: List[Dict],
        system: str = "",
        tools: Optional[List[Dict]] = None,
        stream: bool = False,
    ) -> Dict:
        """
        发送对话请求
        返回: {"content": str, "tool_calls": list, "usage": dict}
        """
        provider = self.config.llm_provider
        if provider == "anthropic":
            return self._chat_anthropic(messages, system, tools)
        elif provider == "ollama":
            return self._chat_ollama(messages, system, tools)
        else:
            return self._chat_openai_compatible(messages, system, tools)

    # ------------------------------------------------------------------ #
    #  OpenAI-compatible  (默认，兼容 DeepSeek/Qwen/Gemini/Moonshot 等)   #
    # ------------------------------------------------------------------ #
    def _chat_openai_compatible(self, messages, system, tools):
        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.extend(messages)

        payload = {
            "model": self.config.llm_model,
            "messages": msgs,
            "temperature": self.config.llm_temperature,
            "max_tokens": self.config.llm_max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        url = self.config.llm_api_base.rstrip("/") + "/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config.llm_api_key}",
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                result = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8")
            raise RuntimeError(f"LLM API 错误 {e.code}: {body}")

        choice = result["choices"][0]
        msg = choice["message"]

        tool_calls = []
        if msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                tool_calls.append({
                    "id": tc["id"],
                    "name": tc["function"]["name"],
                    "arguments": json.loads(tc["function"]["arguments"]),
                })

        return {
            "content": msg.get("content") or "",
            "tool_calls": tool_calls,
            "usage": result.get("usage", {}),
        }

    # ------------------------------------------------------------------ #
    #  Anthropic                                                           #
    # ------------------------------------------------------------------ #
    def _chat_anthropic(self, messages, system, tools):
        payload = {
            "model": self.config.llm_model,
            "max_tokens": self.config.llm_max_tokens,
            "messages": messages,
        }
        if system:
            payload["system"] = system
        if tools:
            # 转换为 Anthropic tool 格式
            anth_tools = []
            for t in tools:
                fn = t.get("function", t)
                anth_tools.append({
                    "name": fn["name"],
                    "description": fn.get("description", ""),
                    "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
                })
            payload["tools"] = anth_tools

        url = "https://api.anthropic.com/v1/messages"
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.config.llm_api_key,
            "anthropic-version": "2023-06-01",
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                result = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8")
            raise RuntimeError(f"Anthropic API 错误 {e.code}: {body}")

        content_text = ""
        tool_calls = []
        for block in result.get("content", []):
            if block["type"] == "text":
                content_text += block["text"]
            elif block["type"] == "tool_use":
                tool_calls.append({
                    "id": block["id"],
                    "name": block["name"],
                    "arguments": block["input"],
                })

        return {
            "content": content_text,
            "tool_calls": tool_calls,
            "usage": result.get("usage", {}),
        }

    # ------------------------------------------------------------------ #
    #  Ollama (本地模型)                                                   #
    # ------------------------------------------------------------------ #
    def _chat_ollama(self, messages, system, tools):
        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.extend(messages)

        payload = {
            "model": self.config.llm_model,
            "messages": msgs,
            "stream": False,
            "options": {"temperature": self.config.llm_temperature},
        }

        url = self.config.llm_api_base.rstrip("/") + "/api/chat"
        headers = {"Content-Type": "application/json"}
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                result = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8")
            raise RuntimeError(f"Ollama API 错误 {e.code}: {body}")

        content = result.get("message", {}).get("content", "")
        return {"content": content, "tool_calls": [], "usage": {}}
