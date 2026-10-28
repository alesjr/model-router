"""Project-local incremental telemetry, transactional storage and publication."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone as utc_timezone
from functools import wraps
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import uuid
from zoneinfo import ZoneInfo

from telemetry_records import FIELDS, cumulative_records, digest, encode, normalize, stamp, text, timestamp, totals
from telemetry_settings import require_enabled


def snapshot(method):
    """Keep a multi-query public read on one SQLite snapshot."""
    @wraps(method)
    def read(self, *args, **kwargs):
        with self.lock:
            owns_transaction = not self.db.in_transaction
            if owns_transaction:
                self.db.execute("BEGIN")
            try:
                self.timezone = self._state("timezone", self.timezone)
                self.zone = ZoneInfo(self.timezone)
                return method(self, *args, **kwargs)
            finally:
                if owns_transaction:
                    self.db.rollback()
    return read


class Telemetry:
    def __init__(self, project: Path, sources=None, logs_dir=None, timezone="UTC"):
        self.project = Path(project).expanduser().resolve()
        require_enabled(self.project)
        if not self.project.is_dir():
            raise ValueError("Project must be an existing directory")
        self.zone = ZoneInfo(timezone)
        self.timezone = timezone
        home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
        self.sources = [Path(p).expanduser().resolve() for p in sources] if sources is not None else [home / "sessions", home / "archived_sessions"]
        self.directory = self.project / ".model-router"
        self.directory.mkdir(exist_ok=True)
        self.logs = (Path(logs_dir).expanduser() if logs_dir else self.directory / "logs").resolve()
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.directory / "telemetry.sqlite3", timeout=30, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA busy_timeout=30000;
            CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT OR IGNORE INTO state VALUES ('revision', '0');
            CREATE TABLE IF NOT EXISTS threads (id TEXT PRIMARY KEY, data TEXT NOT NULL, included INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS events (key TEXT PRIMARY KEY, data TEXT NOT NULL, ingested_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS cursors (path TEXT PRIMARY KEY, identity TEXT, offset INTEGER, fingerprint TEXT, thread_id TEXT);
            CREATE TABLE IF NOT EXISTS diagnostics (key TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS invocations (id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS source_generations (path TEXT PRIMARY KEY, generation INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS observations (
                path TEXT NOT NULL, generation INTEGER NOT NULL, offset INTEGER NOT NULL,
                event_key TEXT NOT NULL, PRIMARY KEY(path,generation,offset,event_key));
            CREATE TABLE IF NOT EXISTS publications (
                root TEXT PRIMARY KEY, revision INTEGER NOT NULL, timezone TEXT NOT NULL, last_sync TEXT);
        """)
        self.db.commit()
        self._migrate_provenance()
        self._migrate_reconciliation()
        stored_zone = self._state("timezone")
        if stored_zone and stored_zone != timezone:
            if timezone != "UTC":
                self.db.close()
                raise ValueError("Timezone differs from stored snapshot; regenerate exports explicitly")
            # A default reader follows the project's stored grouping zone.
            self.timezone = stored_zone
            self.zone = ZoneInfo(stored_zone)
        elif not stored_zone:
            with self.db:
                self._set("timezone", timezone)

    def close(self):
        with self.lock:
            self.db.close()

    def _state(self, key, default=None):
        row = self.db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def _set(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO state VALUES (?,?)", (key, str(value)))

    def _migrate_provenance(self):
        """Replay sources once, without assigning fabricated provenance to legacy events."""
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            if self._state("telemetry_schema_version") == "2":
                return
            existing = self.db.execute("SELECT count(*) FROM events").fetchone()[0]
            self.db.execute("DELETE FROM cursors")
            self._set("telemetry_schema_version", "2")
            if existing or int(self._state("revision", "0")):
                self._set("revision", int(self._state("revision", "0")) + 1)
                self._diagnose("provenance_migration_pending", legacy_events=existing)

    def _freshness(self):
        row = self.db.execute("SELECT revision,timezone FROM publications WHERE root=?", (str(self.logs),)).fetchone()
        revision = int(self._state("revision", "0"))
        published = row[0] if row else 0
        return {"last_sync": self._state("last_sync"), "published_revision": published,
                "publication_lag": published != revision or bool(row and row[1] != self.timezone),
                "logs_dir": str(self.logs)}

    def _migrate_reconciliation(self):
        """Invalidate derived exports once when the overlap algorithm changes."""
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            if self._state("reconciliation_version") == "2":
                return
            has_content = (int(self._state("revision", "0")) > 0 or
                           self.db.execute("SELECT EXISTS(SELECT 1 FROM events)").fetchone()[0] or
                           self.db.execute("SELECT EXISTS(SELECT 1 FROM invocations)").fetchone()[0])
            self._set("reconciliation_version", "2")
            if has_content:
                self._set("revision", int(self._state("revision", "0")) + 1)
                self._set("reconciliation_publication_epoch", int(self._state("reconciliation_publication_epoch", "0")) + 1)

    def _diagnose(self, code, **location):
        data = {"code": code, **location}
        self.db.execute("INSERT OR IGNORE INTO diagnostics VALUES (?,?)", (digest(encode(data).encode()), encode(data)))

    def _diagnostics(self):
        return [json.loads(r[0]) for r in self.db.execute("SELECT data FROM diagnostics ORDER BY key")]

    def _source_gaps(self, selected, unfiltered):
        """Only proven unrelated sources can be excluded from a coverage claim."""
        blocking = {"invalid_json", "source_unreadable", "unproven_thread", "invalid_timestamp"}
        gaps = []
        for diagnostic in self._diagnostics():
            if diagnostic["code"] not in blocking:
                continue
            thread = diagnostic.get("thread_id")
            path = diagnostic.get("path")
            if thread is None and path:
                row = self.db.execute("SELECT thread_id FROM cursors WHERE path=?", (path,)).fetchone()
                thread = row[0] if row else None
            if thread in selected or (thread is None and unfiltered):
                gaps.append(diagnostic)
        return gaps

    def _paths(self):
        found = set()
        for source in self.sources:
            if source.is_file():
                found.add(source)
            elif source.is_dir():
                found.update(source.rglob("*.jsonl"))
        return sorted(p for p in found if not any(root == p or root in p.parents for root in (self.directory, self.logs)))

    def regenerate_timezone(self, timezone):
        require_enabled(self.project)
        zone = ZoneInfo(timezone)
        with self.lock, self.db:
            require_enabled(self.project)
            self.db.execute("BEGIN IMMEDIATE")
            self.timezone, self.zone = timezone, zone
            self._set("timezone", timezone)
            self._set("revision", int(self._state("revision")) + 1)

    def _content_signature(self):
        return digest(encode({table: [tuple(row) for row in self.db.execute(f"SELECT * FROM {table} ORDER BY 1")]
                              for table in ("threads", "events", "diagnostics", "invocations", "observations", "source_generations")}).encode())

    def _metadata(self, paths):
        """Discover graph first; excluded files never consume an ingestion cursor."""
        file_threads = {}
        for path in paths:
            try:
                with path.open("rb") as stream:
                    for line in stream:
                        if not line.endswith(b"\n"):
                            break
                        try:
                            row = json.loads(line)
                        except (ValueError, UnicodeError):
                            continue
                        if not isinstance(row, dict) or row.get("type") != "session_meta":
                            continue
                        p = row.get("payload")
                        if not isinstance(p, dict) or not text(p.get("id")):
                            continue
                        tid = p["id"]
                        file_threads[path] = tid
                        source = p.get("source")
                        parent = None
                        if isinstance(source, dict):
                            subagent = source.get("subagent")
                            if isinstance(subagent, dict):
                                for shape in ("thread_spawn", "spawn"):
                                    spawn = subagent.get(shape)
                                    parent = text(spawn.get("parent_thread_id")) if isinstance(spawn, dict) else None
                                    if parent:
                                        break
                        cwd = text(p.get("cwd"))
                        inside = False
                        if cwd:
                            resolved = Path(cwd).expanduser().resolve()
                            inside = resolved == self.project or self.project in resolved.parents
                        data = {"thread_id": tid, "session_id": text(p.get("session_id")), "parent_thread_id": parent,
                                "root_observed": not parent, "last_seen": None}
                        old = self.db.execute("SELECT data,included FROM threads WHERE id=?", (tid,)).fetchone()
                        if old:
                            previous = json.loads(old[0])
                            for key in ("session_id", "parent_thread_id"):
                                if previous.get(key) and data.get(key) and previous[key] != data[key]:
                                    self._diagnose("identity_conflict", thread_id=tid, field=key)
                                    data[key] = previous[key]
                                elif not data.get(key):
                                    data[key] = previous.get(key)
                            data["root_observed"] = previous.get("root_observed", False) and not data["parent_thread_id"]
                        self.db.execute("INSERT OR REPLACE INTO threads VALUES (?,?,?)", (tid, encode(data), int(inside or bool(old and old[1]))))
                        break
            except OSError:
                self._diagnose("source_unreadable", path=str(path))
        changed = True
        while changed:
            changed = False
            rows = list(self.db.execute("SELECT * FROM threads"))
            included = {r["id"] for r in rows if r["included"]}
            for row in rows:
                if not row["included"] and json.loads(row["data"]).get("parent_thread_id") in included:
                    self.db.execute("UPDATE threads SET included=1 WHERE id=?", (row["id"],))
                    changed = True
        return file_threads

    def _ingest(self, path, thread):
        try:
            with path.open("rb") as stream:
                stat = os.fstat(stream.fileno())
                identity = f"{stat.st_dev}:{stat.st_ino}:{stat.st_mtime_ns}:{stat.st_size}"
                cursor = self.db.execute("SELECT * FROM cursors WHERE path=?", (str(path),)).fetchone()
                offset = cursor["offset"] if cursor else 0
                previous_identity = cursor["identity"].split(":") if cursor else []
                replaced = cursor and (previous_identity[:2] != identity.split(":")[:2] or stat.st_size < offset or
                                       (stat.st_size == offset and previous_identity[2:] != identity.split(":")[2:]) or
                                       self._fingerprint(stream, offset) != cursor["fingerprint"])
                if replaced:
                    offset = 0
                    self._diagnose("source_replaced_or_truncated", path=str(path))
                row = self.db.execute("SELECT generation FROM source_generations WHERE path=?", (str(path),)).fetchone()
                generation = row[0] if row else 0
                if replaced:
                    generation += 1
                self.db.execute("INSERT OR REPLACE INTO source_generations VALUES (?,?)", (str(path), generation))
                stream.seek(offset)
                while True:
                    line = stream.readline()
                    if not line or not line.endswith(b"\n"):
                        break
                    offset = stream.tell()
                    try:
                        row = json.loads(line)
                    except (ValueError, UnicodeError):
                        self._diagnose("invalid_json", path=str(path), offset=offset, thread_id=thread)
                        continue
                    primary = normalize(row, thread)
                    for event in ([primary] if primary else []) + cumulative_records(row, thread):
                        # A foreign explicit thread cannot smuggle usage into this project.
                        known = self.db.execute("SELECT included FROM threads WHERE id=?", (event["thread_id"],)).fetchone()
                        if not known or not known[0]:
                            self._diagnose("unproven_thread", path=str(path), offset=offset)
                            continue
                        if not event["timestamp"]:
                            self._diagnose("invalid_timestamp", thread_id=thread, offset=offset)
                        serial = encode(event)
                        key = digest(serial.encode())
                        self.db.execute("INSERT OR IGNORE INTO events VALUES (?,?,?)", (key, serial, stamp()))
                        self.db.execute("INSERT OR IGNORE INTO observations VALUES (?,?,?,?)", (str(path), generation, offset, key))
                fingerprint = self._fingerprint(stream, offset)
                self.db.execute("INSERT OR REPLACE INTO cursors VALUES (?,?,?,?,?)", (str(path), identity, offset, fingerprint, thread))
        except OSError:
            self._diagnose("source_unreadable", path=str(path), thread_id=thread)

    @staticmethod
    def _fingerprint(stream, offset):
        stream.seek(0)
        head = stream.read(min(4096, offset))
        stream.seek(max(0, offset - 4096))
        return digest(head + stream.read(min(4096, offset)))

    def sync(self):
        require_enabled(self.project)
        with self.lock:
            require_enabled(self.project)
            self.db.execute("BEGIN IMMEDIATE")
            try:
                self.timezone = self._state("timezone", self.timezone)
                self.zone = ZoneInfo(self.timezone)
                before = self._content_signature()
                paths = self._paths()
                mapping = self._metadata(paths)
                included = {r[0] for r in self.db.execute("SELECT id FROM threads WHERE included=1")}
                for path, thread in mapping.items():
                    require_enabled(self.project)
                    if thread in included:
                        self._ingest(path, thread)
                # Revision describes the reconciliation snapshot, including metadata.
                revision = int(self._state("revision", "0")) + (self._content_signature() != before)
                self._set("revision", revision)
                self._set("last_sync", stamp())
                self.db.commit()
            except BaseException:
                self.db.rollback()
                raise
            # Serialize publication with ingestion so an older writer cannot win.
            self.db.execute("BEGIN IMMEDIATE")
            try:
                self._publish()
                self.db.execute("INSERT OR REPLACE INTO publications VALUES (?,?,?,?)",
                                (str(self.logs), int(self._state("revision")), self.timezone, self._state("last_sync")))
                self.db.commit()
            except BaseException:
                self.db.rollback()
                raise
            return {"revision": int(self._state("revision")), "diagnostics": self._diagnostics()}

    def _threads(self):
        return {r[0]: json.loads(r[1]) for r in self.db.execute("SELECT id,data FROM threads WHERE included=1")}

    def _events(self):
        provenance = defaultdict(list)
        for row in self.db.execute("SELECT event_key,path,generation,offset FROM observations ORDER BY path,generation,offset"):
            provenance[row[0]].append({"path": row[1], "generation": row[2], "offset": row[3]})
        return [{**json.loads(r[0]), "ingested_at": r[1], "provenance": provenance[r[2]]}
                for r in self.db.execute("SELECT data,ingested_at,key FROM events ORDER BY rowid")]

    @staticmethod
    def _response_versions(versions):
        """A later record supersedes only earlier records from that same source path."""
        latest, unattributed = {}, []
        for version in versions:
            if not version["provenance"]:
                unattributed.append(version)
            for origin in version["provenance"]:
                path = origin["path"]
                order = (origin["generation"], origin["offset"])
                if path not in latest or order > latest[path][0]:
                    latest[path] = (order, version)
        candidates = unattributed + [value[1] for value in latest.values()]
        unique = {}
        for version in candidates:
            # Provenance and ingestion time do not create extra response candidates.
            key = encode({k: v for k, v in version.items() if k not in {"provenance", "ingested_at"}})
            unique[key] = version
        return list(unique.values())

    def _selection(self, session_id):
        threads = self._threads()
        if session_id is None:
            return set(threads), None
        matches = {tid for tid, t in threads.items() if t["session_id"] == session_id}
        def descendants(initial):
            result = set(initial)
            while True:
                expanded = result | {tid for tid, t in threads.items() if t["parent_thread_id"] in result}
                if expanded == result:
                    return result
                result = expanded
        if session_id in threads and matches and descendants(matches) != descendants({session_id}):
            raise ValueError("Ambiguous session/thread identity")
        selected = {session_id} if session_id in threads else matches
        if not selected:
            raise ValueError("Unknown session/thread identity")
        while True:
            expanded = selected | {tid for tid, t in threads.items() if t["parent_thread_id"] in selected}
            if expanded == selected:
                break
            selected = expanded
        return selected, {"id": session_id, "kind": "thread" if session_id in threads else "session"}

    def _view(self):
        events = self._events()
        contexts = defaultdict(set)
        efforts = defaultdict(set)
        detailed = defaultdict(list)
        snapshots = defaultdict(list)
        for event in events:
            key = (event["thread_id"], event.get("turn_id"))
            if event["kind"] == "turn_context":
                if event.get("model_reported"):
                    contexts[key].add(event["model_reported"])
                if event.get("reasoning_effort_reported"):
                    efforts[key].add(event["reasoning_effort_reported"])
            elif event["kind"] == "token_usage_record":
                detailed[(event["thread_id"], event["response_id"])].append(event)
            elif event["kind"] == "token_count":
                for origin in event["provenance"] or [None]:
                    # Unattributed legacy checkpoints each remain a separate baseline.
                    path = origin["path"] if origin else "legacy:" + digest(encode(event).encode())
                    generation = origin["generation"] if origin else None
                    snapshots[(event["thread_id"], event["source"], event.get("turn_id"), path, generation)].append(event)
        records, coverage = [], []
        threads = self._threads()
        for key, versions in detailed.items():
            versions = self._response_versions(versions)
            e = dict(versions[-1])
            models = contexts[(key[0], e.get("turn_id"))]
            e.update(model_reported=next(iter(models)) if len(models) == 1 else None,
                     reasoning_effort_reported=next(iter(efforts[(key[0], e.get("turn_id"))])) if len(efforts[(key[0], e.get("turn_id"))]) == 1 else None,
                     coverage="detailed", source="native", reconciliation_revision=int(self._state("revision")))
            if len({encode(v["usage"]) for v in versions}) > 1:
                e["coverage"] = "conflicting_response_versions"
                # No source precedence: conflicting dimensions stay unknown.
                e["usage"] = {field: versions[0]["usage"][field] if len({v["usage"][field] for v in versions}) == 1 else None for field in FIELDS}
                coverage.append({"code": "response_conflict", "thread_id": key[0], "response_id": key[1]})
            if len(models) > 1:
                coverage.append({"code": "ambiguous_model", "thread_id": key[0], "turn_id": e.get("turn_id")})
            if any(not v["provenance"] for v in versions):
                coverage.append({"code": "legacy_response_provenance_unknown", "thread_id": key[0], "response_id": key[1]})
            if len({v["timestamp"] for v in versions}) > 1:
                e["timestamp"] = None
                coverage.append({"code": "conflicting_response_timestamp", "thread_id": key[0], "response_id": key[1]})
            records.append(e)
        detailed_threads = {r["thread_id"] for r in records}
        snapshot_threads = {key[0] for key in snapshots}
        preferred_source = {thread: next(source for source in ("thread_token_usage", "token_count", "turn_token_usage")
                                         if any(key[0] == thread and key[1] == source for key in snapshots)) for thread in snapshot_threads}
        consumed_edges = set()
        interval_candidates = defaultdict(list)
        for (thread, source, turn, path, generation), checkpoints in snapshots.items():
            if source != preferred_source[thread]:
                coverage.append({"code": "alternative_checkpoint_source_non_additive", "thread_id": thread, "source": source})
                continue
            checkpoints = list({encode([e["timestamp"], e["usage"]]): e for e in checkpoints}.values())
            checkpoints.sort(key=lambda e: e["timestamp"] or "")
            by_time = defaultdict(set)
            for checkpoint in checkpoints:
                by_time[checkpoint["timestamp"]].add(encode(checkpoint["usage"]))
            if any(len(values) > 1 for values in by_time.values()):
                coverage.append({"code": "conflicting_checkpoint", "thread_id": thread, "source": source})
                continue
            baseline = checkpoints[0]
            coverage.append({"code": "baseline", "thread_id": thread, "usage": baseline["usage"], "timestamp": baseline["timestamp"],
                             "path": path if generation is not None else None, "generation": generation})
            for previous, current in zip(checkpoints, checkpoints[1:]):
                edge = encode([thread, source, turn, previous["timestamp"], previous["usage"], current["timestamp"], current["usage"]])
                if edge in consumed_edges:
                    continue
                consumed_edges.add(edge)
                delta = {f: current["usage"][f] - previous["usage"][f] if current["usage"][f] is not None and previous["usage"][f] is not None else None for f in FIELDS}
                if any(v is not None and v < 0 for v in delta.values()):
                    coverage.append({"code": "counter_reset_or_gap", "thread_id": thread})
                    continue
                start, end = timestamp(previous["timestamp"]), timestamp(current["timestamp"])
                if thread in detailed_threads or start is None or end is None or start.astimezone(self.zone).date() != end.astimezone(self.zone).date():
                    coverage.append({"code": "non_additive" if thread in detailed_threads else "unallocated_interval", "thread_id": thread, "start": previous["timestamp"], "end": current["timestamp"], "usage": delta})
                else:
                    interval_key = (thread, source, turn)
                    candidate = {**current, "usage": delta, "coverage": "cumulative_delta_partial", "model_reported": None,
                                 "response_id": None, "source": source,
                                 "checkpoint_interval": {"start": previous["timestamp"], "end": current["timestamp"], "path": path, "generation": generation}}
                    interval_candidates[interval_key].append((start, end, candidate))
        # Allocation follows reconciliation of the complete candidate set. There is no
        # source precedence that would justify retaining whichever interval arrived first.
        for (thread, source, turn), candidates in interval_candidates.items():
            candidates.sort(key=lambda candidate: (candidate[0], candidate[1]))
            conflicting = set()
            for index, (start, end, _) in enumerate(candidates):
                for other_index in range(index + 1, len(candidates)):
                    other_start, other_end, _ = candidates[other_index]
                    if other_start >= end:
                        break
                    if start < other_end:
                        conflicting.update((index, other_index))
            for index, (_, _, candidate) in enumerate(candidates):
                if index in conflicting:
                    coverage.append({"code": "overlapping_checkpoint_interval_non_additive", "thread_id": thread,
                                     "source": source, "turn_id": turn, **candidate["checkpoint_interval"], "usage": candidate["usage"]})
                else:
                    records.append(candidate)
        for event in events:
            if event["kind"] == "unidentified_usage":
                coverage.append({"code": "unidentified_usage_non_additive", "thread_id": event["thread_id"], "usage": event["usage"]})
        for row in self.db.execute("SELECT data FROM invocations"):
            invocation = json.loads(row[0])
            if invocation.get("exec_usage"):
                coverage.append({"code": "exec_usage_non_additive_unmatched_native_coverage", "thread_id": invocation.get("thread_id"),
                                 "invocation_id": invocation["invocation_id"], "usage": invocation["exec_usage"]})
        for record in records:
            u = record["usage"]
            if all(u[k] is not None for k in ("input_tokens", "output_tokens", "total_tokens")) and u["total_tokens"] != u["input_tokens"] + u["output_tokens"]:
                coverage.append({"code": "inconsistent_total", "thread_id": record["thread_id"], "response_id": record.get("response_id")})
            t = threads.get(record["thread_id"], {})
            record.update(schema_version=1, project=str(self.project), session_id=self._session(record["thread_id"], threads),
                          parent_thread_id=t.get("parent_thread_id"), model_verified=False,
                          revision=int(self._state("revision")), timezone=self.timezone)
            record.update(self._association(record["thread_id"], record["timestamp"]))
            record["session_provenance"] = "observed_grouping" if t.get("session_id") else "derived_tree"
        return records, coverage, contexts, efforts

    def _association(self, thread, at):
        candidates = [json.loads(row[0]) for row in self.db.execute("SELECT data FROM invocations")]
        candidates = [i for i in candidates if i.get("thread_id") == thread and at and i["started_at"] <= at and
                      (not i.get("finished_at") or at <= i["finished_at"])]
        item = candidates[0] if len(candidates) == 1 else {}
        return {"task_id": item.get("task_id"), "invocation_id": item.get("invocation_id"),
                "model_requested": item.get("model_requested"), "reasoning_effort_requested": item.get("reasoning_effort_requested")}

    @staticmethod
    def _session(thread, threads):
        seen = set()
        current = thread
        while current in threads and current not in seen:
            seen.add(current)
            t = threads[current]
            if t.get("session_id"):
                return t["session_id"]
            if not t.get("parent_thread_id"):
                return current
            current = t["parent_thread_id"]
        return thread

    @snapshot
    def summary(self, date, session_id=None):
        datetime.strptime(date, "%Y-%m-%d")
        with self.lock:
            selected, resolved = self._selection(session_id)
            all_records, coverage, _, _ = self._view()
            records = [r for r in all_records if r["thread_id"] in selected and timestamp(r["timestamp"]) is not None and timestamp(r["timestamp"]).astimezone(self.zone).date().isoformat() == date]
            by_model = defaultdict(list)
            by_session = defaultdict(list)
            for r in records:
                by_model[r.get("model_reported") or "unknown"].append(r)
                by_session[r["session_id"]].append(r)
            sessions = {}
            roles = defaultdict(list)
            thread_metadata = self._threads()
            router_threads = {json.loads(row[0]).get("thread_id") for row in self.db.execute("SELECT data FROM invocations")}
            for r in records:
                t = thread_metadata.get(r["thread_id"], {})
                role = "worker" if t.get("parent_thread_id") or r["thread_id"] in router_threads else "coordinator" if t.get("root_observed") else "unknown"
                roles[role].append(r)
            for sid, values in by_session.items():
                models = defaultdict(list)
                for r in values:
                    models[r.get("model_reported") or "unknown"].append(r)
                sessions[sid] = {"totals": totals(values), "by_model": {m: totals(v) for m, v in models.items()}}
            relevant = [c for c in coverage if c["thread_id"] in selected]
            gaps = self._source_gaps(selected, session_id is None)
            missing_time = any(r["thread_id"] in selected and not r["timestamp"] for r in all_records)
            complete = bool(records) and not relevant and not gaps and not missing_time and all(r["coverage"] == "detailed" and r.get("model_reported") and all(v is not None for v in r["usage"].values()) for r in records)
            return {"schema_version": 1, "date": date, "timezone": self.timezone, "revision": int(self._state("revision")),
                    **self._freshness(),
                    "totals": totals(records), "by_model": {m: totals(v) for m, v in by_model.items()}, "by_session": sessions,
                    "by_role": {role: totals(values) for role, values in roles.items()}, "coordinator_totals": totals(roles["coordinator"]),
                    "coverage": {"complete": bool(complete), "baseline": [c for c in relevant if c["code"] == "baseline"],
                                 "non_additive": [c for c in relevant if c["code"] != "baseline"], "undated_responses": missing_time,
                                 "source_gaps": gaps, "diagnostics": self._diagnostics()}, "responses": records, "selection": resolved}

    @snapshot
    def status(self, session_id=None, stale_after=300, now=None):
        if stale_after < 0:
            raise ValueError("stale_after must be nonnegative")
        now = now or datetime.now(utc_timezone.utc)
        with self.lock:
            selected, resolved = self._selection(session_id)
            threads = self._threads()
            events = self._events()
            records, coverage, contexts, efforts = self._view()
            invocations = [json.loads(r[0]) for r in self.db.execute("SELECT data FROM invocations ORDER BY rowid")]
            agents = []
            for tid in sorted(selected):
                t = threads[tid]
                observed = sorted([e for e in events if e["thread_id"] == tid], key=lambda e: e["timestamp"] or "")
                lifecycle = [e for e in observed if e["kind"] in {"task_started", "task_complete", "turn_aborted"}]
                last = observed[-1] if observed else {}
                state = {"task_started": "observed_running", "task_complete": "completed", "turn_aborted": "failed"}.get(lifecycle[-1]["kind"], "unknown") if lifecycle else "unknown"
                linked = [i for i in invocations if i.get("thread_id") == tid]
                invocation = linked[-1] if linked else {}
                invocation_time = invocation.get("finished_at") or invocation.get("started_at")
                if invocation and (not lifecycle or (invocation_time or "") > (lifecycle[-1].get("timestamp") or "")):
                    state = {"success": "completed", "failed": "failed", "timeout": "failed", "unavailable": "failed",
                             "observed_running": "observed_running"}.get(invocation.get("status"), "unknown")
                turn = lifecycle[-1].get("turn_id") if lifecycle else last.get("turn_id")
                models = contexts[(tid, turn)]
                effort = efforts[(tid, turn)]
                last_seen = max([e["timestamp"] for e in observed if e["timestamp"]] + [invocation.get("finished_at") or invocation.get("started_at") or ""], default="") or None
                base_state = state
                if timestamp(last_seen) and (now - timestamp(last_seen)).total_seconds() > stale_after:
                    state = "stale"
                agents.append({"thread_id": tid, "session_id": self._session(tid, threads), "parent_thread_id": t["parent_thread_id"],
                               "origin": "router" if linked else "native", "role": "worker" if linked or t["parent_thread_id"] else "coordinator" if t["root_observed"] else "unknown",
                               "status": state, "observed_status": base_state, "status_provenance": "router_invocation" if invocation and invocation_time and (not lifecycle or invocation_time > (lifecycle[-1].get("timestamp") or "")) else "native_lifecycle", "turn_id": turn, "model_reported": next(iter(models)) if len(models) == 1 else None,
                               "model_requested": invocation.get("model_requested"), "reasoning_effort_requested": invocation.get("reasoning_effort_requested"),
                               "reasoning_effort_reported": next(iter(effort)) if len(effort) == 1 else None, "model_verified": False,
                               "last_seen": last_seen, "task_id": invocation.get("task_id"), "invocation_id": invocation.get("invocation_id")})
            reported = sorted({m for (tid, _), values in contexts.items() if tid in selected for m in values})
            requested = sorted({i["model_requested"] for i in invocations if text(i.get("model_requested")) and
                                (session_id is None or i.get("thread_id") in selected)})
            selected_records = [r for r in records if r["thread_id"] in selected]
            by_model = defaultdict(list)
            for record in selected_records:
                by_model[record.get("model_reported") or "unknown"].append(record)
            coordinator_ids = {a["thread_id"] for a in agents if a["role"] == "coordinator"}
            revision = int(self._state("revision"))
            return {"schema_version": 1, "revision": revision, **self._freshness(),
                    "timezone": self.timezone, "agents": agents, "selection": resolved,
                    "counts": {"agents": len(agents), "workers": sum(a["role"] == "worker" for a in agents), "coordinators": sum(a["role"] == "coordinator" for a in agents),
                               "observed_running": sum(a["status"] == "observed_running" for a in agents), "models": len(reported), "models_requested": len(requested),
                               "invocations": sum(session_id is None or i.get("thread_id") in selected for i in invocations),
                               "workers_by_origin": {origin: sum(a["role"] == "worker" and a["origin"] == origin for a in agents) for origin in ("native", "router", "unknown")}},
                    "models_reported": reported, "models_requested": requested,
                    "usage_by_model": {m: totals(v) for m, v in by_model.items()},
                    "coordinator_totals": totals([r for r in selected_records if r["thread_id"] in coordinator_ids]),
                    "coverage": [c for c in coverage if c.get("thread_id") in selected], "diagnostics": self._diagnostics(),
                    "unscoped_threads": self.db.execute("SELECT count(*) FROM threads WHERE included=0").fetchone()[0]}

    def _write(self, path, content):
        require_enabled(self.project)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
        try:
            with temporary.open("w", encoding="utf-8") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            self._fsync_directory(path.parent)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _fsync_directory(path):
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _export_events(self, records):
        threads = self._threads()
        output = list(records)
        for event in self._events():
            if event["kind"] not in {"task_started", "task_complete", "turn_aborted"}:
                continue
            tid = event["thread_id"]
            if tid not in threads:
                continue
            t = threads[tid]
            output.append({**event, **self._association(tid, event["timestamp"]), "schema_version": 1,
                           "project": str(self.project), "session_id": self._session(tid, threads),
                           "parent_thread_id": t.get("parent_thread_id"), "model_verified": False, "source": "native",
                           "revision": int(self._state("revision")), "timezone": self.timezone,
                           "status": {"task_started": "observed_running", "task_complete": "completed", "turn_aborted": "failed"}[event["kind"]]})
        for row in self.db.execute("SELECT data FROM invocations ORDER BY id"):
            invocation = json.loads(row[0])
            for kind, field in (("invocation_started", "started_at"), ("invocation_finished", "finished_at")):
                at = invocation.get(field)
                if not at:
                    continue
                tid = invocation.get("thread_id")
                output.append({**invocation, "kind": kind, "timestamp": at, "status": "observed_running" if kind == "invocation_started" else invocation["status"],
                               "schema_version": 1, "project": str(self.project), "session_id": self._session(tid, threads) if tid else None,
                               "parent_thread_id": threads.get(tid, {}).get("parent_thread_id"), "turn_id": None,
                               "model_verified": False, "model_reported": None, "source": "router", "revision": int(self._state("revision")), "timezone": self.timezone})
        return sorted(output, key=lambda e: (e.get("timestamp") or "", e.get("thread_id") or "", e["kind"]))

    def _daily_digest(self, events, summary):
        # Strip publication clocks only at their defined locations. Coverage and
        # diagnostics remain part of the signature, including their nested fields.
        def response(record):
            return {k: v for k, v in record.items() if k not in {"revision", "reconciliation_revision"}}
        stable = {k: v for k, v in summary.items()
                  if k not in {"revision", "published_revision", "publication_lag", "last_sync", "responses"}}
        stable["responses"] = [response(record) for record in summary["responses"]]
        return digest(encode({"events": [response(event) for event in events], "summary": stable,
                              "reconciliation_version": self._state("reconciliation_version"),
                              "reconciliation_publication_epoch": self._state("reconciliation_publication_epoch", "0")}).encode())

    @staticmethod
    def _generation_name(name):
        return isinstance(name, str) and re.fullmatch(r"(?:0|[1-9][0-9]*)(?:-[0-9a-f]{32})?", name) is not None

    def _published_matches(self, directory, manifest, content_digest):
        if (not isinstance(manifest, dict) or manifest.get("schema_version") != 1
                or manifest.get("timezone") != self.timezone or manifest.get("content_digest") != content_digest):
            return False
        revision = manifest.get("revision")
        if type(revision) is not int or revision < 0:
            return False
        if not isinstance(manifest.get("events"), str):
            return False
        generation = manifest["events"].split("/")
        if (len(generation) != 3 or generation[0] != "revisions" or generation[2] != "events.jsonl"
                or not self._generation_name(generation[1]) or generation[1].split("-")[0] != str(revision)
                or manifest.get("summary") != f"revisions/{generation[1]}/summary.json"):
            return False
        for key in ("events", "summary"):
            path = directory / manifest[key]
            if any(parent.is_symlink() for parent in (directory, path.parent.parent, path.parent, path)):
                return False
            if digest(path.read_bytes()) != manifest.get(key + "_digest"):
                return False
        return True

    def _prune_generations(self, directory, manifest):
        """Best-effort retention; never traverse links or remove unknown entries."""
        revisions = directory / "revisions"
        try:
            if directory.is_symlink() or revisions.is_symlink():
                return
            current = manifest["events"].split("/")[1]
            if not self._generation_name(current):
                return
            entries = [p for p in revisions.iterdir()
                       if self._generation_name(p.name) and not p.is_symlink() and p.is_dir()]
            previous = manifest.get("previous_generation")
            if previous not in {p.name for p in entries} or previous == current:
                # Legacy manifests have no previous pointer. Future/orphan
                # generations do not qualify as the previous published revision.
                older = [p for p in entries if int(p.name.split("-")[0]) < int(current.split("-")[0])]
                previous = max(older, key=lambda p: (int(p.name.split("-")[0]), p.stat().st_mtime_ns), default=None)
                previous = previous.name if previous else None
            for entry in entries:
                if entry.name in {current, previous}:
                    continue
                children = list(entry.iterdir())
                if any(p.name not in {"events.jsonl", "summary.json"} or p.is_symlink() or not p.is_file()
                       for p in children):
                    continue
                for child in children:
                    child.unlink()
                entry.rmdir()
        except (OSError, ValueError, KeyError, TypeError):
            # Cleanup must not invalidate a successful publication checkpoint.
            return

    def _publish(self):
        require_enabled(self.project)
        records, coverage, _, _ = self._view()
        export_events = self._export_events(records)
        dates = {timestamp(r["timestamp"]).astimezone(self.zone).date().isoformat() for r in export_events if timestamp(r["timestamp"])}
        # Rebuild previously published days too, including days emptied by reconciliation.
        if self.logs.exists():
            dates.update(p.parent.name for p in self.logs.glob("????-??-??/current.json"))
        revision = int(self._state("revision"))
        for date in sorted(dates):
            summary = self.summary(date)
            # These files describe the generation that the following manifest publishes.
            # The root-level publication checkpoint is committed only after all days succeed.
            summary.update(published_revision=revision, publication_lag=False)
            directory = self.logs / date
            daily = [e for e in export_events if timestamp(e["timestamp"]) and timestamp(e["timestamp"]).astimezone(self.zone).date().isoformat() == date]
            content_digest = self._daily_digest(daily, summary)
            manifest = None
            try:
                manifest = json.loads((directory / "current.json").read_text(encoding="utf-8"))
                if self._published_matches(directory, manifest, content_digest):
                    self._prune_generations(directory, manifest)
                    continue
            except (OSError, ValueError, KeyError, TypeError):
                pass
            # A generation is immutable once referenced. Repairs receive a new directory.
            suffix = str(revision) if not (directory / "revisions" / str(revision)).exists() else f"{revision}-{uuid.uuid4().hex}"
            generation = f"revisions/{suffix}"
            staging = directory / "revisions" / (".staging-" + uuid.uuid4().hex)
            events_content = "".join(encode(r) + "\n" for r in daily)
            summary_content = encode(summary) + "\n"
            self._write(staging / "events.jsonl", events_content)
            self._write(staging / "summary.json", summary_content)
            os.rename(staging, directory / generation)
            self._fsync_directory(directory / "revisions")
            previous_path = manifest.get("events") if isinstance(manifest, dict) else None
            previous = previous_path.split("/") if isinstance(previous_path, str) else []
            published = {"schema_version": 1, "revision": revision, "timezone": self.timezone,
                         "content_digest": content_digest, "events_digest": digest(events_content.encode()),
                         "summary_digest": digest(summary_content.encode()),
                         "events": generation + "/events.jsonl", "summary": generation + "/summary.json"}
            if len(previous) == 3 and previous[0] == "revisions" and self._generation_name(previous[1]):
                published["previous_generation"] = previous[1]
            self._write(directory / "current.json", encode(published) + "\n")
            self._prune_generations(directory, published)
        self._write(self.logs / "coverage.json", encode({"schema_version": 1, "revision": revision, "timezone": self.timezone, "coverage": coverage}) + "\n")

    def record_invocation(self, invocation):
        """Accept only caller-produced safe metadata, never a task/prompt object."""
        require_enabled(self.project)
        allowed = {"invocation_id", "task_id", "task_type", "profile", "model_requested", "reasoning_effort_requested",
                   "thread_id", "started_at", "finished_at", "status", "returncode", "exec_usage", "usage_coverage"}
        data = {k: v for k, v in invocation.items() if k in allowed}
        with self.lock, self.db:
            require_enabled(self.project)
            self.db.execute("INSERT OR REPLACE INTO invocations VALUES (?,?)", (data["invocation_id"], encode(data)))
            tid = data.get("thread_id")
            if tid and not self.db.execute("SELECT 1 FROM threads WHERE id=?", (tid,)).fetchone():
                self.db.execute("INSERT INTO threads VALUES (?,?,1)", (tid, encode({"thread_id": tid, "session_id": None, "parent_thread_id": None, "root_observed": False})))
            self._set("revision", int(self._state("revision")) + 1)
