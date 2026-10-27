"""Render a local usage snapshot from published daily JSON generations only."""

from __future__ import annotations

import argparse
from datetime import date as calendar_date, datetime, timedelta, timezone
import json
import os
from pathlib import Path, PurePosixPath
import webbrowser
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


FIELDS = ("input_tokens", "cached_input_tokens", "cache_write_input_tokens",
          "output_tokens", "reasoning_output_tokens", "total_tokens")


def _date(value):
    if not isinstance(value, str):
        raise ValueError("Invalid date")
    result = calendar_date.fromisoformat(value)
    if result.isoformat() != value:
        raise ValueError("Invalid date")
    return result


def _timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc).isoformat() if parsed.tzinfo else None
    except (ValueError, OverflowError):
        return None


def _tokens(value):
    value = value if isinstance(value, dict) else {}
    return {key: value[key] if type(value.get(key)) is int and value[key] >= 0 else None
            for key in FIELDS}


def _models(value):
    return {name: _tokens(tokens) for name, tokens in value.items()
            if isinstance(name, str) and isinstance(tokens, dict)} if isinstance(value, dict) else {}


def _read_file(root, relative):
    """Use descriptor-relative opens so symlink replacement cannot escape logs."""
    parts = PurePosixPath(relative)
    if parts.is_absolute() or any(part in (".", "..") for part in parts.parts):
        raise ValueError("Unsafe publication path")
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts.parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        file_descriptor = os.open(parts.parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                  dir_fd=descriptor)
        with os.fdopen(file_descriptor, "rb") as stream:
            import stat
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError("Publication is not a regular file")
            return stream.read()
    finally:
        os.close(descriptor)


def read_usage(logs_dir: Path) -> dict:
    """Read each current manifest once; ignore audit logs and older revisions."""
    root = Path(logs_dir).expanduser()
    days, diagnostics = [], []
    if root.is_symlink():
        return {"days": [], "diagnostics": [{"code": "unsafe_logs_root"}]}
    if not root.exists():
        return {"days": [], "diagnostics": [{"code": "logs_unavailable"}]}
    try:
        directories = sorted(root.iterdir())
    except OSError:
        return {"days": [], "diagnostics": [{"code": "logs_unreadable"}]}
    for directory in directories:
        try:
            day = _date(directory.name).isoformat()
        except ValueError:
            continue
        try:
            before = _read_file(root, f"{day}/current.json")
            manifest = json.loads(before)
            if not isinstance(manifest, dict) or type(manifest.get("schema_version")) is not int or manifest["schema_version"] != 1:
                raise ValueError("Unsupported manifest schema")
            if manifest.get("date", day) != day:
                raise ValueError("Manifest date mismatch")
            revision, zone = manifest.get("revision"), manifest.get("timezone")
            if type(revision) is not int or revision < 0 or not isinstance(zone, str):
                raise ValueError("Invalid manifest identity")
            ZoneInfo(zone)
            summary_name = manifest.get("summary")
            if not isinstance(summary_name, str) or "\\" in summary_name:
                raise ValueError("Invalid summary path")
            parts = PurePosixPath(summary_name).parts
            if (len(parts) != 3 or parts[0] != "revisions" or parts[2] != "summary.json"
                    or not (parts[1] == str(revision) or parts[1].startswith(f"{revision}-"))):
                raise ValueError("Summary outside generation")
            summary = json.loads(_read_file(root, f"{day}/{summary_name}"))
            if not isinstance(summary, dict) or type(summary.get("schema_version")) is not int or summary["schema_version"] != 1:
                raise ValueError("Unsupported summary schema")
            if (summary.get("date") != day or type(summary.get("revision")) is not int
                    or summary["revision"] != revision or summary.get("timezone") != zone):
                raise ValueError("Summary identity mismatch")
            if _read_file(root, f"{day}/current.json") != before:
                diagnostics.append({"date": day, "code": "publication_changed"})
                continue
            coverage = summary.get("coverage")
            coverage = coverage if isinstance(coverage, dict) else {}
            # Export coverage categories/counts only, never responses or source messages.
            issues = [key for key, value in coverage.items()
                      if key in ("baseline", "non_additive", "undated_responses", "source_gaps", "diagnostics", "missing") and value]
            sessions = summary.get("by_session")
            sessions = sessions if isinstance(sessions, dict) else {}
            days.append({"date": day, "timezone": zone, "revision": revision,
                         "last_sync": _timestamp(summary.get("last_sync")),
                         "totals": _tokens(summary.get("totals")),
                         "by_model": _models(summary.get("by_model")),
                         "by_session": {name: {"totals": _tokens(row.get("totals")),
                                                "by_model": _models(row.get("by_model"))}
                                        for name, row in sessions.items()
                                        if isinstance(name, str) and isinstance(row, dict)},
                         "coverage": {"complete": coverage.get("complete") is True and not issues
                                                   and summary.get("publication_lag") is not True,
                                      "issues": issues,
                                      "publication_lag": summary.get("publication_lag") is True}})
        except (OSError, ValueError, TypeError, ZoneInfoNotFoundError):
            diagnostics.append({"date": day, "code": "invalid_publication"})
    if len({day["timezone"] for day in days}) > 1:
        diagnostics.append({"code": "mixed_timezones"})
        days = []  # Different calendar boundaries cannot be added safely.
    return {"days": days, "diagnostics": diagnostics}


