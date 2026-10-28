import contextlib
import importlib.util
import io
import json
import os
import shutil
import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "model-router" / "scripts"
import sys
sys.path.insert(0, str(SCRIPTS))

from telemetry_core import Telemetry

TELEMETRY_CLI_PATH = SCRIPTS / "telemetry.py"
CLI_SPEC = importlib.util.spec_from_file_location("model_router_telemetry_cli_tests", TELEMETRY_CLI_PATH)
if CLI_SPEC is None or CLI_SPEC.loader is None:
    raise ImportError(f"Could not load telemetry CLI from {TELEMETRY_CLI_PATH}")
telemetry_cli = importlib.util.module_from_spec(CLI_SPEC)
CLI_SPEC.loader.exec_module(telemetry_cli)


def write_rollout(path, thread, session, cwd, events, parent=None, parent_shape="spawn"):
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [{"timestamp": "2026-10-26T09:00:00Z", "type": "session_meta", "payload": {
        "id": thread, "session_id": session, "cwd": str(cwd),
        **({"source": {"subagent": {parent_shape: {"parent_thread_id": parent}}}} if parent else {}),
    }}]
    for event in events:
        event = dict(event)
        if event.get("type") in {"task_started", "task_complete", "token_count"}:
            kind = event.pop("type")
            event = {"type": "event_msg", "payload": {"type": kind, **event.get("payload", {})}}
        event.setdefault("timestamp", event.get("payload", {}).get("timestamp", "2026-10-26T10:00:00Z"))
        rows.append(event)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def context(turn, model="gpt-6-luna", effort="low"):
    return {"type": "turn_context", "payload": {
        "turn_id": turn, "model": model, "reasoning_effort": effort,
    }}


def response(turn, response_id, at, usage, thread=None, session=None):
    return {"type": "token_usage_record", "payload": {
        "thread_id": thread, "session_id": session, "turn_id": turn,
        "response_id": response_id, "timestamp": at, "usage": usage,
    }}


def usage(total, input_tokens=0, cached=0, output=0, reasoning=0, cache_write=0):
    return {"total_tokens": total, "input_tokens": input_tokens,
            "cached_input_tokens": cached, "output_tokens": output,
            "reasoning_output_tokens": reasoning,
            "cache_write_input_tokens": cache_write}


class TelemetryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.xdg = self.root / "xdg-config"
        (self.xdg / "model-router").mkdir(parents=True)
        (self.xdg / "model-router" / "settings.json").write_text(
            json.dumps({"telemetry": {"enabled": True}}), encoding="utf-8")
        self._environment = patch.dict(os.environ, {
            "XDG_CONFIG_HOME": str(self.xdg), "HOME": str(self.root / "home"),
        })
        self._environment.start()
        self.addCleanup(self._environment.stop)
        (self.project / ".model-router.json").write_text(
            json.dumps({"telemetry": {"enabled": True}}), encoding="utf-8")
        self.source = self.root / "source"
        self.source.mkdir()

    def collector(self, sources=None, **kwargs):
        return Telemetry(self.project, sources=sources or [self.source], **kwargs)

    def addCleanupCollector(self, collector):
        self.addCleanup(collector.close)
        return collector

    def test_parent_and_child_share_session_but_remain_distinct_threads(self):
        write_rollout(self.source / "root.jsonl", "root-thread", "session-a", self.project,
                      [context("turn-root"), response("turn-root", "resp-root", "2026-10-26T10:00:00Z", usage(110, 100, 80, 10, 5))])
        write_rollout(self.source / "child.jsonl", "child-thread", "session-a", self.root / "elsewhere",
                      [context("turn-child"), response("turn-child", "resp-child", "2026-10-26T10:01:00Z", usage(7, 5, 0, 2), "child-thread", "session-a")], parent="root-thread")
        telemetry = self.addCleanupCollector(self.collector())
        telemetry.sync()
        status = telemetry.status()
        self.assertEqual({a["thread_id"] for a in status["agents"]}, {"root-thread", "child-thread"})
        self.assertEqual({a["session_id"] for a in status["agents"]}, {"session-a"})
        summary = telemetry.summary("2026-10-26")
        self.assertEqual(summary["totals"]["total_tokens"], 117)
        self.assertEqual(summary["totals"]["cached_input_tokens"], 80)
        self.assertEqual(summary["totals"]["reasoning_output_tokens"], 5)

    def test_thread_spawn_parent_metadata_links_outside_child_and_exports_worker_identity(self):
        write_rollout(
            self.source / "root.jsonl", "root-thread", "root-session", self.project,
            [context("root-turn", "gpt-6-luna"),
             response("root-turn", "root-response", "2026-10-26T10:00:00Z",
                      usage(10, 8, 4, 2), "root-thread", "root-session")],
        )
        write_rollout(
            self.source / "child.jsonl", "child-thread", None, self.root / "outside-project",
            [context("child-turn", "gpt-6.1-sol"),
             response("child-turn", "child-response", "2026-10-26T10:01:00Z",
                      usage(7, 5, 0, 2), "child-thread", None)],
            parent="root-thread", parent_shape="thread_spawn",
        )
        logs = self.root / "thread-spawn-logs"
        telemetry = self.addCleanupCollector(self.collector(logs_dir=logs))

        telemetry.sync()

        agents = {agent["thread_id"]: agent for agent in telemetry.status()["agents"]}
        self.assertEqual(set(agents), {"root-thread", "child-thread"})
        self.assertEqual(agents["root-thread"]["role"], "coordinator")
        self.assertEqual(agents["child-thread"]["role"], "worker")
        self.assertEqual(agents["child-thread"]["parent_thread_id"], "root-thread")
        self.assertEqual(agents["child-thread"]["session_id"], "root-session")
        self.assertEqual(agents["child-thread"]["model_reported"], "gpt-6.1-sol")

        summary = telemetry.summary("2026-10-26")
        self.assertEqual(summary["totals"]["total_tokens"], 17)
        self.assertEqual(summary["by_role"]["coordinator"]["total_tokens"], 10)
        self.assertEqual(summary["by_role"]["worker"]["total_tokens"], 7)
        self.assertEqual(summary["by_session"]["root-session"]["totals"]["total_tokens"], 17)

        manifest_path = logs / "2026-10-26" / "current.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        events_path = manifest_path.parent / manifest["events"]
        exported = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
        child_export = next(event for event in exported if event.get("response_id") == "child-response")
        self.assertEqual(child_export["parent_thread_id"], "root-thread")
        self.assertEqual(child_export["session_id"], "root-session")
        self.assertEqual(child_export["model_reported"], "gpt-6.1-sol")

    def test_sync_reconciles_persisted_root_metadata_after_thread_spawn_parent_arrives(self):
        write_rollout(
            self.source / "root.jsonl", "root-thread", "root-session", self.project,
            [context("root-turn"),
             response("root-turn", "root-response", "2026-10-26T10:00:00Z",
                      usage(10, 8, 4, 2), "root-thread", "root-session")],
        )
        child_path = self.source / "child.jsonl"
        write_rollout(
            child_path, "child-thread", None, self.root / "outside-project",
            [context("child-turn", "gpt-6.1-sol"),
             response("child-turn", "child-response", "2026-10-26T10:01:00Z",
                      usage(7, 5, 0, 2), "child-thread", None)],
        )
        logs = self.root / "reconciled-thread-spawn-logs"
        telemetry = self.addCleanupCollector(self.collector(logs_dir=logs))
        telemetry.sync()

        before = {agent["thread_id"]: agent for agent in telemetry.status()["agents"]}
        self.assertEqual(set(before), {"root-thread"})
        self.assertEqual(telemetry.summary("2026-10-26")["totals"]["total_tokens"], 10)

        lines = child_path.read_text(encoding="utf-8").splitlines(keepends=True)
        metadata = json.loads(lines[0])
        metadata["payload"]["source"] = {
            "subagent": {"thread_spawn": {"parent_thread_id": "root-thread"}}
        }
        lines[0] = json.dumps(metadata) + "\n"
        child_path.write_text("".join(lines), encoding="utf-8")
        telemetry.sync()

        agents = {agent["thread_id"]: agent for agent in telemetry.status()["agents"]}
        self.assertEqual(set(agents), {"root-thread", "child-thread"})
        self.assertEqual(agents["child-thread"]["role"], "worker")
        self.assertEqual(agents["child-thread"]["parent_thread_id"], "root-thread")
        self.assertEqual(agents["child-thread"]["session_id"], "root-session")
        self.assertEqual(telemetry.summary("2026-10-26")["totals"]["total_tokens"], 17)

        manifest_path = logs / "2026-10-26" / "current.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        events_path = manifest_path.parent / manifest["events"]
        exported = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
        response_ids = [event["response_id"] for event in exported if event.get("response_id")]
        self.assertCountEqual(response_ids, ["root-response", "child-response"])
        self.assertEqual(len(response_ids), len(set(response_ids)))

        first_revision = telemetry.summary("2026-10-26")["revision"]
        telemetry.sync()
        self.assertEqual(telemetry.summary("2026-10-26")["revision"], first_revision)
        self.assertEqual(telemetry.summary("2026-10-26")["totals"]["total_tokens"], 17)

    def test_response_dedup_across_sync_reopen_archive_and_duplicate_source(self):
        events = [context("turn-1"), response("turn-1", "resp-1", "2026-10-26T10:00:00Z", usage(12, 10, 8, 2, 1), "thread-1", "session-1")]
        path = self.source / "thread.jsonl"
        write_rollout(path, "thread-1", "session-1", self.project, events)
        archived = self.source / "archived" / path.name
        archived.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, archived)
        telemetry = self.addCleanupCollector(self.collector([self.source, self.source / "archived"]))
        telemetry.sync()
        telemetry.sync()
        telemetry.close()
        reopened = self.addCleanupCollector(Telemetry(self.project, sources=[self.source, self.source / "archived"]))
        reopened.sync()
        self.assertEqual(reopened.summary("2026-10-26")["totals"]["total_tokens"], 12)
        self.assertEqual(len(reopened.status()["agents"]), 1)

    def test_reused_thread_turn_model_and_second_collector_do_not_duplicate(self):
        events = [context("turn-a", "gpt-6-luna"), response("turn-a", "r-a", "2026-10-26T10:00:00Z", usage(10, 8, 4, 2), "thread", "s"),
                  context("turn-b", "gpt-6.1-sol"), response("turn-b", "r-b", "2026-10-26T11:00:00Z", usage(20, 17, 10, 3), "thread", "s")]
        write_rollout(self.source / "thread.jsonl", "thread", "s", self.project, events)
        first = self.addCleanupCollector(self.collector())
        first.sync()
        second = self.addCleanupCollector(self.collector())
        second.sync()
        summary = second.summary("2026-10-26")
        self.assertEqual(summary["totals"]["total_tokens"], 30)
        self.assertEqual(set(summary["by_model"]), {"gpt-6-luna", "gpt-6.1-sol"})
        self.assertEqual(len(second.status()["agents"]), 1)

    def test_cached_and_reasoning_fields_are_subsets_of_total(self):
        write_rollout(self.source / "thread.jsonl", "t", "s", self.project,
                      [context("turn"), response("turn", "r", "2026-10-26T10:00:00Z", usage(110, 100, 80, 10, 5), "t", "s")])
        telemetry = self.addCleanupCollector(self.collector())
        telemetry.sync()
        totals = telemetry.summary("2026-10-26")["totals"]
        self.assertEqual(totals["total_tokens"], 110)
        self.assertEqual(totals["input_tokens"], 100)
        self.assertEqual(totals["cached_input_tokens"], 80)
        self.assertEqual(totals["output_tokens"], 10)
        self.assertEqual(totals["reasoning_output_tokens"], 5)

    def test_snapshots_use_baseline_and_increment_without_double_count_then_backfill(self):
        events = [
            {"type": "token_count", "payload": {"timestamp": "2026-10-24T10:00:00Z", "info": {"total_token_usage": usage(100)}}},
            {"type": "token_count", "payload": {"timestamp": "2026-10-25T10:00:00Z", "info": {"total_token_usage": usage(130)}}},
            {"type": "token_count", "payload": {"timestamp": "2026-10-26T10:00:00Z", "info": {"total_token_usage": usage(150)}}},
        ]
        write_rollout(self.source / "thread.jsonl", "t", "s", self.project, events)
        telemetry = self.addCleanupCollector(self.collector())
        telemetry.sync()
        with (self.source / "thread.jsonl").open("a", encoding="utf-8") as stream:
            for event in (
                context("turn-a"),
                response("turn-a", "detail-a", "2026-10-25T12:00:00Z", usage(7), "t", "s"),
                context("turn-b"),
                response("turn-b", "detail-b", "2026-10-26T12:00:00Z", usage(8), "t", "s"),
            ):
                event["timestamp"] = event["payload"].get("timestamp", "2026-10-26T12:00:00Z")
                stream.write(json.dumps(event) + "\n")
        telemetry.sync()
        day = telemetry.summary("2026-10-26")
        self.assertEqual(day["totals"]["total_tokens"], 8)
        self.assertFalse(day["coverage"]["complete"])

    def test_same_day_snapshot_baseline_and_delta_are_partial_unknown_and_deduplicated(self):
        events = [
            {"type": "token_count", "payload": {"timestamp": "2026-10-26T09:00:00Z", "info": {"total_token_usage": usage(100)}}},
            {"type": "token_count", "payload": {"timestamp": "2026-10-26T10:00:00Z", "info": {"total_token_usage": usage(150)}}},
        ]
        write_rollout(self.source / "thread.jsonl", "t", "s", self.project, events)
        telemetry = self.addCleanupCollector(self.collector())
        telemetry.sync()
        first = telemetry.summary("2026-10-26")
        telemetry.sync()
        second = telemetry.summary("2026-10-26")
        self.assertEqual(first["totals"]["total_tokens"], 50)
        self.assertEqual(second["totals"]["total_tokens"], 50)
        self.assertIn("unknown", second["by_model"])
        self.assertFalse(second["coverage"]["complete"])
        self.assertTrue(second["coverage"].get("baseline") or second["coverage"].get("non_additive"))

    def test_conflicting_independent_snapshot_sequences_are_unknown_in_either_file_order(self):
        def checkpoints(second_total):
            return [
                {"type": "token_count", "payload": {"timestamp": "2026-10-26T10:00:00Z", "info": {"total_token_usage": usage(100)}}},
                {"type": "token_count", "payload": {"timestamp": "2026-10-26T11:00:00Z", "info": {"total_token_usage": usage(second_total)}}},
            ]

        outcomes = []
        for index, swapped in enumerate((False, True)):
            project = self.root / f"overlap-project-{index}"
            project.mkdir()
            source_a = self.root / f"overlap-source-a-{index}"
            source_b = self.root / f"overlap-source-b-{index}"
            source_a.mkdir()
            source_b.mkdir()
            first_values, second_values = (120, 150) if swapped else (150, 120)
            write_rollout(source_a / "a.jsonl", "shared-thread", "shared-session", project,
                          checkpoints(first_values))
            write_rollout(source_b / "b.jsonl", "shared-thread", "shared-session", project,
                          checkpoints(second_values))
            telemetry = self.addCleanupCollector(Telemetry(project, sources=[source_a, source_b]))
            telemetry.sync()
            summary = telemetry.summary("2026-10-26")
            outcomes.append((summary["totals"]["total_tokens"], summary["coverage"]))

        for total, coverage in outcomes:
            self.assertIsNone(total)
            self.assertFalse(coverage["complete"])
            self.assertTrue(coverage["non_additive"])

    def test_identical_snapshot_sequences_from_independent_files_count_delta_once(self):
        events = [
            {"type": "token_count", "payload": {"timestamp": "2026-10-26T10:00:00Z", "info": {"total_token_usage": usage(100)}}},
            {"type": "token_count", "payload": {"timestamp": "2026-10-26T11:00:00Z", "info": {"total_token_usage": usage(150)}}},
        ]
        source_a, source_b = self.root / "identical-source-a", self.root / "identical-source-b"
        source_a.mkdir()
        source_b.mkdir()
        write_rollout(source_a / "a.jsonl", "shared-thread", "shared-session", self.project, events)
        write_rollout(source_b / "b.jsonl", "shared-thread", "shared-session", self.project, events)
        telemetry = self.addCleanupCollector(self.collector([source_a, source_b]))
        telemetry.sync()
        summary = telemetry.summary("2026-10-26")
        self.assertEqual(summary["totals"]["total_tokens"], 50)

    def test_replaced_snapshot_generation_rebaselines_without_adding_old_delta(self):
        path = self.source / "thread.jsonl"
        write_rollout(path, "t", "s", self.project, [
            {"type": "token_count", "payload": {"timestamp": "2026-10-26T10:00:00Z", "info": {"total_token_usage": usage(100)}}},
        ])
        telemetry = self.addCleanupCollector(self.collector())
        telemetry.sync()
        write_rollout(path, "t", "s", self.project, [
            {"type": "token_count", "payload": {"timestamp": "2026-10-26T11:00:00Z", "info": {"total_token_usage": usage(150)}}},
            {"type": "token_count", "payload": {"timestamp": "2026-10-26T12:00:00Z", "info": {"total_token_usage": usage(170)}}},
        ])
        telemetry.sync()
        summary = telemetry.summary("2026-10-26")
        self.assertEqual(summary["totals"]["total_tokens"], 20)
        self.assertFalse(summary["coverage"]["complete"])
        self.assertTrue(summary["coverage"]["baseline"] or summary["coverage"]["non_additive"])

    def test_corrected_response_replaces_same_source_but_independent_conflict_stays_partial(self):
        path = self.source / "thread.jsonl"
        original = [context("turn"), response("turn", "r", "2026-10-26T10:00:00Z", usage(10, 8, 4, 2), "t", "s")]
        write_rollout(path, "t", "s", self.project, original)
        other_source = self.root / "independent-source"
        telemetry = self.addCleanupCollector(self.collector([self.source]))
        telemetry.sync()
        write_rollout(path, "t", "s", self.project,
                      [context("turn"), response("turn", "r", "2026-10-26T10:00:00Z", usage(12, 10, 5, 2), "t", "s")])
        telemetry.sync()
        self.assertEqual(telemetry.summary("2026-10-26")["totals"]["total_tokens"], 12)
        write_rollout(other_source / "same-response.jsonl", "t", "s", self.project,
                      [context("turn"), response("turn", "r", "2026-10-26T10:00:00Z", usage(14, 12, 6, 2), "t", "s")])
        independent = self.addCleanupCollector(self.collector([self.source, other_source]))
        independent.sync()
        conflicted = independent.summary("2026-10-26")
        self.assertFalse(conflicted["coverage"]["complete"])
        self.assertIn("response_conflict", {item.get("code") for item in conflicted["coverage"]["non_additive"]})

    def test_malformed_source_line_makes_coverage_partial_without_losing_valid_subtotal(self):
        path = self.source / "thread.jsonl"
        write_rollout(path, "t", "s", self.project,
                      [context("turn"), response("turn", "r", "2026-10-26T10:00:00Z", usage(12, 10, 8, 2, 1), "t", "s")])
        with path.open("a", encoding="utf-8") as stream:
            stream.write('{"type": not-valid-json}\n')
        telemetry = self.addCleanupCollector(self.collector())
        telemetry.sync()
        summary = telemetry.summary("2026-10-26")
        self.assertEqual(summary["totals"]["total_tokens"], 12)
        self.assertFalse(summary["coverage"]["complete"])

    def test_absent_and_conflicting_models_keep_usage_unknown_and_nulls_unknown(self):
        events = [context("turn-a", "gpt-6-luna"), context("turn-a", "gpt-6.1-sol"),
                  response("turn-a", "r1", "2026-10-26T10:00:00Z", {"total_tokens": 5}, "t", "s"),
                  response("turn-b", "r2", "2026-10-26T10:01:00Z", {"input_tokens": 4}, "t", "s")]
        write_rollout(self.source / "thread.jsonl", "t", "s", self.project, events)
        telemetry = self.addCleanupCollector(self.collector())
        telemetry.sync()
        summary = telemetry.summary("2026-10-26")
        self.assertIn("unknown", summary["by_model"])
        self.assertEqual(summary["by_model"]["unknown"]["total_tokens"], 5)
        self.assertIsNone(summary["by_model"]["unknown"]["input_tokens"])
        self.assertEqual(summary["totals"]["total_tokens"], 5)
        self.assertIsNone(summary["totals"]["input_tokens"])
        self.assertFalse(summary["coverage"]["complete"])

    def test_partial_final_line_then_completion_is_ingested_once(self):
        path = self.source / "thread.jsonl"
        write_rollout(path, "t", "s", self.project, [context("turn")])
        complete = json.dumps(response("turn", "r", "2026-10-26T10:00:00Z", usage(9), "t", "s"))
        with path.open("a", encoding="utf-8") as stream:
            stream.write(complete[:len(complete) // 2])
        telemetry = self.addCleanupCollector(self.collector())
        telemetry.sync()
        with path.open("a", encoding="utf-8") as stream:
            stream.write(complete[len(complete) // 2:] + "\n")
        telemetry.sync()
        self.assertEqual(telemetry.summary("2026-10-26")["totals"]["total_tokens"], 9)

    def test_invalid_complete_line_is_diagnosed_and_invalid_timestamp_is_not_dated_at_ingest(self):
        path = self.source / "thread.jsonl"
        write_rollout(path, "t", "s", self.project,
                      [context("turn"), response("turn", "bad-time", "not-a-timestamp", usage(6), "t", "s")])
        with path.open("a", encoding="utf-8") as stream:
            stream.write('{"type": invalid complete row}\n')
        telemetry = self.addCleanupCollector(self.collector())
        result = telemetry.sync()
        self.assertTrue(result["diagnostics"])
        self.assertIsNone(telemetry.summary("2026-10-26")["totals"]["total_tokens"])

    def test_truncation_and_rewrite_preserve_existing_response_and_accept_new_response(self):
        path = self.source / "thread.jsonl"
        write_rollout(path, "t", "s", self.project,
                      [context("turn-a"), response("turn-a", "stable", "2026-10-26T10:00:00Z", usage(4), "t", "s")])
        telemetry = self.addCleanupCollector(self.collector())
        telemetry.sync()
        write_rollout(path, "t", "s", self.project,
                      [context("turn-a"), response("turn-a", "stable", "2026-10-26T10:00:00Z", usage(4), "t", "s"),
                       context("turn-b"), response("turn-b", "new", "2026-10-26T11:00:00Z", usage(7), "t", "s")])
        telemetry.sync()
        self.assertEqual(telemetry.summary("2026-10-26")["totals"]["total_tokens"], 11)

    def test_project_scope_excludes_sibling_but_child_link_overrides_child_cwd(self):
        sibling = self.root / "project-sibling"
        sibling.mkdir()
        write_rollout(self.source / "sibling.jsonl", "outside", "s-out", sibling, [context("t"), response("t", "r-out", "2026-10-26T10:00:00Z", usage(50), "outside", "s-out")])
        write_rollout(self.source / "root.jsonl", "inside", "s-in", self.project, [context("t"), response("t", "r-in", "2026-10-26T10:00:00Z", usage(10), "inside", "s-in")])
        write_rollout(self.source / "child.jsonl", "child", "s-in", self.root, [context("t"), response("t", "r-child", "2026-10-26T10:00:00Z", usage(5), "child", "s-in")], parent="inside")
        telemetry = self.addCleanupCollector(self.collector())
        telemetry.sync()
        self.assertEqual({a["thread_id"] for a in telemetry.status()["agents"]}, {"inside", "child"})
        self.assertEqual(telemetry.summary("2026-10-26")["totals"]["total_tokens"], 15)

    def test_timezone_groups_responses_into_configured_local_day(self):
        write_rollout(self.source / "thread.jsonl", "t", "s", self.project,
                      [context("turn"), response("turn", "r", "2026-10-27T02:30:00Z", usage(4), "t", "s")])
        telemetry = self.addCleanupCollector(self.collector(timezone="America/Sao_Paulo"))
        telemetry.sync()
        self.assertEqual(telemetry.summary("2026-10-26")["totals"]["total_tokens"], 4)
        self.assertEqual(telemetry.summary("2026-10-26")["timezone"], "America/Sao_Paulo")

    def test_manifest_points_to_matching_revision_and_sync_repairs_publication(self):
        write_rollout(self.source / "thread.jsonl", "t", "s", self.project,
                      [context("turn"), response("turn", "r", "2026-10-26T10:00:00Z", usage(4), "t", "s")])
        logs = self.root / "logs"
        telemetry = self.addCleanupCollector(self.collector(logs_dir=logs))
        telemetry.sync()
        manifest_path = logs / "2026-10-26" / "current.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        events_path = manifest_path.parent / manifest["events"]
        summary_path = manifest_path.parent / manifest["summary"]
        events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        self.assertEqual(summary["revision"], manifest["revision"])
        self.assertTrue(all(event["revision"] == manifest["revision"] for event in events))
        events_path.unlink()
        telemetry.sync()
        self.assertTrue((manifest_path.parent / json.loads(manifest_path.read_text())["events"]).exists())

    def test_sync_without_content_change_does_not_create_generation_and_prunes_old_generations(self):
        write_rollout(self.source / "thread.jsonl", "t", "s", self.project,
                      [context("turn"), response("turn", "r", "2026-10-26T10:00:00Z", usage(4), "t", "s")])
        logs = self.root / "logs-retention"
        telemetry = self.addCleanupCollector(self.collector(logs_dir=logs))
        telemetry.sync()
        day = logs / "2026-10-26"
        manifest_path = day / "current.json"
        first = json.loads(manifest_path.read_text(encoding="utf-8"))
        telemetry.sync()
        self.assertEqual(json.loads(manifest_path.read_text(encoding="utf-8")), first)
        self.assertEqual(sorted(p.name for p in (day / "revisions").iterdir()), [str(first["revision"])])

        (day / "revisions" / "legacy" ).mkdir()
        (day / "revisions" / "legacy" / "summary.json").write_text("{}", encoding="utf-8")
        (day / "revisions" / "999" ).mkdir()
        (day / "revisions" / "999" / "summary.json").write_text("{}", encoding="utf-8")
        telemetry.sync()
        generations = sorted(p.name for p in (day / "revisions").iterdir())
        self.assertEqual(generations, [str(first["revision"]), "legacy"])
        self.assertEqual(telemetry.summary("2026-10-26")["totals"]["total_tokens"], 4)

    def test_new_event_on_another_day_does_not_republish_stable_day_and_late_event_does(self):
        path = self.source / "thread.jsonl"
        write_rollout(path, "t", "s", self.project,
                      [context("turn-a"), response("turn-a", "r-a", "2026-10-26T10:00:00Z", usage(4, 4), "t", "s")])
        telemetry = self.addCleanupCollector(self.collector(logs_dir=self.root / "logs-dates"))
        telemetry.sync()
        day_a = telemetry.logs / "2026-10-26"
        manifest_a_path = day_a / "current.json"
        manifest_a = json.loads(manifest_a_path.read_text(encoding="utf-8"))
        summary_a = json.loads((day_a / manifest_a["summary"]).read_text(encoding="utf-8"))
        events_a = (day_a / manifest_a["events"]).read_bytes()

        with path.open("a", encoding="utf-8") as stream:
            turn_context = context("turn-b")
            turn_context["timestamp"] = "2026-10-27T09:59:00Z"
            event = response("turn-b", "r-b", "2026-10-27T10:00:00Z", usage(6, 6), "t", "s")
            event["timestamp"] = event["payload"]["timestamp"]
            stream.write(json.dumps(turn_context) + "\n")
            stream.write(json.dumps(event) + "\n")
        telemetry.sync()
        self.assertEqual(json.loads(manifest_a_path.read_text(encoding="utf-8")), manifest_a)
        self.assertEqual((day_a / manifest_a["events"]).read_bytes(), events_a)
        self.assertEqual(json.loads((day_a / manifest_a["summary"]).read_text(encoding="utf-8"))["coverage"],
                         summary_a["coverage"])

        with path.open("a", encoding="utf-8") as stream:
            turn_context = context("turn-c")
            turn_context["timestamp"] = "2026-10-26T10:59:00Z"
            event = response("turn-c", "r-c", "2026-10-26T11:00:00Z", usage(3, 3), "t", "s")
            event["timestamp"] = event["payload"]["timestamp"]
            stream.write(json.dumps(turn_context) + "\n")
            stream.write(json.dumps(event) + "\n")
        telemetry.sync()
        revised = json.loads(manifest_a_path.read_text(encoding="utf-8"))
        self.assertNotEqual(revised, manifest_a)
        events = (day_a / revised["events"]).read_text(encoding="utf-8")
        self.assertIn('"response_id":"r-c"', events)
        self.assertIn('"response_id":"r-a"', events)
        self.assertEqual(telemetry.summary("2026-10-26")["totals"]["total_tokens"], 7)

    def test_retention_after_many_updates_preserves_events_and_unknown_entries(self):
        path = self.source / "thread.jsonl"
        rows = [context("turn-0"), response("turn-0", "r-0", "2026-10-26T10:00:00Z", usage(1, 1), "t", "s")]
        write_rollout(path, "t", "s", self.project, rows)
        logs = self.root / "logs-many-updates"
        telemetry = self.addCleanupCollector(self.collector(logs_dir=logs))
        telemetry.sync()
        revisions = logs / "2026-10-26" / "revisions"
        (revisions / "keep-me").mkdir()
        (revisions / "keep-me" / "note.txt").write_text("user data", encoding="utf-8")
        outside = self.root / "outside-generation"
        outside.mkdir()
        try:
            (revisions / "linked-generation").symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")

        for index in range(1, 5):
            with path.open("a", encoding="utf-8") as stream:
                turn_context = context(f"turn-{index}")
                turn_context["timestamp"] = f"2026-10-26T10:0{index}:00Z"
                event = response(f"turn-{index}", f"r-{index}", f"2026-10-26T10:0{index}:00Z", usage(1, 1), "t", "s")
                event["timestamp"] = event["payload"]["timestamp"]
                stream.write(json.dumps(turn_context) + "\n")
                stream.write(json.dumps(event) + "\n")
            telemetry.sync()

        numeric = [entry for entry in revisions.iterdir() if entry.name.isdigit()]
        self.assertLessEqual(len(numeric), 2)
        self.assertEqual((revisions / "keep-me" / "note.txt").read_text(encoding="utf-8"), "user data")
        self.assertTrue((revisions / "linked-generation").is_symlink())
        self.assertTrue(outside.is_dir())
        self.assertEqual(telemetry.summary("2026-10-26")["totals"]["total_tokens"], 5)
        manifest = json.loads((logs / "2026-10-26" / "current.json").read_text(encoding="utf-8"))
        exported = (logs / "2026-10-26" / manifest["events"]).read_text(encoding="utf-8")
        for index in range(5):
            self.assertIn(f'"response_id":"r-{index}"', exported)
        with sqlite3.connect(telemetry.directory / "telemetry.sqlite3") as db:
            persisted = [json.loads(row[0]) for row in db.execute("SELECT data FROM events")]
        self.assertEqual(sum(event["kind"] == "token_usage_record" for event in persisted), 5)

    def test_publication_failure_before_manifest_swap_keeps_previous_generation_and_sync_repairs(self):
        write_rollout(self.source / "thread.jsonl", "t", "s", self.project,
                      [context("turn-a"), response("turn-a", "r-a", "2026-10-26T10:00:00Z", usage(4), "t", "s")])
        logs = self.root / "logs-atomic"
        telemetry = self.addCleanupCollector(self.collector(logs_dir=logs))
        telemetry.sync()
        manifest_path = logs / "2026-10-26" / "current.json"
        previous = manifest_path.read_bytes()
        with (self.source / "thread.jsonl").open("a", encoding="utf-8") as stream:
            event = response("turn-b", "r-b", "2026-10-26T11:00:00Z", usage(6), "t", "s")
            event["timestamp"] = event["payload"]["timestamp"]
            stream.write(json.dumps(event) + "\n")
        original_write = telemetry._write
        def fail_before_manifest(path, content):
            if Path(path).name == "current.json":
                raise OSError("injected publication failure")
            return original_write(path, content)
        with patch.object(telemetry, "_write", side_effect=fail_before_manifest):
            with self.assertRaises(OSError):
                telemetry.sync()
        self.assertEqual(manifest_path.read_bytes(), previous)
        self.assertEqual(telemetry.summary("2026-10-26")["totals"]["total_tokens"], 10)
        telemetry.sync()
        repaired = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertNotEqual(repaired, json.loads(previous))
        self.assertEqual(telemetry.summary("2026-10-26")["totals"]["total_tokens"], 10)

    def test_exports_whitelisted_metadata_without_conversation_canary(self):
        canary = "PRIVATE_CONVERSATION_CANARY_7f31"
        event = response("turn", "r", "2026-10-26T10:00:00Z", usage(4), "t", "s")
        event["payload"]["assistant_message"] = canary
        write_rollout(self.source / "thread.jsonl", "t", "s", self.project, [context("turn"), event])
        telemetry = self.addCleanupCollector(self.collector())
        telemetry.sync()
        exported = "".join(p.read_text(encoding="utf-8") for p in (self.project / ".model-router").rglob("*.json*"))
        self.assertNotIn(canary, exported)

    def test_concurrent_collectors_do_not_duplicate_responses(self):
        write_rollout(self.source / "thread.jsonl", "t", "s", self.project,
                      [context("turn"), response("turn", "r", "2026-10-26T10:00:00Z", usage(17), "t", "s")])
        collectors = [self.addCleanupCollector(self.collector()) for _ in range(2)]
        errors = []
        def sync(collector):
            try:
                collector.sync()
            except Exception as exc:
                errors.append(exc)
        threads = [threading.Thread(target=sync, args=(collector,)) for collector in collectors]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(collectors[0].summary("2026-10-26")["totals"]["total_tokens"], 17)

    def test_status_transitions_completed_reused_running_then_stale(self):
        events = [{"type": "event_msg", "timestamp": "2026-10-26T10:00:00Z", "payload": {"type": "task_started", "turn_id": "a"}},
                  {"type": "event_msg", "timestamp": "2026-10-26T10:01:00Z", "payload": {"type": "task_complete", "turn_id": "a"}},
                  {"type": "event_msg", "timestamp": "2026-10-26T10:02:00Z", "payload": {"type": "task_started", "turn_id": "b"}}]
        write_rollout(self.source / "thread.jsonl", "t", "s", self.project, events)
        telemetry = self.addCleanupCollector(self.collector())
        telemetry.sync()
        agent = telemetry.status(now=datetime(2026, 10, 26, 10, 2, tzinfo=timezone.utc))["agents"][0]
        self.assertEqual(agent["status"], "observed_running")
        stale = telemetry.status(stale_after=1, now=datetime(2026, 10, 26, 10, 3, tzinfo=timezone.utc))["agents"][0]
        self.assertEqual(stale["status"], "stale")

    def test_session_filter_only_filters_query_and_does_not_delete_other_sessions(self):
        for index, key in enumerate(("a", "b")):
            write_rollout(self.source / f"{key}.jsonl", key, f"s-{key}", self.project,
                          [context("turn"), response("turn", f"r-{key}", f"2026-10-26T10:0{index}:00Z", usage(3), key, f"s-{key}")])
        telemetry = self.addCleanupCollector(self.collector())
        telemetry.sync()
        self.assertEqual(len(telemetry.status(session_id="s-a")["agents"]), 1)
        self.assertEqual(len(telemetry.status()["agents"]), 2)

    def test_publication_freshness_is_scoped_to_each_logs_root_and_summary_exposes_it(self):
        path = self.source / "thread.jsonl"
        write_rollout(path, "t", "s", self.project,
                      [context("turn-a"), response("turn-a", "r-a", "2026-10-26T10:00:00Z", usage(10), "t", "s")])
        logs_a, logs_b = self.root / "logs-a", self.root / "logs-b"
        first = self.addCleanupCollector(self.collector(logs_dir=logs_a))
        first.sync()
        manifest_a = json.loads((logs_a / "2026-10-26" / "current.json").read_text(encoding="utf-8"))
        with path.open("a", encoding="utf-8") as stream:
            event = response("turn-b", "r-b", "2026-10-26T11:00:00Z", usage(5), "t", "s")
            event["timestamp"] = event["payload"]["timestamp"]
            stream.write(json.dumps(event) + "\n")
        second = self.addCleanupCollector(self.collector(logs_dir=logs_b))
        second.sync()
        status_a, status_b = first.status(), second.status()
        self.assertTrue(status_a["publication_lag"])
        self.assertEqual(status_a["published_revision"], manifest_a["revision"])
        self.assertFalse(status_b["publication_lag"])
        summary = second.summary("2026-10-26")
        for field in ("last_sync", "published_revision", "publication_lag"):
            self.assertIn(field, summary)
        rendered = telemetry_cli.render(summary)
        self.assertIn(str(summary["revision"]), rendered)
        self.assertTrue(any(word in rendered.lower() for word in ("lag", "publication", "sync")))

    def test_invocation_without_thread_contributes_requested_model_without_fake_agent(self):
        telemetry = self.addCleanupCollector(self.collector())
        telemetry.record_invocation({
            "invocation_id": "inv-no-thread", "task_id": "task-no-thread",
            "model_requested": "gpt-6.1-sol", "reasoning_effort_requested": "medium",
            "started_at": "2026-10-26T10:00:00Z", "status": "success",
        })
        status = telemetry.status()
        self.assertEqual(status["models_requested"], ["gpt-6.1-sol"])
        self.assertEqual(status["counts"]["models_requested"], 1)
        self.assertEqual(status["agents"], [])

    def test_reconciliation_upgrade_preserves_legacy_rows_cursor_and_republishes_once(self):
        write_rollout(self.source / "thread.jsonl", "t", "s", self.project,
                      [context("turn"), response("turn", "r", "2026-10-26T10:00:00Z", usage(10), "t", "s")])
        telemetry = self.addCleanupCollector(self.collector())
        telemetry.sync()
        store = self.project / ".model-router" / "telemetry.sqlite3"
        initial_revision = int(telemetry._state("revision"))
        initial_manifest = json.loads((telemetry.logs / "2026-10-26" / "current.json").read_text(encoding="utf-8"))
        telemetry.close()

        with sqlite3.connect(store) as legacy:
            legacy.execute("DELETE FROM state WHERE key='reconciliation_version'")
            legacy.commit()
            before = {table: legacy.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                      for table in ("events", "cursors", "observations")}

        migrated = self.addCleanupCollector(Telemetry(self.project, sources=[self.source]))
        self.assertEqual(int(migrated._state("revision")), initial_revision + 1)
        with sqlite3.connect(store) as upgraded:
            after_migration = {table: upgraded.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                               for table in ("events", "cursors", "observations")}
        self.assertEqual(after_migration, before)
        migrated.sync()
        manifest = json.loads((migrated.logs / "2026-10-26" / "current.json").read_text(encoding="utf-8"))
        self.assertGreater(manifest["revision"], initial_manifest["revision"])
        self.assertEqual(manifest["revision"], migrated.status()["published_revision"])
        self.assertEqual(migrated.summary("2026-10-26")["totals"]["total_tokens"], 10)

        reopened = self.addCleanupCollector(Telemetry(self.project, sources=[self.source]))
        self.assertEqual(int(reopened._state("revision")), manifest["revision"])
        reopened.sync()
        self.assertEqual(int(reopened._state("revision")), manifest["revision"])

    def test_installer_copies_telemetry_and_installed_cli_imports_without_repo_path(self):
        destination = self.root / "installed model router"
        install_script = Path(__file__).resolve().parents[1] / "install.sh"
        import subprocess
        installed = subprocess.run(["sh", str(install_script), "--dest", str(destination)],
                                   text=True, capture_output=True, timeout=30, check=False)
        self.assertEqual(installed.returncode, 0, installed.stderr)
        cli = destination / "scripts" / "telemetry.py"
        self.assertTrue(cli.is_file())
        environment = dict(os.environ)
        environment.pop("PYTHONPATH", None)
        help_result = subprocess.run([sys.executable, str(cli), "--help"], cwd=self.root,
                                     env=environment, text=True, capture_output=True,
                                     timeout=10, check=False)
        self.assertEqual(help_result.returncode, 0, help_result.stderr)


if __name__ == "__main__":
    unittest.main()
