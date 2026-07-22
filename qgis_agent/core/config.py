# -*- coding: utf-8 -*-
"""QGIS Agent configuration persistence and validation."""

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from typing import Optional


_SUPPORTED_PROVIDERS = {"openai_compatible", "anthropic", "ollama"}


@dataclass
class AgentConfig:
    """Runtime configuration for the LLM client and the agent."""

    llm_provider: str = "openai_compatible"
    llm_api_key: str = ""
    llm_api_base: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o"
    llm_temperature: float = 0.1
    llm_max_tokens: int = 4096
    request_timeout: int = 120
    request_retries: int = 2
    retry_backoff_seconds: float = 1.0

    max_iterations: int = 10
    tool_execution_timeout: int = 300
    enable_python_tool: bool = False
    enable_auto_execute: bool = False
    enable_generic_processing: bool = False
    allowed_processing_algorithms: list = field(default_factory=lambda: [
        "native:buffer",
        "native:clip",
        "native:intersection",
        "native:dissolve",
        "native:reprojectlayer",
        "native:joinattributesbylocation",
        "native:fixgeometries",
    ])
    enable_memory: bool = True
    max_history: int = 20

    output_dir: str = ""
    restrict_output_paths: bool = True
    allow_overwrite: bool = False
    default_crs: str = "EPSG:4326"
    log_level: str = "INFO"
    enable_task_logging: bool = True

    _config_path: str = field(default="", init=False, repr=False)
    _load_error: Optional[str] = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        override = os.environ.get("QGIS_AGENT_CONFIG_PATH", "").strip()
        if override:
            self._config_path = os.path.abspath(os.path.expanduser(override))
            config_dir = os.path.dirname(self._config_path)
        else:
            config_dir = os.path.join(os.path.expanduser("~"), ".qgis_agent")
            self._config_path = os.path.join(config_dir, "config.json")

        if config_dir:
            os.makedirs(config_dir, exist_ok=True)
        self.load()
        self.normalize()

    @property
    def load_error(self) -> Optional[str]:
        """Return the last configuration load error, if any."""
        return self._load_error

    def normalize(self) -> None:
        """Normalize values loaded from disk or edited by the settings UI."""
        if self.llm_provider not in _SUPPORTED_PROVIDERS:
            self.llm_provider = "openai_compatible"

        self.llm_api_key = str(self.llm_api_key or "").strip()
        self.llm_api_base = str(self.llm_api_base or "").strip().rstrip("/")
        self.llm_model = str(self.llm_model or "").strip()
        self.default_crs = str(self.default_crs or "EPSG:4326").strip() or "EPSG:4326"
        self.log_level = str(self.log_level or "INFO").upper()

        self.llm_temperature = min(2.0, max(0.0, float(self.llm_temperature)))
        self.llm_max_tokens = min(131072, max(128, int(self.llm_max_tokens)))
        self.request_timeout = min(600, max(5, int(self.request_timeout)))
        self.request_retries = min(5, max(0, int(self.request_retries)))
        self.retry_backoff_seconds = min(30.0, max(0.0, float(self.retry_backoff_seconds)))
        self.max_iterations = min(50, max(1, int(self.max_iterations)))
        self.tool_execution_timeout = min(3600, max(5, int(self.tool_execution_timeout)))
        self.max_history = min(200, max(1, int(self.max_history)))
        self.enable_python_tool = bool(self.enable_python_tool)
        self.enable_auto_execute = bool(self.enable_auto_execute) and self.enable_python_tool
        self.enable_generic_processing = bool(self.enable_generic_processing)
        self.enable_memory = bool(self.enable_memory)
        self.restrict_output_paths = bool(self.restrict_output_paths)
        self.allow_overwrite = bool(self.allow_overwrite)
        self.enable_task_logging = bool(self.enable_task_logging)
        if not isinstance(self.allowed_processing_algorithms, (list, tuple, set)):
            self.allowed_processing_algorithms = []
        self.allowed_processing_algorithms = sorted({
            str(item).strip() for item in self.allowed_processing_algorithms if str(item).strip()
        })

        if not self.output_dir:
            self.output_dir = os.path.join(os.path.expanduser("~"), "qgis_agent_output")
        self.output_dir = os.path.abspath(
            os.path.expandvars(os.path.expanduser(str(self.output_dir)))
        )
        os.makedirs(self.output_dir, exist_ok=True)

    def load(self) -> None:
        """Load known configuration keys from disk."""
        self._load_error = None
        if not os.path.exists(self._config_path):
            return

        try:
            with open(self._config_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if not isinstance(data, dict):
                raise ValueError("配置文件根节点必须是 JSON 对象")
            allowed = {key for key in asdict(self) if not key.startswith("_")}
            for key, value in data.items():
                if key in allowed:
                    setattr(self, key, value)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            self._load_error = str(exc)

    def save(self) -> None:
        """Validate and atomically save the configuration."""
        self.normalize()
        data = {key: value for key, value in asdict(self).items() if not key.startswith("_")}
        config_dir = os.path.dirname(self._config_path) or os.curdir
        os.makedirs(config_dir, exist_ok=True)

        fd, temp_path = tempfile.mkstemp(prefix="config-", suffix=".tmp", dir=config_dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            try:
                os.chmod(temp_path, 0o600)
            except OSError:
                pass
            os.replace(temp_path, self._config_path)
        finally:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass

    @property
    def is_configured(self) -> bool:
        """Return whether the selected backend has its required fields."""
        if self.llm_provider == "ollama":
            return bool(self.llm_api_base and self.llm_model)
        return bool(self.llm_api_key and self.llm_model)