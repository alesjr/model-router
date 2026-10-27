import contextlib
import importlib.util
import io
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROUTER_PATH = Path(__file__).resolve().parents[1] / "model-router" / "scripts" / "router.py"
SPEC = importlib.util.spec_from_file_location("model_router_router_telemetry", ROUTER_PATH)
if SPEC is None or SPEC.loader is None:
    raise ImportError(f"Could not load router from {ROUTER_PATH}")
router = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(router)


def task(**overrides):
    value = {
        "id": "task-telemetry", "task_type": "implementation", "complexity": "medium",
        "instructions": "Perform synthetic worker task.",
        "acceptance_criteria": ["Worker result is preserved."],
    }
    value.update(overrides)
    return value


def policy_text():
    return '''
cost_order = ["gpt-6-luna", "gpt-6.1-sol", "gpt-6-astra"]
[complexity_profiles]
low = "economy"
medium = "standard"
high = "strong"
[profiles.economy]
model = "gpt-6-luna"
reasoning_effort = "low"
allowed_efforts = ["low", "medium", "high"]
[profiles.standard]
model = "gpt-6.1-sol"
reasoning_effort = "medium"
allowed_efforts = ["low", "medium", "high"]
[profiles.strong]
model = "gpt-6-astra"
reasoning_effort = "high"
allowed_efforts = ["low", "medium", "high"]
'''


class RouterTelemetryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.cwd = self.root / "project"
        self.cwd.mkdir()
        self.xdg = self.root / "xdg-config"
        (self.xdg / "model-router").mkdir(parents=True)
        (self.xdg / "model-router" / "settings.json").write_text(
            json.dumps({"telemetry": {"enabled": True}}), encoding="utf-8")
        (self.cwd / ".model-router.json").write_text(
            json.dumps({"telemetry": {"enabled": True}}), encoding="utf-8")
        self.home = self.root / "home"
        self.home.mkdir()
        settings_environment = patch.dict(os.environ, {
            "XDG_CONFIG_HOME": str(self.xdg), "HOME": str(self.home),
        })
        settings_environment.start()
        self.addCleanup(settings_environment.stop)
        self.policy_path = self.root / "policy.toml"
        self.policy_path.write_text(policy_text(), encoding="utf-8")
        self.policy = router.load_policy(self.policy_path)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        codex_home = self.root / "codex-home"
        codex_home.mkdir()
        environment = patch.dict(os.environ, {"CODEX_HOME": str(codex_home)})
        environment.start()
        self.addCleanup(environment.stop)

    def fake_codex(self, body):
        executable = self.bin / "codex"
        executable.write_text("#!/usr/bin/env python3\n" + body, encoding="utf-8")
        executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
        return executable

    def run_with_fake(self, resolved, timeout=5):
        with patch.dict(os.environ, {"PATH": str(self.bin) + os.pathsep + os.environ.get("PATH", "")}):
            return router.execute_task(resolved, self.cwd, timeout, telemetry=True,
                                       telemetry_project=self.cwd)

    def test_execute_task_accepts_optional_keyword_only_telemetry_controls(self):
        resolved = router.resolve_task(task(), self.policy)
        parameters = __import__("inspect").signature(router.execute_task).parameters
        self.assertEqual(parameters["telemetry"].kind, __import__("inspect").Parameter.KEYWORD_ONLY)
        self.assertEqual(parameters["telemetry"].default, False)
        self.assertEqual(parameters["telemetry_project"].kind, __import__("inspect").Parameter.KEYWORD_ONLY)
        self.assertEqual(parameters["telemetry_project"].default, None)

    def test_active_cli_emits_json_events_and_keeps_single_public_json_result(self):
        self.fake_codex("""
import json
print(json.dumps({"type":"thread.started","thread_id":"fake-thread-1"}), flush=True)
print(json.dumps({"type":"item.completed","item":{"type":"agent_message","text":"synthetic complete"}}), flush=True)
print(json.dumps({"type":"turn.completed","usage":{"input_tokens":10,"cached_input_tokens":8,"output_tokens":2,"reasoning_output_tokens":1,"total_tokens":12}}), flush=True)
""")
        task_path = self.root / "task.json"
        task_path.write_text(json.dumps(task()), encoding="utf-8")
        output = io.StringIO()
        errors = io.StringIO()
        with patch.dict(os.environ, {"PATH": str(self.bin) + os.pathsep + os.environ.get("PATH", "")}), \
             contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = router.main(["run", "--task", str(task_path), "--cwd", str(self.cwd),
                                "--policy", str(self.policy_path), "--telemetry"])
        self.assertEqual(code, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["task_id"], "task-telemetry")
        self.assertIn("fake-thread-1", json.dumps(payload))
        self.assertTrue((self.cwd / ".model-router").exists())

    def test_active_call_uses_json_mode_and_does_not_send_worker_prompt_as_arguments(self):
        args_path = self.root / "argv.json"
        executable = self.fake_codex("""
import json, os, sys
open(os.environ["FAKE_ARGS"], "w").write(json.dumps(sys.argv[1:]))
print(json.dumps({"type":"thread.started","thread_id":"thread-link"}), flush=True)
print(json.dumps({"type":"turn.completed","usage":{"total_tokens":0}}), flush=True)
""")
        with patch.dict(os.environ, {"FAKE_ARGS": str(args_path)}):
            result = self.run_with_fake(router.resolve_task(task(), self.policy))
        self.assertEqual(result["status"], "success")
        self.assertIn("thread-link", json.dumps(result))
        self.assertIn("--json", json.loads(args_path.read_text(encoding="utf-8")))
        recorded = json.dumps(result)
        self.assertNotIn("Perform synthetic worker task", recorded)
        self.assertNotIn("MODEL_ROUTER_WORKER", recorded)

    def test_legacy_execution_defaults_to_existing_single_json_exec_contract(self):
        resolved = router.resolve_task(task(), self.policy)
        completed = __import__("subprocess").CompletedProcess([], 0, "done", "")
        with patch.object(router.subprocess, "run", return_value=completed) as run:
            result = router.execute_task(resolved, self.cwd, 5)
        self.assertEqual(result["status"], "success")
        run.assert_called_once()
        self.assertNotIn("--json", run.call_args.args[0])

    def test_dry_run_telemetry_flag_does_not_create_project_storage_or_start_worker(self):
        task_path = self.root / "task.json"
        task_path.write_text(json.dumps(task()), encoding="utf-8")
        output = io.StringIO()
        with patch.object(router.subprocess, "Popen") as popen, contextlib.redirect_stdout(output):
            code = router.main(["run", "--task", str(task_path), "--cwd", str(self.cwd),
                                "--policy", str(self.policy_path), "--dry-run", "--telemetry"])
        self.assertEqual(code, 0)
        self.assertFalse(self.cwd.joinpath(".model-router").exists())
        popen.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())["command"][-1], "-")

    def test_telemetry_project_changes_store_root_without_changing_worker_cwd(self):
        self.fake_codex("import json\nprint(json.dumps({\"type\":\"thread.started\",\"thread_id\":\"t\"}), flush=True)\n")
        other_project = self.root / "shared telemetry root"
        other_project.mkdir()
        task_path = self.root / "task.json"
        task_path.write_text(json.dumps(task()), encoding="utf-8")
        output = io.StringIO()
        with patch.dict(os.environ, {"PATH": str(self.bin) + os.pathsep + os.environ.get("PATH", "")}), \
             contextlib.redirect_stdout(output):
            code = router.main(["run", "--task", str(task_path), "--cwd", str(self.cwd),
                                "--policy", str(self.policy_path), "--telemetry",
                                "--telemetry-project", str(other_project)])
        self.assertEqual(code, 0)
        self.assertTrue((other_project / ".model-router").exists())
        self.assertFalse((self.cwd / ".model-router").exists())
        self.assertEqual(json.loads(output.getvalue())["status"], "success")

    def test_timeout_is_reported_even_if_telemetry_cannot_finish(self):
        self.fake_codex("import json, time\nprint(json.dumps({\"type\":\"thread.started\",\"thread_id\":\"slow\"}), flush=True)\ntime.sleep(3)\n")
        result = self.run_with_fake(router.resolve_task(task(), self.policy), timeout=1)
        self.assertEqual(result["status"], "timeout")
        self.assertFalse(result["model_verified"])

    def test_worker_failure_and_missing_cli_remain_worker_outcomes_with_telemetry(self):
        self.fake_codex("import json, sys\nprint(json.dumps({\"type\":\"thread.started\",\"thread_id\":\"failed\"}), flush=True)\nsys.exit(7)\n")
        result = self.run_with_fake(router.resolve_task(task(), self.policy))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["returncode"], 7)
        with patch.dict(os.environ, {"PATH": str(self.root / "missing-bin")}):
            unavailable = router.execute_task(router.resolve_task(task(), self.policy), self.cwd, 3,
                                              telemetry=True, telemetry_project=self.cwd)
        self.assertEqual(unavailable["status"], "unavailable")

    def test_telemetry_failure_is_reported_separately_and_does_not_mask_worker_success(self):
        self.fake_codex("import json\nprint(json.dumps({\"type\":\"thread.started\",\"thread_id\":\"ok\"}), flush=True)\n")
        (self.cwd / ".model-router").write_text("prevent telemetry storage directory", encoding="utf-8")
        with patch.dict(os.environ, {"PATH": str(self.bin) + os.pathsep + os.environ.get("PATH", "")}), \
             contextlib.redirect_stderr(io.StringIO()):
            result = router.execute_task(router.resolve_task(task(), self.policy), self.cwd, 5,
                                         telemetry=True, telemetry_project=self.cwd)
        self.assertEqual(result["status"], "success")
        self.assertTrue(result.get("telemetry_warning") or result.get("telemetry_error"))
        self.assertFalse(result["model_verified"])


if __name__ == "__main__":
    unittest.main()
