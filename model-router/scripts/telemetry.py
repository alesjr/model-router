"""Observe local Codex agents and conservative token subtotals."""

import argparse
import json
from pathlib import Path
import sqlite3
import sys
import time
from zoneinfo import ZoneInfoNotFoundError

from telemetry_core import Telemetry
from telemetry_settings import TelemetryDisabled, get_settings, require_enabled, set_telemetry


def render(payload):
    if payload.get("enabled") is False:
        return payload["reason"]
    if "agents" in payload:
        counts = payload["counts"]
        lines = [f"Revision {payload['revision']} | last sync {payload['last_sync']} | zone {payload['timezone']}",
                 f"Agents {counts['agents']} | workers {counts['workers']} | coordinators {counts['coordinators']} | reported models {counts['models']} | requested models {counts['models_requested']}"]
        workers_running = sum(a["role"] == "worker" and a["status"] == "observed_running" for a in payload["agents"])
        coordinators_running = sum(a["role"] == "coordinator" and a["status"] == "observed_running" for a in payload["agents"])
        lines.append(f"Workers observed running={workers_running} | coordinators observed running={coordinators_running}")
        for agent in payload["agents"]:
            lines.append(f"{agent['thread_id']} session={agent['session_id']} {agent['role']}/{agent['origin']} {agent['status']} model={agent['model_reported'] or 'unknown'} requested={agent['model_requested'] or 'unknown'} effort={agent['reasoning_effort_reported'] or 'unknown'} last={agent['last_seen'] or 'unknown'}")
        lines.extend(f"{model}: known token subtotal={values['total_tokens']}" for model, values in sorted(payload["usage_by_model"].items()))
        lines.append(f"Coordinator known token subtotal={payload['coordinator_totals']['total_tokens']}")
        lines.append(f"Coverage notes: {len(payload['coverage'])}; diagnostics: {len(payload['diagnostics'])}; publication lag: {payload['publication_lag']}")
        lines.append("Reported models are unverified. Observed running/stale does not establish process liveness.")
        return "\n".join(lines)
    if "totals" in payload:
        lines = [f"{payload['date']} ({payload['timezone']}) revision {payload['revision']} | total known subtotal={payload['totals']['total_tokens']} | complete={payload['coverage']['complete']}"]
        lines.append(f"Last sync={payload['last_sync'] or 'unknown'} | published revision={payload['published_revision']} | publication lag={payload['publication_lag']} | logs={payload['logs_dir']}")
        lines.extend(f"{model}: {values['total_tokens']}" for model, values in sorted(payload["by_model"].items()))
        lines.append(f"Baseline records: {len(payload['coverage']['baseline'])}; non-additive/unallocated: {len(payload['coverage']['non_additive'])}")
        return "\n".join(lines)
    return json.dumps(payload, ensure_ascii=False, indent=2)


def nonnegative(value):
    result = float(value)
    if not 0 <= result < float("inf"):
        raise argparse.ArgumentTypeError("Must be a finite nonnegative number")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    config = commands.add_parser("config", help="Explicitly enable or disable local telemetry")
    config.add_argument("--project", type=Path, required=True)
    config.add_argument("--scope", choices=("global", "project"), default="global")
    config.add_argument("--enabled", choices=("true", "false"), required=True)
    config.add_argument("--json", action="store_true")
    for action in ("sync", "status", "summary", "watch"):
        command = commands.add_parser(action)
        command.add_argument("--project", type=Path, required=True)
        command.add_argument("--source", action="append", type=Path)
        command.add_argument("--logs-dir", type=Path)
        command.add_argument("--timezone", default=None)
        command.add_argument("--session")
        command.add_argument("--json", action="store_true")
        command.add_argument("--stale-after", type=nonnegative, default=300)
        if action == "summary":
            command.add_argument("--date", required=True)
        if action == "watch":
            command.add_argument("--interval", type=nonnegative, default=2)
        if action == "sync":
            command.add_argument("--regenerate", action="store_true", help="Explicitly regenerate exports when changing timezone")
    args = parser.parse_args(argv)
    collector = None
    try:
        if args.action == "config":
            path = set_telemetry(args.enabled == "true", args.project, args.scope)
            payload = {**get_settings(args.project), "updated": str(path)}
            print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else payload["reason"])
            return 0
        require_enabled(args.project)
        # Read existing zone before applying an explicitly requested change.
        collector = Telemetry(args.project, sources=args.source, logs_dir=args.logs_dir)
        if args.timezone and args.timezone != collector.timezone:
            if args.action == "sync" and args.regenerate:
                collector.regenerate_timezone(args.timezone)
            elif collector._state("last_sync") is None:
                collector.regenerate_timezone(args.timezone)
            else:
                raise ValueError("Timezone change requires sync --regenerate --timezone ZONE")
        while True:
            require_enabled(args.project)
            if args.action in {"sync", "watch"}:
                payload = collector.sync()
            if args.action in {"status", "watch"}:
                payload = collector.status(args.session, args.stale_after)
            elif args.action == "summary":
                payload = collector.summary(args.date, args.session)
            if args.action == "watch" and sys.stdout.isatty():
                print("\033[2J\033[H", end="")
            print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else render(payload), flush=True)
            if args.action != "watch":
                return 0
            time.sleep(max(args.interval, 0.05))
    except KeyboardInterrupt:
        return 0
    except TelemetryDisabled:
        payload = get_settings(args.project)
        print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else render(payload), flush=True)
        return 0
    except (OSError, ValueError, sqlite3.Error, ZoneInfoNotFoundError) as exc:
        print(f"Telemetry error: {exc}", file=sys.stderr)
        return 1
    finally:
        if collector:
            collector.close()


if __name__ == "__main__":
    raise SystemExit(main())
