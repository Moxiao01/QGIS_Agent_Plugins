import configparser
import io
import json
import os
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

from qgis_agent.core.agent import QGISAgent
from qgis_agent.core.config import AgentConfig
from qgis_agent.core.llm_client import LLMClient
from qgis_agent.tools.spatial_tools import (
    ALL_TOOLS, TOOL_SCHEMAS, CodeExecutionTools, ToolResult, _ensure_parent,
    set_runtime_context,
)


class FakeHTTPResponse:
    def __init__(self, payload):
        self.data = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self.data


class ConfigTests(unittest.TestCase):
    def test_round_trip_and_normalization(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "config.json")
            with patch.dict(os.environ, {"QGIS_AGENT_CONFIG_PATH": path}):
                config = AgentConfig(output_dir=os.path.join(directory, "out"))
                config.llm_api_key = " secret "
                config.llm_model = "model-a"
                config.max_iterations = 999
                config.save()
                loaded = AgentConfig(output_dir=os.path.join(directory, "other"))
            self.assertEqual(loaded.llm_api_key, "secret")
            self.assertEqual(loaded.llm_model, "model-a")
            self.assertEqual(loaded.max_iterations, 50)
            self.assertTrue(os.path.isdir(loaded.output_dir))
            self.assertTrue(loaded.restrict_output_paths)
            self.assertFalse(loaded.allow_overwrite)
            self.assertFalse(loaded.enable_python_tool)
            self.assertFalse(loaded.enable_generic_processing)

    def test_auto_execute_requires_python_tool(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "config.json")
            with patch.dict(os.environ, {"QGIS_AGENT_CONFIG_PATH": path}):
                config = AgentConfig(output_dir=os.path.join(directory, "out"))
            config.enable_python_tool = False
            config.enable_auto_execute = True
            config.normalize()
            self.assertFalse(config.enable_auto_execute)

    def test_invalid_json_uses_defaults_and_reports_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "config.json")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("{not-json")
            with patch.dict(os.environ, {"QGIS_AGENT_CONFIG_PATH": path}):
                config = AgentConfig(output_dir=os.path.join(directory, "out"))
            self.assertIsNotNone(config.load_error)
            self.assertEqual(config.llm_provider, "openai_compatible")


