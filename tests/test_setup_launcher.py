"""Bootstrap contracts: local installation, offline reuse, and argument dispatch."""

import importlib.util
import json
from pathlib import Path
import subprocess

import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "setup_node.py"
SPEC = importlib.util.spec_from_file_location("setup_node_launcher", SCRIPT_PATH)
launcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(launcher)


def existing_environment(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'test'\n")
    python = tmp_path / ".venv" / "python"
    python.parent.mkdir()
    python.touch()
    monkeypatch.setattr(launcher, "environment_python", lambda root: python)
    monkeypatch.setattr(
        launcher,
        "probe_environment",
        lambda *args: {"project_installed": True, "unavailable": []},
    )
    return python


def test_help_never_prepares_environment(monkeypatch, capsys):
    def unexpected(*args, **kwargs):
        pytest.fail("help must not prepare an environment")

    monkeypatch.setattr(launcher, "prepare_environment", unexpected)
    with pytest.raises(SystemExit) as error:
        launcher.main(["--help"])
    assert error.value.code == 0
    assert "--skip-install" in capsys.readouterr().out


def test_verified_installation_is_reused_and_pyproject_change_triggers_install(tmp_path, monkeypatch):
    python = existing_environment(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(launcher, "run_checked", lambda *args: calls.append(args))
    marker = tmp_path / ".venv" / launcher.MARKER_NAME
    marker.write_text(json.dumps(launcher.expected_install_marker(tmp_path)))

    assert launcher.prepare_environment(tmp_path) == python
    assert calls == []

    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'changed'\n")
    assert launcher.prepare_environment(tmp_path) == python
    assert calls[0][0] == [str(python), "-m", "pip", "install", "--editable", str(tmp_path)]
    assert json.loads(marker.read_text()) == launcher.expected_install_marker(tmp_path)


def test_skip_install_rejects_missing_dependencies_without_running_pip(tmp_path, monkeypatch):
    existing_environment(tmp_path, monkeypatch)
    monkeypatch.setattr(
        launcher,
        "probe_environment",
        lambda *args: {"project_installed": True, "unavailable": ["cv2: missing"]},
    )
    monkeypatch.setattr(launcher, "run_checked", lambda *args: pytest.fail("must not run pip"))
    with pytest.raises(launcher.SetupError, match="cv2: missing"):
        launcher.prepare_environment(tmp_path, skip_install=True)


def test_skip_install_works_offline_without_install_marker(tmp_path, monkeypatch):
    python = existing_environment(tmp_path, monkeypatch)
    monkeypatch.setattr(launcher, "run_checked", lambda *args: pytest.fail("must not run pip"))
    assert launcher.prepare_environment(tmp_path, skip_install=True) == python
    assert not (tmp_path / ".venv" / launcher.MARKER_NAME).exists()


def test_launcher_forwards_setup_arguments_and_child_exit_code(monkeypatch):
    project_root = SCRIPT_PATH.parents[1]
    python = project_root / "test-python"
    calls = []
    monkeypatch.setattr(
        launcher, "prepare_environment", lambda root, skip_install: python if skip_install else None
    )

    def popen(command, **kwargs):
        calls.append((command, kwargs))
        return FakeChild([7])

    monkeypatch.setattr(launcher.subprocess, "Popen", popen)
    assert launcher.main(["--skip-install", "--node-id", "C2", "--port", "9100"]) == 7
    assert calls == [
        ([str(python), "-m", "reid", "setup", "--node-id", "C2", "--port", "9100"],
         {"cwd": str(project_root)})
    ]


class FakeChild:
    def __init__(self, outcomes):
        self.outcomes = iter(outcomes)
        self.waits = []
        self.terminated = 0
        self.killed = 0

    def wait(self, timeout=None):
        self.waits.append(timeout)
        outcome = next(self.outcomes)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def terminate(self):
        self.terminated += 1

    def kill(self):
        self.killed += 1


def test_first_interrupt_allows_node_cleanup_without_termination(tmp_path, monkeypatch, capsys):
    child = FakeChild([KeyboardInterrupt(), 0])
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda *args, **kwargs: child)

    assert launcher.run_setup_child(["python", "-m", "reid", "setup"], tmp_path) == 130
    assert child.waits == [None, 15]
    assert child.terminated == child.killed == 0
    assert "15 seconds for cleanup" in capsys.readouterr().out


def test_cleanup_timeout_terminates_child_with_an_explicit_message(tmp_path, monkeypatch, capsys):
    child = FakeChild([KeyboardInterrupt(), subprocess.TimeoutExpired("python", 15), 0])
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda *args, **kwargs: child)

    assert launcher.run_setup_child(["python"], tmp_path) == 130
    assert child.waits == [None, 15, 5]
    assert child.terminated == 1
    assert child.killed == 0
    assert "Forcing process termination" in capsys.readouterr().out


def test_second_interrupt_forces_termination_and_kills_unresponsive_child(tmp_path, monkeypatch, capsys):
    child = FakeChild([
        KeyboardInterrupt(), KeyboardInterrupt(), subprocess.TimeoutExpired("python", 5), -9
    ])
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda *args, **kwargs: child)

    assert launcher.run_setup_child(["python"], tmp_path) == 130
    assert child.waits == [None, 15, 5, None]
    assert child.terminated == child.killed == 1
    assert "killing it now" in capsys.readouterr().out
