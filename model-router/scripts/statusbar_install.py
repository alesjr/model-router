"""Install/remove the isolated Codex status bar without modifying Codex itself."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import sys
import tempfile
import uuid

from telemetry_settings import _global_path, _read, get_settings, set_telemetry

START = "# >>> model-router statusbar >>>"
END = "# <<< model-router statusbar <<<"
ENTRY_MARKER = "# model-router-statusbar-entrypoint"
BUNDLE_MARKER = ".statusbar-managed.json"
MANAGED = "model-router-statusbar-v1"
OWNER_PREFIX = "# model-router-statusbar-owner: "


def _without_block(content):
    if content.count(START) != content.count(END) or content.count(START) > 1:
        raise ValueError("Malformed statusbar managed block; preserve and repair Bash configuration first")
    if START not in content:
        return content
    pattern = re.compile(r"^" + re.escape(START) + r"\n.*?^" + re.escape(END) + r"(?:\n|$)", re.M | re.S)
    stripped, count = pattern.subn("", content)
    if count != 1:
        raise ValueError("Malformed statusbar managed block")
    return stripped


def _bash_block(executable, *, metadata=True):
    owner = ""
    if metadata:
        owner = OWNER_PREFIX + json.dumps(
            {"owner": MANAGED, "entrypoint": str(executable)}, sort_keys=True) + "\n"
    return START + "\n" + owner + "codex() { " + shlex.quote(str(executable)) + ' "$@"; }\n' + END + "\n"


def _block_owner(content):
    """Trust only an exact generated block, including the legacy block format."""
    outside = _without_block(content)
    if outside == content:
        return None
    pattern = re.compile(r"^" + re.escape(START) + r"\n.*?^" + re.escape(END) + r"(?:\n|$)", re.M | re.S)
    block = pattern.search(content).group()
    lines = block.splitlines()
    try:
        if len(lines) == 4 and lines[1].startswith(OWNER_PREFIX):
            owner = json.loads(lines[1][len(OWNER_PREFIX):])
            if owner.get("owner") != MANAGED or not isinstance(owner.get("entrypoint"), str):
                return None
            executable = owner["entrypoint"]
            expected = _bash_block(executable)
        elif len(lines) == 3 and lines[1].startswith("codex() { ") and lines[1].endswith(' "$@"; }'):
            words = shlex.split(lines[1][len("codex() { "):-len(' "$@"; }')])
            if len(words) != 1:
                return None
            executable = words[0]
            expected = _bash_block(executable, metadata=False)
        else:
            return None
        if not Path(executable).is_absolute() or block.rstrip("\n") != expected.rstrip("\n"):
            return None
        return executable
    except (ValueError, TypeError, AttributeError):
        return None


def _bash_conflict(content):
    # Tokenize without evaluating Bash. Quotes/comments cannot hide compound-line
    # definitions, alias options, or additional name=value alias arguments.
    lexer = shlex.shlex(content, posix=True, punctuation_chars=";&|(){}\n")
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    try:
        words = list(lexer)
    except ValueError:
        return True  # Ambiguous shell syntax must be preserved.
    for index, word in enumerate(words):
        if word == "function" and index + 1 < len(words) and words[index + 1] == "codex":
            return True
        if word == "codex" and index + 1 < len(words) and (
                words[index + 1].startswith("()") or
                (words[index + 1] == "(" and index + 2 < len(words) and words[index + 2].startswith(")"))):
            return True
        if word == "alias":
            for argument in words[index + 1:]:
                if argument and argument[0] in ";&|(){}\n":
                    break
                # Expansion in an unrelated alias value cannot change its name.
                # Dynamic names remain ambiguous without executing user Bash.
                name, separator, _ = argument.partition("=")
                if (separator and name == "codex") or "$" in name or "`" in name:
                    return True
    return False


def _bash_content(bashrc, executable):
    original = bashrc.read_text(encoding="utf-8") if bashrc.exists() else ""
    outside = _without_block(original)
    if START in original and _block_owner(original) != str(executable):
        raise ValueError("Existing statusbar Bash block belongs to another prefix or has unknown contents; preserved")
    if _bash_conflict(outside):
        raise ValueError("Existing codex Bash alias/function conflicts with statusbar activation; preserved")
    block = _bash_block(executable)
    if START in original:
        pattern = re.compile(r"^" + re.escape(START) + r"\n.*?^" + re.escape(END) + r"(?:\n|$)", re.M | re.S)
        return original, pattern.sub(lambda _: block, original)
    return original, outside + ("\n" if outside and not outside.endswith("\n") else "") + block


def _backup(path):
    destination = path.with_name(path.name + ".backup." + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "." + uuid.uuid4().hex[:8])
    shutil.copy2(path, destination)
    return destination


def _write(path, content, executable=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    temporary = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            shutil.copymode(path, temporary)
        elif executable:
            temporary.chmod(0o755)
        else:
            temporary.chmod(0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _entry_owned(entry):
    if not entry.exists() and not entry.is_symlink():
        return False
    if entry.is_symlink() or not entry.is_file():
        raise ValueError(f"Unmanaged entrypoint preserved: {entry}")
    bundle = entry.parent.parent / "share" / "model-router" / "statusbar"
    if entry.read_text(encoding="utf-8") != _entry_content(bundle):
        raise ValueError(f"Unmanaged entrypoint preserved: {entry}")
    return True


def _entry_content(bundle):
    return ("#!/bin/sh\n" + ENTRY_MARKER + "\nexec python3 " +
            shlex.quote(str(bundle / "scripts" / "codex_status.py")) + ' "$@"\n')


def _bundle_owned(bundle):
    if not bundle.exists() and not bundle.is_symlink():
        return False
    if bundle.is_symlink():
        raise ValueError(f"Unmanaged bundle preserved: {bundle}")
    try:
        marker = json.loads((bundle / BUNDLE_MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError(f"Unmanaged bundle preserved: {bundle}") from None
    if not isinstance(marker, dict) or marker.get("owner") != MANAGED:
        raise ValueError(f"Unmanaged bundle preserved: {bundle}")
    return True


def _snapshot(path):
    if path.is_symlink():
        raise ValueError(f"Symlink preserved without replacement: {path}")
    if path.exists():
        return path.read_bytes(), path.stat().st_mode & 0o7777
    return None


def _restore(path, snapshot):
    """Restore raw bytes without relying on a possibly failed write operation."""
    if snapshot is None:
        path.unlink(missing_ok=True)
        return
    content, mode = snapshot
    descriptor, temporary = tempfile.mkstemp(prefix=".statusbar-restore-", dir=path.parent)
    temporary = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _preflight(enable_telemetry, activate_bash=False):
    if sys.version_info < (3, 11):
        raise RuntimeError("Statusbar requires Python 3.11 or newer")
    settings_path = _global_path()
    global_settings = _read(settings_path)
    # Installation is global: a project veto still controls the launcher, but
    # cannot guarantee that another project will not activate this installation.
    active_project = activate_bash and get_settings(Path.cwd())["enabled"]
    if (enable_telemetry or global_settings.get("telemetry", {}).get("enabled") is True
            or active_project):
        from codex_status import _codex, _tmux
        _codex()
        _tmux()
    return settings_path


def install_statusbar(prefix: Path, bashrc: Path, *, enable_telemetry: bool = False,
                      activate_bash: bool = False) -> dict:
    prefix, bashrc = Path(prefix).expanduser().absolute(), Path(bashrc).expanduser().absolute()
    bundle = prefix / "share" / "model-router" / "statusbar"
    entry = prefix / "bin" / "codex-status"
    old_entry = _entry_owned(entry)
    old_bundle = _bundle_owned(bundle)
    if bashrc.is_symlink() and activate_bash:
        raise ValueError("Bash configuration is a symlink; preserved without replacement")
    # Detect conflicts before mkdir, copying, settings writes, or Bash backups.
    original, activated = _bash_content(bashrc, entry) if activate_bash else (None, None)
    settings_path = _preflight(enable_telemetry, activate_bash)
    snapshots = {entry: _snapshot(entry)}
    if activate_bash:
        snapshots[bashrc] = _snapshot(bashrc)
    if enable_telemetry:
        snapshots[settings_path] = _snapshot(settings_path)
    missing_parents = set()
    for path in [bundle, *snapshots]:
        parent = path.parent
        while not parent.exists():
            missing_parents.add(parent)
            parent = parent.parent
    source = Path(__file__).resolve().parent.parent
    staging = None
    backup_bundle = None
    published_bundle = False
    touched = []
    backups = []
    try:
        bundle.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".statusbar-staging-", dir=bundle.parent))
        shutil.copytree(source / "scripts", staging / "scripts",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))
        if (source / "assets").is_dir():
            shutil.copytree(source / "assets", staging / "assets",
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))
        (staging / BUNDLE_MARKER).write_text(json.dumps({"owner": MANAGED}) + "\n", encoding="utf-8")
        if old_bundle:
            destination = bundle.with_name("statusbar.backup." + uuid.uuid4().hex)
            os.replace(bundle, destination)
            backup_bundle = destination
            backups.append(str(backup_bundle))
        os.replace(staging, bundle)
        published_bundle = True
        if old_entry:
            backups.append(str(_backup(entry)))
        touched.append(entry)
        _write(entry, _entry_content(bundle), executable=True)
        if activate_bash and original != activated:
            if bashrc.exists():
                backups.append(str(_backup(bashrc)))
            touched.append(bashrc)
            _write(bashrc, activated)
        if enable_telemetry:
            touched.append(settings_path)
            set_telemetry(True, scope="global")
        return {"entrypoint": str(entry), "bundle": str(bundle), "bashrc": str(bashrc),
                "already_installed": old_entry and old_bundle, "bash_activated": activate_bash,
                "telemetry_enabled": get_settings(Path.cwd())["enabled"], "backups": backups}
    except BaseException:
        for path in reversed(touched):
            _restore(path, snapshots[path])
        if published_bundle:
            shutil.rmtree(bundle)
        if backup_bundle:
            os.replace(backup_bundle, bundle)
        raise
    finally:
        if staging is not None and staging.exists():
            shutil.rmtree(staging)
        # Remove only empty directories created by this transaction; backups and
        # any unrelated files always survive failure.
        for parent in sorted(missing_parents, key=lambda item: len(item.parts), reverse=True):
            try:
                parent.rmdir()
            except OSError:
                pass


def uninstall_statusbar(prefix: Path, bashrc: Path) -> dict:
    prefix, bashrc = Path(prefix).expanduser().absolute(), Path(bashrc).expanduser().absolute()
    bundle = prefix / "share" / "model-router" / "statusbar"
    entry = prefix / "bin" / "codex-status"
    entry_owned = _entry_owned(entry)
    bundle_owned = _bundle_owned(bundle)
    if bashrc.is_symlink():
        raise ValueError("Bash configuration is a symlink; preserved without replacement")
    original = bashrc.read_text(encoding="utf-8") if bashrc.exists() else ""
    # A different prefix (or an unrecognized block) does not belong to us.
    remaining = _without_block(original) if _block_owner(original) == str(entry) else original
    backups = []
    if remaining != original:
        backups.append(str(_backup(bashrc)))
        _write(bashrc, remaining)
    if entry_owned:
        entry.unlink()
    if bundle_owned:
        shutil.rmtree(bundle)
    return {"removed_entrypoint": entry_owned, "removed_bundle": bundle_owned,
            "bash_preserved": START in original and remaining == original, "backups": backups}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", type=Path, default=Path.home() / ".local")
    parser.add_argument("--bashrc", type=Path, default=Path.home() / ".bashrc")
    parser.add_argument("--enable-telemetry", action="store_true")
    parser.add_argument("--activate-bash", action="store_true")
    parser.add_argument("--uninstall", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = (uninstall_statusbar(args.prefix, args.bashrc) if args.uninstall else
                  install_statusbar(args.prefix, args.bashrc, enable_telemetry=args.enable_telemetry,
                                    activate_bash=args.activate_bash))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Statusbar installation: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
