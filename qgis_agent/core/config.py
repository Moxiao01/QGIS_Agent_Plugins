# -*- coding: utf-8 -*-
"""
Agent 配置管理
支持多种LLM后端配置
"""
import json
import os
from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class AgentConfig:
    """Agent配置数据类"""

    # LLM 后端配置
    llm_provider: str = "openai_compatible"   # openai_compatible | anthropic | ollama
    llm_api_key: str = ""
    llm_api_base: str = "https://api.openai.com/v1"   # 兼容OpenAI格式的任意端点
    llm_model: str = "gpt-4o"
    llm_temperature: float = 0.1
    llm_max_tokens: int = 4096

    # Agent 行为配置
    max_iterations: int = 10          # 最大推理轮次
    enable_auto_execute: bool = False  # 是否自动执行代码（不询问用户）
    enable_memory: bool = True         # 是否保留对话历史
    max_history: int = 20             # 保留的最大对话条数

    # 输出配置
    output_dir: str = ""              # 默认输出目录（空=用户主目录）
    default_crs: str = "EPSG:4326"   # 默认坐标参考系

    # 日志配置
    log_level: str = "INFO"           # DEBUG | INFO | WARNING | ERROR

    _config_path: str = field(default="", init=False, repr=False)

    def __post_init__(self):
        config_dir = os.path.join(os.path.expanduser("~"), ".qgis_agent")
        os.makedirs(config_dir, exist_ok=True)
        self._config_path = os.path.join(config_dir, "config.json")
        if self.output_dir == "":
            self.output_dir = os.path.join(os.path.expanduser("~"), "qgis_agent_output")
        os.makedirs(self.output_dir, exist_ok=True)
        self.load()

    def load(self):
        """从文件加载配置"""
        if os.path.exists(self._config_path):
            try:
                with open(self._config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for k, v in data.items():
                    if hasattr(self, k) and not k.startswith("_"):
                        setattr(self, k, v)
            except Exception:
                pass

    def save(self):
        """保存配置到文件"""
        data = {k: v for k, v in asdict(self).items() if not k.startswith("_")}
        with open(self._config_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    @property
    def is_configured(self) -> bool:
        """检查是否已完成基本配置"""
        if self.llm_provider == "ollama":
            return bool(self.llm_api_base and self.llm_model)
        return bool(self.llm_api_key and self.llm_model)