class LLMClientTests(unittest.TestCase):
    def make_config(self, directory, provider="openai_compatible"):
        path = os.path.join(directory, f"{provider}.json")
        with patch.dict(os.environ, {"QGIS_AGENT_CONFIG_PATH": path}):
            config = AgentConfig(output_dir=os.path.join(directory, "out"))
        config.llm_provider = provider
        config.llm_api_key = "key"
        config.llm_model = "model"
        config.llm_api_base = "http://localhost:9999/v1" if provider != "ollama" else "http://localhost:11434"
        return config

    def test_openai_tool_call_is_normalized(self):
        with tempfile.TemporaryDirectory() as directory:
            client = LLMClient(self.make_config(directory))
            response = {"choices": [{"message": {"content": None, "tool_calls": [{
                "id": "call-1", "function": {"name": "list_layers", "arguments": "{}"}
            }]}}], "usage": {"total_tokens": 10}}
            with patch("urllib.request.urlopen", return_value=FakeHTTPResponse(response)) as mocked:
                result = client.chat([{"role": "user", "content": "test"}], tools=[{"type": "function", "function": {"name": "list_layers"}}])
            self.assertEqual(result["tool_calls"][0]["name"], "list_layers")
            request = mocked.call_args.args[0]
            sent = json.loads(request.data.decode("utf-8"))
            self.assertEqual(sent["tool_choice"], "auto")
            self.assertTrue(request.full_url.endswith("/chat/completions"))

    def test_anthropic_converts_tool_results(self):
        messages = [
            {"role": "user", "content": "go"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "a", "type": "function", "function": {"name": "one", "arguments": "{}"}},
                {"id": "b", "type": "function", "function": {"name": "two", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "a", "name": "one", "content": "1"},
            {"role": "tool", "tool_call_id": "b", "name": "two", "content": "2"},
        ]
        converted = LLMClient._anthropic_messages(messages)
        self.assertEqual(converted[-1]["role"], "user")
        self.assertEqual(len(converted[-1]["content"]), 2)
        self.assertEqual(converted[-1]["content"][1]["tool_use_id"], "b")

    def test_ollama_sends_and_parses_tools(self):
        with tempfile.TemporaryDirectory() as directory:
            client = LLMClient(self.make_config(directory, "ollama"))
            response = {"message": {"content": "", "tool_calls": [{
                "function": {"name": "list_layers", "arguments": {}}
            }]}, "prompt_eval_count": 3, "eval_count": 4}
            with patch("urllib.request.urlopen", return_value=FakeHTTPResponse(response)) as mocked:
                result = client.chat([{"role": "user", "content": "test"}], tools=[{"type": "function", "function": {"name": "list_layers"}}])
            sent = json.loads(mocked.call_args.args[0].data.decode("utf-8"))
            self.assertIn("tools", sent)
            self.assertEqual(result["tool_calls"][0]["name"], "list_layers")
            self.assertEqual(result["usage"]["completion_tokens"], 4)

    def test_retryable_http_error_is_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self.make_config(directory)
            config.request_retries = 1
            config.retry_backoff_seconds = 0
            client = LLMClient(config)
            error = urllib.error.HTTPError(
                "http://localhost:9999/v1/chat/completions",
                429,
                "too many requests",
                {},
                io.BytesIO(b'{"error":"rate limit"}'),
            )
            response = {"choices": [{"message": {"content": "ok", "tool_calls": []}}]}
            with patch("urllib.request.urlopen", side_effect=[error, FakeHTTPResponse(response)]) as mocked:
                result = client.chat([{"role": "user", "content": "test"}])
            self.assertEqual(result["content"], "ok")
            self.assertEqual(mocked.call_count, 2)

    def test_non_retryable_http_error_fails_immediately(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self.make_config(directory)
            config.request_retries = 3
            config.retry_backoff_seconds = 0
            client = LLMClient(config)
            error = urllib.error.HTTPError(
                "http://localhost:9999/v1/chat/completions",
                400,
                "bad request",
                {},
                io.BytesIO(b'{"error":"bad request"}'),
            )
            with patch("urllib.request.urlopen", side_effect=error) as mocked:
                with self.assertRaisesRegex(RuntimeError, "HTTP 400"):
                    client.chat([{"role": "user", "content": "test"}])
            self.assertEqual(mocked.call_count, 1)


class SequenceLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


class AgentTests(unittest.TestCase):
    def make_agent(self, directory, memory=True, python_tool=False, processing_tool=False):
        path = os.path.join(directory, "agent.json")
        with patch.dict(os.environ, {"QGIS_AGENT_CONFIG_PATH": path}):
            config = AgentConfig(output_dir=os.path.join(directory, "out"))
        config.llm_api_key = "key"
        config.llm_model = "model"
        config.enable_memory = memory
        config.enable_python_tool = python_tool
        config.enable_generic_processing = processing_tool
        config.normalize()
        return QGISAgent(None, config)

    def test_tool_loop_and_serialization(self):
        with tempfile.TemporaryDirectory() as directory:
            agent = self.make_agent(directory)
            agent.tools = {"echo": lambda value: ToolResult(True, {"value": value}, "ok")}
            agent.tool_schemas = [{"type": "function", "function": {"name": "echo", "parameters": {"type": "object", "properties": {}}}}]
            agent.llm = SequenceLLM([
                {"content": "", "tool_calls": [{"id": "x", "name": "echo", "arguments": {"value": 7}}]},
                {"content": "完成", "tool_calls": []},
            ])
            result = agent.chat("run")
            self.assertEqual(result, "完成")
            self.assertEqual(agent.history[-2].role, "tool")
            self.assertIn('"value": 7', agent.history[-2].content)

    def test_python_is_denied_without_confirmation_callback(self):
        with tempfile.TemporaryDirectory() as directory:
            agent = self.make_agent(directory, python_tool=True)
            executed = []
            agent.tools = {"execute_python": lambda code: executed.append(code)}
            agent.tool_schemas = []
            agent.llm = SequenceLLM([
                {"content": "", "tool_calls": [{"id": "x", "name": "execute_python", "arguments": {"code": "print(1)"}}]},
                {"content": "未执行", "tool_calls": []},
            ])
            self.assertEqual(agent.chat("run"), "未执行")
            self.assertEqual(executed, [])
            self.assertIn("未授权", agent.history[-2].content)

    def test_risky_tools_are_hidden_and_rejected_by_default(self):
        with tempfile.TemporaryDirectory() as directory:
            agent = self.make_agent(directory)
            schema_names = {(item.get("function") or item)["name"] for item in agent.tool_schemas}
            self.assertNotIn("execute_python", schema_names)
            self.assertNotIn("run_processing_algorithm", schema_names)
            executed = []
            agent.tools["execute_python"] = lambda code: executed.append(code)
            agent.llm = SequenceLLM([
                {"content": "", "tool_calls": [{"id": "x", "name": "execute_python", "arguments": {"code": "print(1)"}}]},
                {"content": "disabled", "tool_calls": []},
            ])
            self.assertEqual(agent.chat("run"), "disabled")
            self.assertEqual(executed, [])
            self.assertIn("\u672a\u542f\u7528", agent.history[-2].content)

    def test_task_log_records_tool_result_and_acceptance(self):
        with tempfile.TemporaryDirectory() as directory:
            agent = self.make_agent(directory)
            agent.tools = {"echo": lambda value: ToolResult(True, {"value": value}, "ok")}
            agent.tool_schemas = [{"type": "function", "function": {"name": "echo"}}]
            agent.llm = SequenceLLM([
                {"content": "", "tool_calls": [{"id": "x", "name": "echo", "arguments": {"value": 7}}]},
                {"content": "done", "tool_calls": []},
            ])
            self.assertEqual(agent.chat("run"), "done")
            self.assertTrue(os.path.isfile(agent.last_run_log_path))
            with open(agent.last_run_log_path, "r", encoding="utf-8") as handle:
                events = [json.loads(line) for line in handle if line.strip()]
            self.assertEqual(events[0]["event"], "task_started")
            self.assertTrue(any(item["event"] == "tool_call" for item in events))
            tool_result = next(item for item in events if item["event"] == "tool_result")
            self.assertTrue(tool_result["success"])
            finished = events[-1]
            self.assertEqual(finished["event"], "task_finished")
            self.assertTrue(finished["accepted"])
            self.assertEqual(finished["tool_successes"], 1)
            self.assertEqual(finished["tool_failures"], 0)

    def test_task_log_rejects_run_with_failed_tool(self):
        with tempfile.TemporaryDirectory() as directory:
            agent = self.make_agent(directory)
            agent.tools = {"fail": lambda: ToolResult(False, {"error_code": "EXPECTED"}, "failed")}
            agent.tool_schemas = [{"type": "function", "function": {"name": "fail"}}]
            agent.llm = SequenceLLM([
                {"content": "", "tool_calls": [{"id": "x", "name": "fail", "arguments": {}}]},
                {"content": "failure reported", "tool_calls": []},
            ])
            self.assertEqual(agent.chat("run"), "failure reported")
            with open(agent.last_run_log_path, "r", encoding="utf-8") as handle:
                events = [json.loads(line) for line in handle if line.strip()]
            tool_result = next(item for item in events if item["event"] == "tool_result")
            self.assertFalse(tool_result["success"])
            finished = events[-1]
            self.assertEqual(finished["event"], "task_finished")
            self.assertFalse(finished["accepted"])
            self.assertEqual(finished["tool_successes"], 0)
            self.assertEqual(finished["tool_failures"], 1)

    def test_memory_disabled_clears_history(self):
        with tempfile.TemporaryDirectory() as directory:
            agent = self.make_agent(directory, memory=False)
            agent.llm = SequenceLLM([{"content": "ok", "tool_calls": []}])
            agent.chat("hello")
            self.assertEqual(agent.history, [])

    def test_custom_schema_does_not_mutate_global_schemas(self):
        with tempfile.TemporaryDirectory() as directory:
            before = len(TOOL_SCHEMAS)
            agent = self.make_agent(directory)
            agent.register_tool("custom", lambda: None, {"type": "function", "function": {"name": "custom"}})
            self.assertEqual(len(TOOL_SCHEMAS), before)
            names = {(item.get("function") or item).get("name") for item in agent.tool_schemas}
            self.assertIn("custom", names)
            self.assertNotIn("execute_python", names)


class OutputPolicyTests(unittest.TestCase):
    def tearDown(self):
        set_runtime_context(None, None)

    def make_config(self, directory):
        path = os.path.join(directory, "config.json")
        with patch.dict(os.environ, {"QGIS_AGENT_CONFIG_PATH": path}):
            return AgentConfig(output_dir=os.path.join(directory, "out"))

    def test_relative_output_is_resolved_under_output_dir(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self.make_config(directory)
            set_runtime_context(None, config)
            resolved = _ensure_parent(os.path.join("nested", "result.gpkg"))
            self.assertEqual(resolved, os.path.join(config.output_dir, "nested", "result.gpkg"))
            self.assertTrue(os.path.isdir(os.path.dirname(resolved)))

    def test_output_escape_and_overwrite_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self.make_config(directory)
            set_runtime_context(None, config)
            outside = os.path.join(directory, "outside.gpkg")
            with self.assertRaises(ValueError):
                _ensure_parent(outside)

            existing = os.path.join(config.output_dir, "existing.gpkg")
            with open(existing, "w", encoding="utf-8") as handle:
                handle.write("x")
            with self.assertRaises(FileExistsError):
                _ensure_parent(existing)
            config.allow_overwrite = True
            self.assertEqual(_ensure_parent(existing), existing)


class SafetyToolPolicyTests(unittest.TestCase):
    def tearDown(self):
        set_runtime_context(None, None)

    def make_config(self, directory):
        path = os.path.join(directory, "config.json")
        with patch.dict(os.environ, {"QGIS_AGENT_CONFIG_PATH": path}):
            return AgentConfig(output_dir=os.path.join(directory, "out"))

    def test_python_direct_call_requires_enabled_runtime_config(self):
        set_runtime_context(None, None)
        result = CodeExecutionTools.execute_python("print(1)")
        self.assertFalse(result.success)
        self.assertIn("未启用", result.message)

    def test_processing_direct_call_checks_enablement_before_qgis(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self.make_config(directory)
            set_runtime_context(None, config)
            result = CodeExecutionTools.run_processing_algorithm("native:buffer", {})
            self.assertFalse(result.success)
            self.assertIn("未启用", result.message)

    def test_processing_empty_allowlist_denies_algorithm(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self.make_config(directory)
            config.enable_generic_processing = True
            config.allowed_processing_algorithms = []
            set_runtime_context(None, config)
            result = CodeExecutionTools.run_processing_algorithm("native:buffer", {})
            self.assertFalse(result.success)
            self.assertEqual(result.data["error_code"], "PROCESSING_ALGORITHM_NOT_ALLOWED")


class PluginPackageTests(unittest.TestCase):
    def test_qgis_plugin_package_has_required_metadata_and_assets(self):
        plugin_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "qgis_agent"))
        metadata_path = os.path.join(plugin_dir, "metadata.txt")
        parser = configparser.ConfigParser(interpolation=None)
        self.assertTrue(parser.read(metadata_path, encoding="utf-8"))
        self.assertIn("general", parser)
        metadata = parser["general"]
        for key in ("name", "version", "qgisMinimumVersion", "description", "icon"):
            self.assertTrue(metadata.get(key), key)
        self.assertEqual(metadata.get("server"), "False")
        self.assertTrue(os.path.isfile(os.path.join(plugin_dir, metadata["icon"])))
        self.assertTrue(os.path.isfile(os.path.join(plugin_dir, "__init__.py")))
        self.assertTrue(os.path.isfile(os.path.join(plugin_dir, "plugin.py")))


class EvaluationDatasetTests(unittest.TestCase):
    def test_task_cases_have_unique_ids_and_expected_outcomes(self):
        data_path = os.path.join(os.path.dirname(__file__), "data", "task_cases.json")
        with open(data_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        cases = payload["cases"]
        ids = [item["id"] for item in cases]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertGreaterEqual(len(cases), 7)
        complete = next(item for item in cases if item["category"] == "complete_task")
        self.assertGreaterEqual(len(complete["expected_sequence"]), 4)
        self.assertTrue(complete["acceptance"]["output_exists"])


class RegistryTests(unittest.TestCase):
    def test_every_registered_tool_has_a_schema(self):
        schema_names = {(item.get("function") or item)["name"] for item in TOOL_SCHEMAS}
        self.assertEqual(set(ALL_TOOLS), schema_names)
        self.assertEqual(len(ALL_TOOLS), 17)


if __name__ == "__main__":
    unittest.main()