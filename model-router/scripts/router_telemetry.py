"""Optional streaming executor; telemetry failure never changes worker outcome."""

import json
import os
from pathlib import Path
import subprocess
import threading
import uuid

from telemetry_core import Telemetry
from telemetry_records import stamp, usage
from telemetry_settings import TelemetryDisabled, get_settings


def _plain_execute(command, prompt, result, timeout, settings):
    result["telemetry"] = {"enabled": False, "reason": settings["reason"]}
    try:
        completed = subprocess.run(command, input=prompt, text=True, capture_output=True,
                                   timeout=timeout, check=False)
    except FileNotFoundError:
        result.update(status="unavailable", stderr="Codex CLI indisponível")
    except subprocess.TimeoutExpired as exc:
        def output(value):
            return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value or ""
        result.update(status="timeout", stdout=output(exc.stdout), stderr=output(exc.stderr))
    else:
        result.update(status="success" if completed.returncode == 0 else "failed",
                      returncode=completed.returncode, stdout=completed.stdout, stderr=completed.stderr)
    return result


def execute(command, prompt, result, task, project, timeout):
    settings = get_settings(Path(project))
    if not settings["enabled"]:
        return _plain_execute(command, prompt, result, timeout, settings)
    invocation = {"invocation_id": str(uuid.uuid4()), "task_id": task["id"], "task_type": task["task_type"],
                  "profile": task["profile"], "model_requested": task["model"],
                  "reasoning_effort_requested": task["reasoning_effort"], "thread_id": None,
                  "started_at": stamp(), "status": "observed_running", "returncode": None}
    warnings = []
    messages, errors = [], []
    collector = None
    active = threading.Event()
    active.set()
    stopped = threading.Event()
    metadata_lock = threading.Lock()

    def warning(code):
        if code not in warnings:
            warnings.append(code)

    def disabled():
        active.clear()
        stopped.set()
        current = get_settings(Path(project))
        result["telemetry"] = {"enabled": False, "reason": current["reason"]}

    try:
        collector = Telemetry(Path(project))
        # Readers must not stall on another collector's long write transaction.
        collector.db.execute("PRAGMA busy_timeout=100")
        collector.record_invocation(invocation)
    except TelemetryDisabled:
        disabled()
        if collector:
            try:
                collector.close()
            except Exception:
                warning("telemetry_close_failed")
        return _plain_execute(command, prompt, result, timeout, get_settings(Path(project)))
    except Exception:
        warning("telemetry_storage_unavailable")

    command = [*command[:-1], "--json", command[-1]]

    def observe(event):
        if not isinstance(event, dict):
            return
        if not stopped.is_set() and not get_settings(Path(project))["enabled"]:
            disabled()
        kind = event.get("type")
        if kind == "thread.started" and isinstance(event.get("thread_id"), str):
            with metadata_lock:
                invocation["thread_id"] = event["thread_id"]
                result["thread_id"] = event["thread_id"]
                if collector and active.is_set():
                    try:
                        collector.record_invocation(dict(invocation))
                    except TelemetryDisabled:
                        disabled()
                    except Exception:
                        warning("telemetry_thread_link_pending")
        elif kind == "item.completed":
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") == "agent_message" and isinstance(item.get("text"), str):
                messages.append(item["text"])
        elif kind == "turn.completed" and isinstance(event.get("usage"), dict) and not stopped.is_set():
            # Public exec usage cannot be proven to match detailed rollout requests.
            warning("exec_usage_non_additive_unmatched_native_coverage")
            with metadata_lock:
                invocation["exec_usage"] = usage(event["usage"])
                invocation["usage_coverage"] = "non_additive_unmatched_native_coverage"

    def read_stdout(pipe):
        pending = b""
        dropping = False
        try:
            while True:
                chunk = os.read(pipe.fileno(), 65536)
                if not chunk:
                    break
                for part in chunk.splitlines(keepends=True):
                    if not dropping:
                        pending += part
                    if len(pending) > 4 * 1024 * 1024:
                        pending = b""
                        dropping = True
                        warning("exec_event_too_large")
                    if part.endswith(b"\n"):
                        if not dropping:
                            try:
                                observe(json.loads(pending))
                            except Exception:
                                warning("exec_event_unreadable")
                        pending, dropping = b"", False
            if pending:
                try:
                    observe(json.loads(pending))
                except Exception:
                    warning("exec_event_unreadable")
        except (OSError, ValueError):
            warning("stdout_reader_interrupted")

    def read_stderr(pipe):
        size = 0
        try:
            while True:
                chunk = os.read(pipe.fileno(), 65536)
                if not chunk:
                    return
                if size < 1024 * 1024:
                    errors.append(chunk[:1024 * 1024 - size])
                size += len(chunk)
                if size > 1024 * 1024:
                    warning("worker_stderr_truncated")
        except (OSError, ValueError):
            warning("stderr_reader_interrupted")

    process = None
    readers = []
    writer = None
    try:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, shell=False)
        readers = [threading.Thread(target=read_stdout, args=(process.stdout,), daemon=True),
                   threading.Thread(target=read_stderr, args=(process.stderr,), daemon=True)]
        for reader in readers:
            reader.start()

        def write_prompt():
            try:
                process.stdin.write(prompt.encode("utf-8"))
                process.stdin.close()
            except (BrokenPipeError, OSError, ValueError):
                pass

        writer = threading.Thread(target=write_prompt, daemon=True)
        writer.start()
        try:
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            result.update(status="timeout")
        else:
            result.update(status="success" if returncode == 0 else "failed", returncode=returncode)
    except FileNotFoundError:
        result.update(status="unavailable", stderr="Codex CLI indisponível")
    finally:
        active.clear()
        for reader in readers:
            reader.join(timeout=0.5)
            if reader.is_alive():
                warning("worker_pipe_cleanup_incomplete")
        if process:
            # Readers use os.read rather than buffered stream locks, so closing here is bounded.
            for pipe in (process.stdout, process.stderr):
                if pipe:
                    pipe.close()
        if writer:
            writer.join(timeout=0.2)
        result["stdout"] = "\n".join(messages)
        if errors:
            result["stderr"] = b"".join(errors).decode("utf-8", errors="replace")
        with metadata_lock:
            invocation.update(status=result["status"], returncode=result["returncode"], finished_at=stamp())
            result["invocation_id"] = invocation["invocation_id"]
            if collector:
                try:
                    if not stopped.is_set():
                        collector.record_invocation(invocation)
                        collector.sync()
                except TelemetryDisabled:
                    disabled()
                except Exception:
                    warning("telemetry_finalize_pending")
                finally:
                    try:
                        collector.close()
                    except Exception:
                        warning("telemetry_close_failed")
        if warnings:
            result["telemetry_warning"] = sorted(warnings)
    return result
