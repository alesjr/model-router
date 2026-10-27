"""Contract tests for safe status formatting, passthrough and installation."""

import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import sys
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "model-router" / "scripts"
sys.path.insert(0, str(SCRIPTS))


def load_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


statusbar = load_module("codex_status_contract", "codex_status.py")
installer = load_module("statusbar_install_contract", "statusbar_install.py")
from telemetry_settings import get_settings


class StatusFormattingTests(unittest.TestCase):
    def test_counts_observed_workers_only_and_separates_requested_reported_and_stale(self):
        line = statusbar.format_status({
            "agents": [
                {"role": "coordinator", "status": "observed_running", "model_reported": "nope"},
                {"role": "worker", "status": "observed_running", "model_reported": "gpt-6-luna"},
                {"role": "worker", "status": "observed_running", "model_requested": "gpt-6.1-sol"},
                {"role": "worker", "status": "stale", "model_reported": "gpt-6-astra"},
                {"role": "worker", "status": "unknown"},
            ]
        })
        self.assertIn("2", line)
        self.assertIn("gpt-6-luna", line)
        self.assertIn("gpt-6.1-sol", line)
        self.assertNotIn("nope", line)
        self.assertTrue(any(word in line.lower() for word in ("stale", "incerto", "unknown")))

    def test_control_and_tmux_format_injection_are_sanitized_and_bounded(self):
        payload = {"agents": [{"role": "worker", "status": "observed_running",
                               "model_reported": "#(touch /tmp/pwn)\n\x1b[31m" + "x" * 500}]}
        rendered = statusbar.format_status(payload)
        self.assertNotIn("#(", rendered)
        self.assertNotIn("\x1b", rendered)
        self.assertNotIn("\n", rendered)
        self.assertLess(len(rendered), 240)


