"""Explicit opt-in and shared, fail-closed telemetry configuration."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile


class TelemetryDisabled(RuntimeError):
    """Collection is disabled by the current configuration."""


def _global_path() -> Path:
    root = os.environ.get("XDG_CONFIG_HOME")
    return (Path(root).expanduser() if root else Path.home() / ".config") / "model-router" / "settings.json"


def _read(path: Path) -> dict:
    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    data = json.loads(content)
    if not isinstance(data, dict):
        raise ValueError("Settings must be a JSON object")
    if "telemetry" in data:
        telemetry = data["telemetry"]
        if not isinstance(telemetry, dict):
            raise ValueError("telemetry must be a JSON object")
        if "enabled" in telemetry and type(telemetry["enabled"]) is not bool:
            raise ValueError("telemetry.enabled must be true or false")
    return data


def get_settings(project: Path) -> dict:
    """Read both scopes without creating storage; every explicit veto wins."""
    paths = {"global": _global_path(),
             "project": Path(project).expanduser().resolve() / ".model-router.json"}
    result = {"enabled": False, "reason": "Telemetry disabled: explicit opt-in required",
              "paths": {scope: str(path) for scope, path in paths.items()}, "scopes": {}}
    for scope, path in paths.items():
        try:
            data = _read(path)
        except (OSError, ValueError, UnicodeError) as exc:
            result["reason"] = f"Telemetry disabled: invalid {scope} settings ({type(exc).__name__})"
            return result
        result["scopes"][scope] = data.get("telemetry", {}).get("enabled")
    vetoes = [scope for scope, enabled in result["scopes"].items() if enabled is False]
    if vetoes:
        result["reason"] = "Telemetry disabled by " + ", ".join(vetoes) + " settings"
    elif any(enabled is True for enabled in result["scopes"].values()):
        result.update(enabled=True, reason="Telemetry explicitly enabled")
    return result


def require_enabled(project: Path) -> None:
    settings = get_settings(project)
    if not settings["enabled"]:
        raise TelemetryDisabled(settings["reason"])


def set_telemetry(enabled: bool, project: Path | None = None, scope: str = "global") -> Path:
    """Atomically update one scope, preserving unrelated valid configuration."""
    if type(enabled) is not bool:
        raise ValueError("enabled must be a boolean")
    if scope not in {"global", "project"}:
        raise ValueError("scope must be global or project")
    if scope == "project" and project is None:
        raise ValueError("Project scope requires a project directory")
    path = _global_path() if scope == "global" else Path(project).expanduser().resolve() / ".model-router.json"
    data = _read(path)
    data.setdefault("telemetry", {})["enabled"] = enabled
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=path.name + ".", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return path
