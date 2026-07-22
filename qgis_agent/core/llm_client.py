# -*- coding: utf-8 -*-
"""Dependency-free LLM client for OpenAI-compatible, Anthropic and Ollama APIs."""

import json
import socket
import time
import urllib.error
import urllib.request
import uuid
from typing import Any, Dict, List, Optional

from .config import AgentConfig


class LLMClient:
    """Small HTTP client that normalizes different providers to one response shape."""

    def __init__(self, config: AgentConfig):
        self.config = config

    def chat(
        self,
        messages: List[Dict[str, Any]],
        system: str = "",
        tools: Optional[List[Dict[str, Any]]] = None,
        stream: bool = False,
    ) -> Dict[str, Any]:
        """Send a chat request and return content, tool calls and usage."""
        if stream:
            raise ValueError("当前版本尚未开放流式返回，请使用 stream=False")

        provider = self.config.llm_provider
        if provider == "anthropic":
            return self._chat_anthropic(messages, system, tools)
        if provider == "ollama":
            return self._chat_ollama(messages, system, tools)
        return self._chat_openai_compatible(messages, system, tools)

    def _post_json(self, url: str, payload: Dict[str, Any], headers: Dict[str, str], label: str) -> Dict[str, Any]:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        retries = max(0, int(getattr(self.config, "request_retries", 0)))
        backoff = max(0.0, float(getattr(self.config, "retry_backoff_seconds", 1.0)))
        retryable_statuses = {429, 500, 502, 503, 504}
        raw = ""

        for attempt in range(retries + 1):
            request = urllib.request.Request(url, data=data, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(request, timeout=self.config.request_timeout) as response:
                    raw = response.read().decode("utf-8")
                break
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")
                if exc.code not in retryable_statuses or attempt >= retries:
                    raise RuntimeError(f"{label} HTTP {exc.code}: {body[:2000]}") from exc
                delay = backoff * (2 ** attempt)
                retry_after = (exc.headers or {}).get("Retry-After") if exc.headers is not None else None
                if retry_after:
                    try:
                        delay = max(delay, min(60.0, float(retry_after)))
                    except (TypeError, ValueError):
                        pass
                if delay:
                    time.sleep(delay)
            except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
                if attempt >= retries:
                    reason = getattr(exc, "reason", exc)
                    raise RuntimeError(f"{label} 连接失败: {reason}") from exc
                delay = backoff * (2 ** attempt)
                if delay:
                    time.sleep(delay)

        try:
            result = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"{label} 返回了无效 JSON: {raw[:500]}") from exc
        if not isinstance(result, dict):
            raise RuntimeError(f"{label} 返回格式异常")
        return result

    @staticmethod
    def _endpoint(base: str, suffix: str) -> str:
        base = (base or "").strip().rstrip("/")
        if not base:
            raise RuntimeError("API Base URL 不能为空")
        if base.endswith(suffix):
            return base
        return base + suffix

    @staticmethod
    def _parse_arguments(value: Any, tool_name: str) -> Dict[str, Any]:
        if value in (None, ""):
            return {}
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"工具 {tool_name} 的参数不是有效 JSON: {value[:300]}") from exc
            if isinstance(parsed, dict):
                return parsed
        raise RuntimeError(f"工具 {tool_name} 的参数必须是 JSON 对象")

    def _chat_openai_compatible(self, messages, system, tools):
        request_messages = []
        if system:
            request_messages.append({"role": "system", "content": system})
        request_messages.extend(messages)

        payload = {
            "model": self.config.llm_model,
            "messages": request_messages,
            "temperature": self.config.llm_temperature,
            "max_tokens": self.config.llm_max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        result = self._post_json(
            self._endpoint(self.config.llm_api_base, "/chat/completions"),
            payload,
            {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.config.llm_api_key}",
            },
            "LLM API",
        )
        try:
            message = result["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"LLM API 返回缺少 choices[0].message: {str(result)[:500]}") from exc

        tool_calls = []
        for item in message.get("tool_calls") or []:
            function = item.get("function") or {}
            name = function.get("name", "")
            if not name:
                raise RuntimeError("LLM 返回了缺少名称的工具调用")
            tool_calls.append({
                "id": item.get("id") or f"call_{uuid.uuid4().hex}",
                "name": name,
                "arguments": self._parse_arguments(function.get("arguments"), name),
            })
        return {
            "content": message.get("content") or "",
            "tool_calls": tool_calls,
            "usage": result.get("usage") or {},
        }

    @staticmethod
    def _anthropic_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        converted: List[Dict[str, Any]] = []
        for message in messages:
            role = message.get("role")
            if role == "system":
                continue
            if role == "assistant":
                blocks: List[Dict[str, Any]] = []
                if message.get("content"):
                    blocks.append({"type": "text", "text": str(message["content"])})
                for call in message.get("tool_calls") or []:
                    function = call.get("function") or {}
                    arguments = function.get("arguments", {})
                    if isinstance(arguments, str):
                        try:
                            arguments = json.loads(arguments)
                        except json.JSONDecodeError:
                            arguments = {}
                    blocks.append({
                        "type": "tool_use",
                        "id": call.get("id") or f"toolu_{uuid.uuid4().hex}",
                        "name": function.get("name", ""),
                        "input": arguments if isinstance(arguments, dict) else {},
                    })
                converted.append({"role": "assistant", "content": blocks or [{"type": "text", "text": ""}]})
            elif role == "tool":
                block = {
                    "type": "tool_result",
                    "tool_use_id": message.get("tool_call_id", ""),
                    "content": str(message.get("content") or ""),
                }
                if converted and converted[-1]["role"] == "user" and isinstance(converted[-1]["content"], list):
                    converted[-1]["content"].append(block)
                else:
                    converted.append({"role": "user", "content": [block]})
            elif role == "user":
                converted.append({"role": "user", "content": str(message.get("content") or "")})
        return converted

    def _chat_anthropic(self, messages, system, tools):
        payload: Dict[str, Any] = {
            "model": self.config.llm_model,
            "max_tokens": self.config.llm_max_tokens,
            "temperature": self.config.llm_temperature,
            "messages": self._anthropic_messages(messages),
        }
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = [
                {
                    "name": (item.get("function") or item)["name"],
                    "description": (item.get("function") or item).get("description", ""),
                    "input_schema": (item.get("function") or item).get(
                        "parameters", {"type": "object", "properties": {}}
                    ),
                }
                for item in tools
            ]

        base = self.config.llm_api_base
        if not base or "openai.com" in base:
            base = "https://api.anthropic.com/v1"
        result = self._post_json(
            self._endpoint(base, "/messages"),
            payload,
            {
                "Content-Type": "application/json",
                "x-api-key": self.config.llm_api_key,
                "anthropic-version": "2023-06-01",
            },
            "Anthropic API",
        )

        content_text = ""
        tool_calls = []
        for block in result.get("content") or []:
            if block.get("type") == "text":
                content_text += block.get("text", "")
            elif block.get("type") == "tool_use":
                name = block.get("name", "")
                tool_calls.append({
                    "id": block.get("id") or f"toolu_{uuid.uuid4().hex}",
                    "name": name,
                    "arguments": self._parse_arguments(block.get("input"), name),
                })
        return {"content": content_text, "tool_calls": tool_calls, "usage": result.get("usage") or {}}

    @staticmethod
    def _ollama_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        converted = []
        for message in messages:
            item = {"role": message.get("role", "user"), "content": str(message.get("content") or "")}
            if message.get("name"):
                item["tool_name"] = message["name"]
            calls = []
            for call in message.get("tool_calls") or []:
                function = call.get("function") or {}
                arguments = function.get("arguments", {})
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except json.JSONDecodeError:
                        arguments = {}
                calls.append({"function": {"name": function.get("name", ""), "arguments": arguments}})
            if calls:
                item["tool_calls"] = calls
            converted.append(item)
        return converted

    def _chat_ollama(self, messages, system, tools):
        request_messages = []
        if system:
            request_messages.append({"role": "system", "content": system})
        request_messages.extend(self._ollama_messages(messages))
        payload: Dict[str, Any] = {
            "model": self.config.llm_model,
            "messages": request_messages,
            "stream": False,
            "options": {
                "temperature": self.config.llm_temperature,
                "num_predict": self.config.llm_max_tokens,
            },
        }
        if tools:
            payload["tools"] = tools

        result = self._post_json(
            self._endpoint(self.config.llm_api_base, "/api/chat"),
            payload,
            {"Content-Type": "application/json"},
            "Ollama API",
        )
        message = result.get("message") or {}
        tool_calls = []
        for item in message.get("tool_calls") or []:
            function = item.get("function") or {}
            name = function.get("name", "")
            tool_calls.append({
                "id": item.get("id") or f"call_{uuid.uuid4().hex}",
                "name": name,
                "arguments": self._parse_arguments(function.get("arguments"), name),
            })
        usage = {
            "prompt_tokens": result.get("prompt_eval_count", 0),
            "completion_tokens": result.get("eval_count", 0),
        }
        return {"content": message.get("content") or "", "tool_calls": tool_calls, "usage": usage}