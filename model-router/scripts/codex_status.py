"""Run Codex with a private, project-scoped tmux observation bar."""

from __future__ import annotations

import argparse
from collections import Counter
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

from telemetry_settings import get_settings, set_telemetry


def _safe(value, limit=64):
    # No tmux format introducer survives, including literal #[...] and #(...).
    return "".join(c for c in str(value) if c.isprintable() and c != "#")[:limit]


def format_status(payload: dict) -> str:
    agents = [a for a in payload.get("agents", []) if isinstance(a, dict) and a.get("role") == "worker"]
    running = [a for a in agents if a.get("status") == "observed_running"]
    reported = Counter(_safe(a["model_reported"], 36) for a in running if a.get("model_reported"))
    requested = Counter(_safe(a["model_requested"], 36) for a in running
                        if not a.get("model_reported") and a.get("model_requested"))
    unknown = sum(not a.get("model_reported") and not a.get("model_requested") for a in running)
    uncertain = sum(a.get("status") in {"stale", "unknown"} for a in agents)
    parts = [f"Agentes obs.: {len(running)}"]
    if unknown:
        parts.append(f"modelo unknown: {unknown}")
    for label, values in (("reportado", reported), ("solicitado", requested)):
        if values:
            parts.append(label + ": " + ", ".join(f"{model} ×{count}" for model, count in sorted(values.items())))
    if uncertain:
        parts.append(f"stale/unknown: {uncertain}")
    return _safe(" | ".join(parts), 239)


_VALUE_OPTIONS = {"-C", "--cd", "-c", "--config", "-m", "--model", "-p", "--profile",
                  "-s", "--sandbox", "-a", "--ask-for-approval", "--enable", "--disable",
                  "-i", "--image", "--add-dir", "--local-provider"}
_COMMANDS = {"agents", "exec", "e", "review", "login", "logout", "mcp", "mcp-server",
             "plugin", "app-server", "remote-control", "completion", "update", "doctor",
             "sandbox", "debug", "apply", "a", "resume", "queue", "archive", "delete",
             "migrate-rollouts", "migrate-nfs-db", "unarchive", "fork", "cloud",
             "exec-server", "features", "help"}


def should_wrap(argv: list[str], *, is_tty: bool, nested: bool, enabled: bool) -> bool:
    if not is_tty or nested or not enabled:
        return False
    index = 0
    positional_seen = False
    while index < len(argv):
        arg = argv[index]
        # Everything after the separator is prompt data, including command names.
        if arg == "--":
            return True
        if arg in _VALUE_OPTIONS:
            index += 2
            continue
        if arg in {"-h", "--help", "-V", "--version", "--remote"} or arg.startswith("--remote="):
            return False
        if not arg.startswith("-") and not positional_seen:
            if arg in _COMMANDS and arg not in {"resume", "fork"}:
                return False
            # Native flags remain active after a prompt or resume/fork ID until
            # the literal separator. Only the first positional names a command.
            positional_seen = True
        index += 1
    return True


def resolve_project(argv: list[str], cwd: Path) -> Path:
    project = Path(cwd)
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg == "--":
            break
        value = None
        if arg in {"-C", "--cd"}:
            if index + 1 < len(argv):
                index += 1
                value = argv[index]
        elif arg.startswith("--cd="):
            value = arg[5:]
        elif arg.startswith("-C") and len(arg) > 2:
            value = arg[2:]
        elif arg in _VALUE_OPTIONS:
            index += 1
        if value is not None:
            candidate = Path(value).expanduser()
            project = candidate if candidate.is_absolute() else Path(cwd) / candidate
        index += 1
    return project.resolve()