def _aggregate(rows):
    result, known_subtotals, partial_fields = {}, {}, []
    for key in FIELDS:
        values = [row.get(key) for row in rows]
        known = [value for value in values if type(value) is int and value >= 0]
        subtotal = sum(known) if known else None
        known_subtotals[key] = subtotal
        result[key] = subtotal if key == "total_tokens" or len(known) == len(values) else None
        if not values or len(known) != len(values):
            partial_fields.append(key)
    return result, known_subtotals, partial_fields


def usage_view(data: dict, period: str = "weekly", date: str | None = None) -> dict:
    """Aggregate known values, retaining null dimensions and partial subtotals."""
    if period not in ("daily", "weekly", "cumulative"):
        raise ValueError("Period must be daily, weekly or cumulative")
    days = data.get("days", [])
    end = _date(date) if date is not None else _date(max((d["date"] for d in days), default=calendar_date.today().isoformat()))
    start = end if period == "daily" else end - timedelta(days=6) if period == "weekly" else None
    selected = [day for day in days if day["date"] <= end.isoformat()
                and (start is None or day["date"] >= start.isoformat())]
    diagnostics = [row for row in data.get("diagnostics", [])
                   if not row.get("date") or (row["date"] <= end.isoformat()
                       and (start is None or row["date"] >= start.isoformat()))]
    totals, subtotals, partial = _aggregate([day["totals"] for day in selected])
    models = sorted({name for day in selected for name in day["by_model"]})
    sessions = sorted({name for day in selected for name in day["by_session"]})
    by_model = {name: _aggregate([day["by_model"][name] for day in selected if name in day["by_model"]])[0]
                for name in models}
    by_session = {}
    for name in sessions:
        rows = [day["by_session"][name] for day in selected if name in day["by_session"]]
        session_models = sorted({model for row in rows for model in row["by_model"]})
        by_session[name] = {"totals": _aggregate([row["totals"] for row in rows])[0],
                            "by_model": {model: _aggregate([row["by_model"][model] for row in rows if model in row["by_model"]])[0]
                                         for model in session_models}}
    missing = []
    if start is not None:
        observed = {day["date"] for day in selected}
        missing = [(start + timedelta(days=n)).isoformat() for n in range((end - start).days + 1)
                   if (start + timedelta(days=n)).isoformat() not in observed]
    complete = bool(selected) and not diagnostics and not missing and not partial and all(day["coverage"]["complete"] for day in selected)
    return {"period": period, "date": end.isoformat(), "totals": totals,
            "by_model": by_model, "by_session": by_session,
            "last_sync": max((day["last_sync"] for day in selected if day.get("last_sync")), default=None),
            "timezone": selected[0]["timezone"] if selected else None,
            "coverage": {"complete": complete, "no_data": not selected,
                         "days": len(selected), "missing_dates": missing,
                         "partial_fields": partial, "known_subtotals": subtotals,
                         "diagnostics": diagnostics,
                         "issues": sorted({issue for day in selected for issue in day["coverage"]["issues"]}),
                         "publication_lag": any(day["coverage"]["publication_lag"] for day in selected)}}


def build_dashboard(project: Path, logs_dir: Path | None = None,
                    output: Path | None = None, open_browser: bool = False) -> Path:
    """Write a self-contained, offline snapshot without starting collection."""
    from telemetry_settings import get_settings

    project = Path(project).expanduser().resolve()
    logs = Path(logs_dir).expanduser() if logs_dir is not None else project / ".model-router" / "logs"
    destination = Path(output).expanduser() if output is not None else project / ".model-router" / "usage.html"
    settings = get_settings(project)
    data = read_usage(logs)
    payload = {"data": data, "initial": usage_view(data), "fields": FIELDS,
               "generated_at": datetime.now(timezone.utc).isoformat(),
               "settings": {"enabled": settings["enabled"], "reason": settings["reason"]}}
    def browser_data(value):
        # Decimal strings preserve counters beyond JavaScript's safe integer range.
        if isinstance(value, dict):
            return {key: str(item) if key in FIELDS and type(item) is int else browser_data(item)
                    for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [browser_data(item) for item in value]
        return value

    encoded = json.dumps(browser_data(payload), ensure_ascii=True, allow_nan=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    template = Path(__file__).resolve().parents[1] / "assets" / "usage.html"
    html = template.read_text(encoding="utf-8").replace("/*__USAGE_DATA__*/null", encoded)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(html, encoding="utf-8")
    if open_browser:
        webbrowser.open(destination.resolve().as_uri())
    return destination


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Generate an offline usage snapshot from published JSON")
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--logs-dir", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--open", action="store_true", dest="open_browser")
    args = parser.parse_args(argv)
    print(build_dashboard(args.project, args.logs_dir, args.output, args.open_browser))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
