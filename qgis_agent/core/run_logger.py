# -*- coding: utf-8 -*-
"""Local JSONL task tracing for reproducible QGIS Agent runs."""

import json
import os
import threading
import time
import uuid
from datetime import datetime
from typing import Any, Dict, Optional


_SENSITIVE_KEYS = {
    "api_key", "llm_api_key", "authorization", "password", "secret",
    "access_token", "refresh_token",
}


def _safe_value(value: Any, depth: int = 0) -> Any:
    """Make event data serializable, bounded, and free of common secrets."""
    if depth > 6:
        return "<max-depth>"
    if isinstance(value, dict):
        result = {}
        for key, item in list(value.items())[:200]:
            key_text = str(key)
            if key_text.casefold() in _SENSITIVE_KEYS:
                result[key_text] = "<redacted>"
            else:
                result[key_text] = _safe_value(item, depth + 1)
        return result
    if isinstance(value, (list, tuple, set)):
        return [_safe_value(item, depth + 1) for item in list(value)[:200]]
    if isinstance(value, str):
        return value if len(value) <= 20000 else value[:20000] + "<truncated>"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)


class TaskRunLogger:
    """Write one local JSONL file per user task without affecting execution."""

    def __init__(self, config):
        self.config = config
        self.run_id = ""
        self.path: Optional[str] = None
        self.started_at = 0.0
        self.tool_successes = 0
        self.tool_failures = 0
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return bool(getattr(self.config, "enable_task_logging", True))

    def start(self, user_input: str) -> Optional[str]:
        self.run_id = uuid.uuid4().hex
        self.path = None
        self.started_at = time.monotonic()
        self.tool_successes = 0
        self.tool_failures = 0
        if not self.enabled:
            return None
        try:
            log_dir = os.path.join(self.config.output_dir, "logs")
            os.makedirs(log_dir, exist_ok=True)
            stamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%z")
            self.path = os.path.join(log_dir, f"task_{stamp}_{self.run_id[:8]}.jsonl")
            self.event(
                "task_started",
                user_input=user_input,
                provider=getattr(self.config, "llm_provider", ""),
                model=getattr(self.config, "llm_model", ""),
            )
        except OSError:
            self.path = None
        return self.path

    def event(self, event: str, **data: Any) -> None:
        if not self.enabled or not self.path:
            return
        record: Dict[str, Any] = {
            "timestamp": datetime.now().astimezone().isoformat(timespec="milliseconds"),
            "run_id": self.run_id,
            "event": event,
            "elapsed_ms": round(max(0.0, time.monotonic() - self.started_at) * 1000),
        }
        record.update(_safe_value(data))
        line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
        try:
            with self._lock:
                with open(self.path, "a", encoding="utf-8") as handle:
                    handle.write(line)
        except OSError:
            pass

    def tool_result(self, name: str, result_text: str) -> None:
        success = False
        parsed: Any = result_text
        try:
            parsed = json.loads(result_text)
            if isinstance(parsed, dict):
                success = bool(parsed.get("success", False))
        except (TypeError, json.JSONDecodeError):
            success = False
        if success:
            self.tool_successes += 1
        else:
            self.tool_failures += 1
        self.event("tool_result", tool=name, success=success, result=parsed)

    def finish(self, outcome: str, response: str = "", error: str = "") -> None:
        accepted = outcome == "success" and self.tool_failures == 0
        self.event(
            "task_finished",
            outcome=outcome,
            accepted=accepted,
            tool_successes=self.tool_successes,
            tool_failures=self.tool_failures,
            response=response,
            error=error,
        )