def _codex():
    own = {Path(__file__).resolve(), Path(sys.argv[0]).resolve()}
    explicit = os.environ.get("CODEX_STATUS_CODEX")
    candidates = [Path(explicit).expanduser()] if explicit else [Path(p or ".") / "codex" for p in os.get_exec_path()]
    for candidate in candidates:
        if candidate.resolve() not in own and candidate.is_file() and os.access(candidate, os.X_OK):
            # Reject installed launcher entrypoints, even under the name codex.
            try:
                with candidate.open("rb") as stream:
                    if b"# model-router-statusbar-entrypoint" in stream.read(512):
                        continue
            except (UnicodeError, OSError):
                pass
            return str(candidate.absolute())
    raise RuntimeError("Codex executable unavailable; set CODEX_STATUS_CODEX to the original CLI path")


def _tmux():
    found = shutil.which("tmux")
    personal = Path.home() / ".local" / "bin" / "tmux"
    if found:
        return found
    if personal.is_file() and os.access(personal, os.X_OK):
        return str(personal)
    raise RuntimeError("Status bar requires tmux (PATH or ~/.local/bin/tmux); disable telemetry for direct Codex")


def _pane(argv):
    """Keep pane alive until parent reads CLI status; Ctrl-C belongs to Codex."""
    state, owner, executable, *arguments = argv
    state = Path(state)
    owner = int(owner)
    stopped = threading.Event()
    received_signal = None
    request = state.with_name("shutdown-signal")

    def stop(signum, _frame=None):
        nonlocal received_signal
        if received_signal is None:
            received_signal = signum
        stopped.set()

    def stopping(timeout):
        if stopped.wait(timeout):
            return True
        try:
            signum = int(request.read_text(encoding="ascii"))
        except (FileNotFoundError, ValueError):
            return False
        if signum in (signal.SIGHUP, signal.SIGTERM):
            stop(signum)
        return received_signal is not None

    signal.signal(signal.SIGINT, lambda *_: None)
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, stop)

    def child_signals():
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(sig, signal.SIG_DFL)

    env = dict(os.environ, CODEX_STATUS_ACTIVE="1")
    process = subprocess.Popen([executable, *arguments], env=env, preexec_fn=child_signals)
    try:
        while process.poll() is None and not stopping(0.1):
            try:
                os.kill(owner, 0)
            except ProcessLookupError:
                stopped.set()
        if process.poll() is not None:
            state.write_text(str(process.returncode), encoding="ascii")
            while not stopping(0.2):
                try:
                    os.kill(owner, 0)
                except ProcessLookupError:
                    break
    finally:
        if process.poll() is None:
            process.send_signal(received_signal or signal.SIGTERM)
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        if request.exists():
            state.with_name("shutdown-complete").touch()
    return 0