class LauncherDecisionTests(unittest.TestCase):
    def test_wrapper_only_for_enabled_interactive_top_level_tui_commands(self):
        self.assertTrue(statusbar.should_wrap([], is_tty=True, nested=False, enabled=True))
        self.assertTrue(statusbar.should_wrap(["resume"], is_tty=True, nested=False, enabled=True))
        for argv, tty, nested, enabled in (
            (["--help"], True, False, True), (["--version"], True, False, True),
            (["exec", "task"], True, False, True), ([], False, False, True),
            ([], True, True, True), ([], True, False, False),
        ):
            with self.subTest(argv=argv, tty=tty, nested=nested, enabled=enabled):
                self.assertFalse(statusbar.should_wrap(argv, is_tty=tty, nested=nested, enabled=enabled))

    def test_cli_subcommands_and_literal_separator_preserve_direct_passthrough(self):
        commands = (
            "agents", "update", "doctor", "plugin", "queue", "archive", "delete",
            "migrate-rollouts", "migrate-nfs-db", "unarchive", "exec-server", "remote-control",
        )
        for command in commands:
            with self.subTest(command=command):
                self.assertFalse(statusbar.should_wrap(
                    [command], is_tty=True, nested=False, enabled=True))

        for argv in (["--", "exec"], ["--", "--help"]):
            with self.subTest(argv=argv):
                self.assertTrue(statusbar.should_wrap(
                    argv, is_tty=True, nested=False, enabled=True))

    def test_option_value_that_matches_subcommand_is_not_a_subcommand(self):
        self.assertTrue(statusbar.should_wrap(
            ["--model", "agents"], is_tty=True, nested=False, enabled=True))

    def test_flags_after_prompt_or_resume_id_keep_native_passthrough(self):
        cases = (
            ["prompt", "--help"],
            ["prompt", "--remote", "https://example.invalid"],
            ["resume", "session-123", "--help"],
        )
        for argv in cases:
            with self.subTest(argv=argv):
                self.assertFalse(statusbar.should_wrap(
                    argv, is_tty=True, nested=False, enabled=True))

        self.assertTrue(statusbar.should_wrap(
            ["--model", "--help"], is_tty=True, nested=False, enabled=True))

    def test_resolve_project_honors_cd_forms_without_mutating_original_argv(self):
        cwd = Path("/work/start")
        cases = [
            (["-C", "/work/one", "resume"], Path("/work/one")),
            (["--cd", "/work/two", "resume"], Path("/work/two")),
            (["--cd=/work/three", "resume"], Path("/work/three")),
            (["-C/work/four", "resume"], Path("/work/four")),
        ]
        for argv, expected in cases:
            with self.subTest(argv=argv):
                original = list(argv)
                self.assertEqual(statusbar.resolve_project(argv, cwd), expected)
                self.assertEqual(argv, original)

    def test_main_passthrough_preserves_argv_and_worker_exit_without_tmux(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fake = root / "codex fake"
            argv_path = root / "argv.json"
            fake.write_text(
                "#!/usr/bin/env python3\nimport json, os, sys\n"
                "open(os.environ['FAKE_ARGS'], 'w').write(json.dumps(sys.argv[1:]))\n"
                "sys.exit(23)\n", encoding="utf-8")
            fake.chmod(0o755)
            xdg = root / "xdg"
            home = root / "home"
            script = SCRIPTS / "codex_status.py"
            with patch.dict(os.environ, {
                "PATH": str(root) + os.pathsep + os.environ.get("PATH", ""),
                "CODEX_STATUS_CODEX": str(fake), "FAKE_ARGS": str(argv_path),
                "XDG_CONFIG_HOME": str(xdg), "HOME": str(home),
            }):
                result = subprocess.run(
                    [sys.executable, str(script), "exec", "--", "arg with spaces", "$(literal)"],
                    cwd=root, env=os.environ.copy(), capture_output=True, text=True, check=False,
                )
            self.assertEqual(result.returncode, 23)
            self.assertEqual(json.loads(argv_path.read_text(encoding="utf-8")),
                             ["exec", "--", "arg with spaces", "$(literal)"])

    def test_interactive_signal_exit_code_tracks_original_signal_and_cleans_up(self):
        for received, expected in ((statusbar.signal.SIGHUP, 129),
                                   (statusbar.signal.SIGTERM, 143)):
            with self.subTest(signal=received):
                handlers = {}
                prior_handlers = {sig: object() for sig in
                                  (statusbar.signal.SIGTERM, statusbar.signal.SIGHUP,
                                   statusbar.signal.SIGINT)}
                registrations = []

                class TriggerEvent:
                    def __init__(self):
                        self.triggered = False

                    def wait(self, timeout=None):
                        if not self.triggered:
                            self.triggered = True
                            handlers[received](received, None)
                        return True

                    def set(self):
                        pass

                class IdleThread:
                    def __init__(self, *args, **kwargs):
                        pass

                    def start(self):
                        pass

                    def join(self, timeout=None):
                        pass

                class Client:
                    returncode = None

                    def poll(self):
                        return None

                    def wait(self, timeout=None):
                        self.returncode = 0
                        return 0

                def signal_registration(sig, handler):
                    registrations.append((sig, handler))
                    handlers[sig] = handler
                    return prior_handlers[sig]

                completed = type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})()
                with patch.object(statusbar, "_tmux", return_value="fake-tmux"), \
                        patch.object(statusbar.threading, "Event", side_effect=TriggerEvent), \
                        patch.object(statusbar.threading, "Thread", IdleThread), \
                        patch.object(statusbar.signal, "signal", side_effect=signal_registration), \
                        patch.object(statusbar.subprocess, "run", return_value=completed) as run, \
                        patch.object(statusbar.subprocess, "Popen", return_value=Client()):
                    code = statusbar._interactive("fake-codex", [], Path.cwd())

                self.assertEqual(code, expected)
                self.assertTrue(any("kill-server" in call.args[0] for call in run.call_args_list))
                for sig, prior in prior_handlers.items():
                    self.assertIn((sig, prior), registrations)

    def test_statusbar_pane_forwards_the_signal_it_received_to_codex(self):
        for received in (statusbar.signal.SIGHUP, statusbar.signal.SIGTERM):
            with self.subTest(signal=received):
                handlers = {}

                class TriggerEvent:
                    def wait(self, timeout=None):
                        handlers[received](received, None)
                        return True

                    def set(self):
                        pass

                class Child:
                    returncode = None
                    forwarded = None

                    def poll(self):
                        return None

                    def send_signal(self, sig):
                        self.forwarded = sig

                    def wait(self, timeout=None):
                        self.returncode = -received
                        return self.returncode

                    def terminate(self):
                        self.forwarded = statusbar.signal.SIGTERM

                    def kill(self):
                        pass

                child = Child()

                def capture_handler(sig, handler):
                    handlers[sig] = handler
                    return statusbar.signal.SIG_DFL

                with patch.object(statusbar.threading, "Event", side_effect=TriggerEvent), \
                        patch.object(statusbar.signal, "signal", side_effect=capture_handler), \
                        patch.object(statusbar.subprocess, "Popen", return_value=child):
                    result = statusbar._pane([
                        "unused-pane-state", "123", "fake-codex", "arg"])

                self.assertEqual(result, 0)
                self.assertEqual(child.forwarded, received)


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.prefix = self.root / "prefix with spaces"
        self.bashrc = self.root / ".bashrc"
        self.bashrc.write_text("# user content\n", encoding="utf-8")
        self.xdg = self.root / "xdg"
        self.home = self.root / "home"
        self.environment = patch.dict(os.environ, {
            "XDG_CONFIG_HOME": str(self.xdg), "HOME": str(self.home),
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_install_defaults_off_and_is_idempotent_with_backup(self):
        first = installer.install_statusbar(self.prefix, self.bashrc)
        self.assertFalse(first.get("telemetry_enabled", False))
        self.assertTrue((self.prefix / "bin" / "codex-status").exists())
        self.assertFalse(get_settings(self.root)["enabled"])
        self.assertFalse((self.xdg / "model-router" / "settings.json").exists())
        second = installer.install_statusbar(self.prefix, self.bashrc)
        self.assertTrue(second.get("already_installed", True))
        self.assertEqual(len(list(self.root.glob(".bashrc.backup*"))), 0)

    def test_bash_activation_backup_uninstall_preserves_unrelated_text(self):
        installer.install_statusbar(self.prefix, self.bashrc, activate_bash=True)
        backups = list(self.root.glob(".bashrc.backup*"))
        self.assertTrue(backups)
        self.assertIn("# user content", backups[0].read_text(encoding="utf-8"))
        content = self.bashrc.read_text(encoding="utf-8")
        self.assertIn("# >>> model-router statusbar >>>", content)
        self.assertIn("# <<< model-router statusbar <<<", content)
        self.assertIn("# user content", content)
        self.bashrc.write_text(content + "# later user line\n", encoding="utf-8")
        installer.uninstall_statusbar(self.prefix, self.bashrc)
        remaining = self.bashrc.read_text(encoding="utf-8")
        self.assertNotIn("# >>> model-router statusbar >>>", remaining)
        self.assertIn("# user content", remaining)
        self.assertIn("# later user line", remaining)

    def test_existing_function_conflict_is_reported_without_overwriting_bashrc(self):
        original = "codex() { command codex \"$@\"; }\n"
        self.bashrc.write_text(original, encoding="utf-8")
        with self.assertRaises((ValueError, RuntimeError)):
            installer.install_statusbar(self.prefix, self.bashrc, activate_bash=True)
        self.assertEqual(self.bashrc.read_text(encoding="utf-8"), original)

    def test_bash_alias_and_function_conflicts_inside_compound_lines_are_preserved(self):
        conflicts = (
            "if true; then alias codex='command codex'; fi\n",
            "if true; then codex() { :; }; fi\n",
            "alias -- codex='command codex'\n",
            "alias \"$CODENAME\"='command codex'\n",
        )
        for original in conflicts:
            with self.subTest(original=original):
                self.bashrc.write_text(original, encoding="utf-8")
                with self.assertRaises((ValueError, RuntimeError)):
                    installer.install_statusbar(self.prefix, self.bashrc, activate_bash=True)
                self.assertEqual(self.bashrc.read_text(encoding="utf-8"), original)

    def test_unrelated_shell_aliases_with_expansions_survive_activation(self):
        original = "alias alert='notify-send \"$USER\"'\nalias ll='ls $HOME'\n"
        self.bashrc.write_text(original, encoding="utf-8")

        installer.install_statusbar(self.prefix, self.bashrc, activate_bash=True)

        content = self.bashrc.read_text(encoding="utf-8")
        self.assertTrue(content.startswith(original))
        self.assertIn("# >>> model-router statusbar >>>", content)
        self.assertIn("# <<< model-router statusbar <<<", content)

    def test_project_enabled_preflights_dependencies_but_global_veto_stays_off(self):
        project_settings = self.root / ".model-router.json"
        project_settings.write_text('{"telemetry":{"enabled":true}}\n', encoding="utf-8")
        fake_codex = self.root / "fake-codex"
        fake_codex.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        fake_codex.chmod(0o755)
        environment = {
            "PATH": str(self.root),
            "HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.xdg),
            "CODEX_STATUS_CODEX": str(fake_codex),
        }
        original_cwd = Path.cwd()
        try:
            os.chdir(self.root)
            with patch.dict(os.environ, environment):
                bash_before = self.bashrc.read_bytes()
                project_prefix = self.root / "project-enabled"
                with self.assertRaises((ValueError, RuntimeError)):
                    installer.install_statusbar(
                        project_prefix, self.bashrc, activate_bash=True)
                self.assertFalse((project_prefix / "bin" / "codex-status").exists())
                self.assertFalse((project_prefix / "share" / "model-router" / "statusbar").exists())
                self.assertEqual(self.bashrc.read_bytes(), bash_before)
                self.assertFalse((self.xdg / "model-router" / "settings.json").exists())

                global_settings = self.xdg / "model-router" / "settings.json"
                global_settings.parent.mkdir(parents=True)
                global_settings.write_text('{"telemetry":{"enabled":false}}\n', encoding="utf-8")
                off_prefix = self.root / "global-veto"
                with patch.dict(os.environ, {"CODEX_STATUS_CODEX": str(self.root / "missing-codex")}):
                    result = installer.install_statusbar(
                        off_prefix, self.bashrc, activate_bash=True)
                self.assertFalse(result["telemetry_enabled"])
                self.assertTrue((off_prefix / "bin" / "codex-status").exists())
                self.assertIn("# >>> model-router statusbar >>>",
                              self.bashrc.read_text(encoding="utf-8"))
                self.assertFalse(get_settings(self.root)["enabled"])
        finally:
            os.chdir(original_cwd)

    def test_uninstalling_another_prefix_preserves_existing_activation(self):
        other_prefix = self.root / "other-prefix"
        installer.install_statusbar(self.prefix, self.bashrc, activate_bash=True)
        activated = self.bashrc.read_text(encoding="utf-8")

        installer.uninstall_statusbar(other_prefix, self.bashrc)
        self.assertEqual(self.bashrc.read_text(encoding="utf-8"), activated)

        with self.assertRaises((ValueError, RuntimeError)):
            installer.install_statusbar(other_prefix, self.bashrc, activate_bash=True)
        self.assertEqual(self.bashrc.read_text(encoding="utf-8"), activated)

    def test_invalid_global_settings_reject_opt_in_before_install_side_effects(self):
        settings = self.xdg / "model-router" / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text("{ malformed", encoding="utf-8")
        bash_before = self.bashrc.read_bytes()

        with self.assertRaises((ValueError, RuntimeError)):
            installer.install_statusbar(
                self.prefix, self.bashrc, enable_telemetry=True, activate_bash=True)

        self.assertFalse((self.prefix / "bin" / "codex-status").exists())
        self.assertFalse((self.prefix / "share" / "model-router" / "statusbar").exists())
        self.assertEqual(self.bashrc.read_bytes(), bash_before)
        self.assertEqual(settings.read_text(encoding="utf-8"), "{ malformed")

    def test_failed_final_bash_write_restores_prior_install_and_settings(self):
        installer.install_statusbar(self.prefix, self.bashrc)
        settings = self.xdg / "model-router" / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text('{"telemetry":{"enabled":false},"keep":"value"}\n', encoding="utf-8")
        entry = self.prefix / "bin" / "codex-status"
        bundle = self.prefix / "share" / "model-router" / "statusbar"
        entry_before = entry.read_bytes()
        bundle_before = {path.relative_to(bundle): path.read_bytes()
                         for path in bundle.rglob("*") if path.is_file()}
        bash_before = self.bashrc.read_bytes()
        settings_before = settings.read_bytes()
        write = installer._write

        def fail_bash_write(path, content, executable=False):
            if Path(path) == self.bashrc:
                raise OSError("injected final Bash write failure")
            return write(path, content, executable)

        with patch.object(installer, "_write", side_effect=fail_bash_write):
            with self.assertRaises(OSError):
                installer.install_statusbar(
                    self.prefix, self.bashrc, enable_telemetry=True, activate_bash=True)

        self.assertEqual(entry.read_bytes(), entry_before)
        self.assertEqual({path.relative_to(bundle): path.read_bytes()
                          for path in bundle.rglob("*") if path.is_file()}, bundle_before)
        self.assertEqual(self.bashrc.read_bytes(), bash_before)
        self.assertEqual(settings.read_bytes(), settings_before)

    def test_failed_settings_write_restores_prior_install_bash_and_settings(self):
        installer.install_statusbar(self.prefix, self.bashrc)
        settings = self.xdg / "model-router" / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text('{"telemetry":{"enabled":false},"keep":"value"}\n', encoding="utf-8")
        entry = self.prefix / "bin" / "codex-status"
        bundle = self.prefix / "share" / "model-router" / "statusbar"
        entry_before = entry.read_bytes()
        bundle_before = {path.relative_to(bundle): path.read_bytes()
                         for path in bundle.rglob("*") if path.is_file()}
        bash_before = self.bashrc.read_bytes()
        settings_before = settings.read_bytes()
        set_telemetry = installer.set_telemetry

        def write_then_fail(*args, **kwargs):
            set_telemetry(*args, **kwargs)
            raise OSError("injected settings write failure")

        with patch.object(installer, "set_telemetry", side_effect=write_then_fail):
            with self.assertRaises(OSError):
                installer.install_statusbar(
                    self.prefix, self.bashrc, enable_telemetry=True, activate_bash=True)

        self.assertEqual(entry.read_bytes(), entry_before)
        self.assertEqual({path.relative_to(bundle): path.read_bytes()
                          for path in bundle.rglob("*") if path.is_file()}, bundle_before)
        self.assertEqual(self.bashrc.read_bytes(), bash_before)
        self.assertEqual(settings.read_bytes(), settings_before)

    def test_telemetry_install_preflights_codex_and_tmux_but_dashboard_off_installs(self):
        fake_codex = self.root / "codex"
        fake_codex.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        fake_codex.chmod(0o755)
        fake_tmux = self.root / "tmux"
        fake_tmux.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        fake_tmux.chmod(0o755)
        missing = str(self.root / "missing-codex")
        environment = {"PATH": str(self.root), "HOME": str(self.home),
                       "XDG_CONFIG_HOME": str(self.xdg)}

        off_prefix = self.root / "dashboard-only"
        with patch.dict(os.environ, {**environment, "CODEX_STATUS_CODEX": missing}):
            installer.install_statusbar(off_prefix, self.bashrc)
        self.assertTrue((off_prefix / "bin" / "codex-status").exists())
        self.assertFalse(get_settings(self.root)["enabled"])

        for label, env in (
            ("tmux missing", {**environment, "CODEX_STATUS_CODEX": str(fake_codex)}),
            ("Codex missing", {**environment, "CODEX_STATUS_CODEX": missing}),
        ):
            with self.subTest(dependency=label):
                prefix = self.root / label.replace(" ", "-")
                before = self.bashrc.read_bytes()
                with patch.dict(os.environ, env):
                    if label == "Codex missing":
                        # Keep tmux discoverable while forcing Codex resolution to fail.
                        with patch.object(installer.shutil, "which", return_value=str(fake_tmux)):
                            with self.assertRaises((ValueError, RuntimeError)):
                                installer.install_statusbar(
                                    prefix, self.bashrc, enable_telemetry=True, activate_bash=True)
                    else:
                        with patch.object(installer.shutil, "which", return_value=None):
                            with self.assertRaises((ValueError, RuntimeError)):
                                installer.install_statusbar(
                                    prefix, self.bashrc, enable_telemetry=True, activate_bash=True)
                self.assertFalse((prefix / "bin" / "codex-status").exists())
                self.assertFalse((prefix / "share" / "model-router" / "statusbar").exists())
                self.assertEqual(self.bashrc.read_bytes(), before)

    def test_launcher_passthrough_fake_binary_exit_and_version_arguments(self):
        fake = self.root / "fake-codex"
        stdout_path = self.root / "version-output.txt"
        fake.write_text("#!/usr/bin/env python3\nimport sys\n"
                        "print('FAKE VERSION ' + ' '.join(sys.argv[1:]))\n", encoding="utf-8")
        fake.chmod(0o755)
        installer.install_statusbar(self.prefix, self.bashrc)
        executable = self.prefix / "bin" / "codex-status"
        env = os.environ.copy()
        env["CODEX_STATUS_CODEX"] = str(fake)
        result = subprocess.run([str(executable), "--version", "--json"], cwd=self.root,
                                env=env, capture_output=True, text=True, check=False)
        stdout_path.write_text(result.stdout, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("FAKE VERSION --version --json", stdout_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
