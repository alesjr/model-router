"""Contract tests for the settings gate and JSON-only usage dashboard."""

import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "model-router" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from telemetry_settings import (  # noqa: E402
    TelemetryDisabled,
    get_settings,
    require_enabled,
    set_telemetry,
)
import telemetry_core  # noqa: E402
from usage_dashboard import build_dashboard, read_usage, usage_view  # noqa: E402


class SettingsGateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.xdg = self.root / "xdg"
        self._environment = patch.dict(os.environ, {
            "XDG_CONFIG_HOME": str(self.xdg), "HOME": str(self.root / "home"),
        })
        self._environment.start()
        self.addCleanup(self._environment.stop)

    def global_settings(self):
        return self.xdg / "model-router" / "settings.json"

    def put_global(self, enabled):
        path = self.global_settings()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"telemetry": {"enabled": enabled}}), encoding="utf-8")

    def put_project(self, enabled):
        (self.project / ".model-router.json").write_text(
            json.dumps({"telemetry": {"enabled": enabled}}), encoding="utf-8")

    def test_default_off_returns_reason_without_creating_config_or_database(self):
        result = get_settings(self.project)
        self.assertIs(result["enabled"], False)
        self.assertTrue(result["reason"])
        self.assertFalse(self.xdg.exists())
        self.assertFalse((self.project / ".model-router").exists())
        with self.assertRaises(TelemetryDisabled):
            require_enabled(self.project)

    def test_explicit_global_or_project_false_vetoes_true_at_other_scope(self):
        self.put_global(True)
        self.put_project(False)
        self.assertFalse(get_settings(self.project)["enabled"])
        self.put_global(False)
        self.put_project(True)
        self.assertFalse(get_settings(self.project)["enabled"])

    def test_invalid_settings_block_collection_with_clear_reason(self):
        path = self.global_settings()
        path.parent.mkdir(parents=True)
        path.write_text("{broken", encoding="utf-8")
        result = get_settings(self.project)
        self.assertFalse(result["enabled"])
        self.assertTrue(result["reason"])

    def test_set_telemetry_preserves_unrelated_existing_keys(self):
        path = self.global_settings()
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"other": {"keep": 7}, "telemetry": {"enabled": False, "x": 1}}),
                        encoding="utf-8")
        result_path = set_telemetry(True)
        self.assertEqual(result_path, path)
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["other"], {"keep": 7})
        self.assertEqual(data["telemetry"]["x"], 1)
        self.assertTrue(data["telemetry"]["enabled"])

    def test_existing_collector_stops_writes_after_global_off(self):
        self.put_global(True)
        collector = telemetry_core.Telemetry(self.project, sources=[])
        self.addCleanup(collector.close)
        self.put_global(False)
        with self.assertRaises(TelemetryDisabled):
            collector.record_invocation({
                "invocation_id": "after-off", "task_id": "x", "status": "success",
                "started_at": "2026-10-26T10:00:00Z",
            })
        self.assertEqual(collector.db.execute("SELECT count(*) FROM invocations").fetchone()[0], 0)

    def test_disabled_telemetry_cli_returns_json_without_opening_database(self):
        self.assertIsNotNone(importlib.util.find_spec("telemetry"))
        cli_path = SCRIPTS / "telemetry.py"
        spec = importlib.util.spec_from_file_location("telemetry_disabled_contract", cli_path)
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        with patch.object(sqlite3, "connect", side_effect=AssertionError("database opened")):
            # CLI interface is selected by the implementation's documented parser.
            # Config status command must report disabled without touching storage.
            output = cli.main(["status", "--project", str(self.project)])
        self.assertIsInstance(output, int)
        self.assertFalse((self.project / ".model-router").exists())


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.logs = self.root / "logs"
        self.day = self.logs / "2026-10-26"
        self.day.mkdir(parents=True)

    def write_generation(self):
        gen = self.day / "revisions" / "3" / "summary.json"
        gen.parent.mkdir(parents=True)
        summary = {
            "schema_version": 1, "date": "2026-10-26", "timezone": "UTC", "revision": 3,
            "totals": {"total_tokens": 100, "input_tokens": 80,
                       "cached_input_tokens": 30, "output_tokens": 20},
            "by_model": {"<script>alert(1)</script>": {
                "total_tokens": 100, "input_tokens": 80,
                "cached_input_tokens": 30, "output_tokens": 20}},
            "by_session": {"session-<img>": {
                "totals": {"total_tokens": 100}, "by_model": {},
            }},
            "coverage": {"complete": False, "missing": ["partial source"]},
        }
        gen.write_text(json.dumps(summary), encoding="utf-8")
        old_generation = self.day / "revisions" / "2" / "summary.json"
        old_generation.parent.mkdir(parents=True)
        old_generation.write_text(json.dumps({
            **summary, "revision": 2, "totals": {"total_tokens": 900},
        }), encoding="utf-8")
        (self.day / "current.json").write_text(json.dumps({
            "schema_version": 1, "date": "2026-10-26", "revision": 3,
            "summary": "revisions/3/summary.json",
            "timezone": "UTC",
        }), encoding="utf-8")
        (self.day / "events.jsonl").write_text(
            json.dumps({"totals": {"total_tokens": 8000}}) + "\n", encoding="utf-8")

    def test_read_uses_only_manifest_generation_and_view_filters_dates_and_periods(self):
        self.write_generation()
        data = read_usage(self.logs)
        self.assertEqual(len(data["days"]), 1)
        self.assertEqual(data["days"][0]["totals"]["total_tokens"], 100)
        other = self.logs / "2026-10-10"
        generation = other / "revisions" / "1"
        generation.mkdir(parents=True)
        (generation / "summary.json").write_text(json.dumps({
            "schema_version": 1, "date": "2026-10-10", "timezone": "UTC", "revision": 1,
            "totals": {"total_tokens": 500}, "by_model": {}, "by_session": {},
            "coverage": {"complete": True},
        }), encoding="utf-8")
        (other / "current.json").write_text(json.dumps({
            "schema_version": 1, "date": "2026-10-10", "revision": 1,
            "summary": "revisions/1/summary.json", "timezone": "UTC",
        }), encoding="utf-8")
        data = read_usage(self.logs)
        self.assertEqual(usage_view(data, "daily", "2026-10-26")["totals"]["total_tokens"], 100)
        for period in ("weekly", "cumulative"):
            view = usage_view(data, period, "2026-10-26")
            self.assertEqual(view["totals"]["total_tokens"],
                             100 if period == "weekly" else 600)
            self.assertIn("by_model", view)
            self.assertIn("by_session", view)
            self.assertIn("coverage", view)
        self.assertIsNone(usage_view(data, "daily", "2026-10-25")["totals"]["total_tokens"])

    def test_null_fields_partial_coverage_and_cache_not_added_again(self):
        self.write_generation()
        data = read_usage(self.logs)
        view = usage_view(data, "daily", "2026-10-26")
        self.assertEqual(view["totals"]["total_tokens"], 100)
        self.assertEqual(view["totals"]["cached_input_tokens"], 30)
        self.assertIsNone(view["totals"].get("reasoning_output_tokens"))
        self.assertFalse(view["coverage"]["complete"])

    def test_manifest_path_traversal_and_revision_mismatch_are_ignored(self):
        self.write_generation()
        manifest = self.day / "current.json"
        manifest.write_text(json.dumps({
            "date": "2026-10-26", "revision": 4,
            "summary": "../../outside.json", "timezone": "UTC",
        }), encoding="utf-8")
        data = read_usage(self.logs)
        self.assertEqual(data["days"], [])
        self.assertTrue(data["diagnostics"])

    def test_manifest_revision_mismatch_is_ignored(self):
        self.write_generation()
        (self.day / "current.json").write_text(json.dumps({
            "schema_version": 1, "date": "2026-10-26", "revision": 4,
            "summary": "revisions/3/summary.json", "timezone": "UTC",
        }), encoding="utf-8")
        data = read_usage(self.logs)
        self.assertEqual(data["days"], [])
        self.assertTrue(data["diagnostics"])

    def test_html_is_self_contained_escaped_and_uses_text_content(self):
        self.write_generation()
        destination = self.root / "usage local.html"
        with patch.object(sqlite3, "connect", side_effect=AssertionError("SQLite read")):
            output = build_dashboard(self.project, logs_dir=self.logs, output=destination)
        html = output.read_text(encoding="utf-8")
        self.assertIn("textContent", html)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("\\u003cscript", html.lower())
        self.assertIn("weekly", html)
        self.assertIn("cumulative", html)


if __name__ == "__main__":
    unittest.main()