def _interactive(executable, argv, project):
    tmux = _tmux()
    stop = threading.Event()
    received_signal = None

    def request_stop(signum, _frame):
        nonlocal received_signal
        if received_signal is None:
            received_signal = signum
        stop.set()

    previous = {}
    for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        previous[sig] = signal.signal(sig, (lambda *_: None) if sig == signal.SIGINT else request_stop)
    with tempfile.TemporaryDirectory(prefix="codex-status-") as temporary:
        state = Path(temporary) / "exit"
        base = [tmux, "-S", str(Path(temporary) / "socket"), "-f", "/dev/null"]
        client = None
        collector_thread = None

        def command(*args, check=True):
            return subprocess.run([*base, *args], check=check, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True, timeout=5)

        def collect():
            from telemetry_core import Telemetry
            collector = None
            try:
                while not stop.is_set():
                    if not get_settings(project)["enabled"]:
                        line = "Telemetria desativada"
                        if collector:
                            collector.close()
                            collector = None
                    else:
                        try:
                            if collector is None:
                                collector = Telemetry(project)
                            collector.sync()
                            line = format_status(collector.status())
                        except Exception:
                            line = "Observação indisponível | stale/unknown"
                    scope = _safe(project.name, 60)
                    try:
                        command("set-option", "-t", "codex", "status-left",
                                _safe(line + " | projeto " + scope, 239))
                    except (OSError, subprocess.SubprocessError):
                        break
                    stop.wait(2)
            finally:
                if collector:
                    collector.close()

        try:
            pane = [sys.executable, str(Path(__file__).resolve()), "__statusbar_pane__",
                    str(state), str(os.getpid()), executable, *argv]
            # Preserve the original working directory: Codex itself applies -C,
            # including relative paths, from that directory exactly once.
            command("new-session", "-d", "-s", "codex", "-c", str(Path.cwd()), shlex.join(pane))
            command("set-option", "-t", "codex", "status", "on")
            command("set-option", "-t", "codex", "status-left-length", "240")
            command("set-option", "-t", "codex", "status-right", "")
            command("set-option", "-t", "codex", "status-left", "Observação iniciando | projeto " + _safe(project, 60))
            command("set-window-option", "-t", "codex:0", "window-status-current-format", "")
            collector_thread = threading.Thread(target=collect, name="codex-status-collector", daemon=True)
            collector_thread.start()
            client = subprocess.Popen([*base, "attach-session", "-t", "codex"])
            while not stop.wait(0.1):
                if state.exists():
                    try:
                        result = int(state.read_text(encoding="ascii"))
                    except ValueError:
                        continue
                    return result if result >= 0 else 128 - result
                if client.poll() is not None:
                    return client.returncode
            return 128 + (received_signal or signal.SIGTERM)
        finally:
            stop.set()
            if received_signal is not None:
                # Deliver the original signal while the pane's terminal is still
                # alive. Killing tmux first would replace TERM with terminal HUP.
                state.with_name("shutdown-signal").write_text(str(received_signal), encoding="ascii")
                deadline = time.monotonic() + 3
                pause = threading.Event()
                while not state.with_name("shutdown-complete").exists() and time.monotonic() < deadline:
                    pause.wait(0.05)
            try:
                command("kill-server", check=False)
            except (OSError, subprocess.SubprocessError):
                pass
            if client and client.poll() is None:
                try:
                    client.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    client.terminate()
                    client.wait(timeout=2)
            if collector_thread:
                collector_thread.join(timeout=0.5)
            for sig, handler in previous.items():
                signal.signal(sig, handler)


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "__statusbar_pane__":
        return _pane(argv[1:])
    if argv and argv[0] == "usage":
        from usage_dashboard import build_dashboard
        parser = argparse.ArgumentParser(prog="codex-status usage")
        parser.add_argument("--project", type=Path, default=Path.cwd())
        parser.add_argument("--logs-dir", type=Path)
        parser.add_argument("--output", type=Path)
        parser.add_argument("--no-open", action="store_true")
        args = parser.parse_args(argv[1:])
        try:
            print(build_dashboard(args.project, args.logs_dir, args.output, open_browser=not args.no_open))
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"codex-status usage: {exc}", file=sys.stderr)
            return 1
        return 0
    if argv and argv[0] == "telemetry":
        parser = argparse.ArgumentParser(prog="codex-status telemetry")
        parser.add_argument("state", choices=("on", "off"))
        parser.add_argument("--project", type=Path, default=Path.cwd())
        parser.add_argument("--scope", choices=("global", "project"), default="global")
        args = parser.parse_args(argv[1:])
        try:
            print(set_telemetry(args.state == "on", args.project, args.scope))
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"codex-status telemetry: {exc}", file=sys.stderr)
            return 1
        return 0
    try:
        executable = _codex()
        project = resolve_project(argv, Path.cwd())
        enabled = get_settings(project)["enabled"]
        if not should_wrap(argv, is_tty=sys.stdin.isatty() and sys.stdout.isatty(),
                           nested=bool(os.environ.get("CODEX_STATUS_ACTIVE") or os.environ.get("TMUX")), enabled=enabled):
            os.execv(executable, [executable, *argv])
        return _interactive(executable, argv, project)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        print(f"codex-status: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
