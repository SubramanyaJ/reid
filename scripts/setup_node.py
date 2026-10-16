#!/usr/bin/env python3
"""Bootstrap one machine, then start the interactive ``reid setup`` command.

This launcher deliberately uses only Python's standard library. Run it from a
fresh checkout with Python 3.10 or newer; no globally installed project packages
are required. Identity keys are managed by ``reid setup``, never by this launcher.
"""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys


MIN_PYTHON = (3, 10)
MARKER_NAME = ".reid-setup-install.json"
PROBE_PREFIX = "REID_SETUP_ENVIRONMENT="
ENVIRONMENT_PROBE = r'''
import importlib
import importlib.metadata
import json
from pathlib import Path
import sys
from urllib.parse import urlparse
from urllib.request import url2pathname

result = {"project_installed": False, "unavailable": []}
if sys.version_info < (3, 10):
    result["unavailable"].append("the .venv interpreter is older than Python 3.10")
try:
    distribution = importlib.metadata.distribution("classical-vehicle-reid")
    direct_url = json.loads(distribution.read_text("direct_url.json") or "{}")
    location = urlparse(direct_url.get("url", ""))
    result["project_installed"] = (
        location.scheme == "file"
        and direct_url.get("dir_info", {}).get("editable", False)
        and Path(url2pathname(location.path)).resolve() == Path(sys.argv[1]).resolve()
    )
except (importlib.metadata.PackageNotFoundError, ValueError, OSError):
    pass

for module in (
    "reid", "numpy", "cv2", "scipy", "yaml", "cryptography", "fastapi",
    "uvicorn", "httpx", "matplotlib",
):
    try:
        importlib.import_module(module)
    except Exception as error:
        result["unavailable"].append(module + ": " + str(error))

print("REID_SETUP_ENVIRONMENT=" + json.dumps(result))
'''


class SetupError(Exception):
    """An actionable bootstrap failure, displayed without a traceback."""


def status(message):
    print("[setup] " + message, flush=True)


def build_parser():
    parser = argparse.ArgumentParser(
        allow_abbrev=False,
        description=(
            "Prepare this machine's project virtual environment, then launch "
            "interactive node setup. Each machine creates and retains its own "
            "private key; node setup exchanges public identity information."
        ),
        epilog=(
            "All other arguments are passed unchanged to 'python -m reid setup'. "
            "With no extra arguments, setup prompts for this machine's node "
            "identity, network settings, and public peer information. "
            "This help screen does not create files or install dependencies."
        ),
    )
    parser.add_argument(
        "--skip-install",
        action="store_true",
        help="Use an existing .venv without pip or network access; fail if required packages are unavailable.",
    )
    return parser


def environment_python(project_root):
    environment = project_root / ".venv"
    if sys.platform == "win32":
        return environment / "Scripts" / "python.exe"
    return environment / "bin" / "python"


def run_checked(command, project_root, description):
    try:
        subprocess.run(command, cwd=str(project_root), check=True)
    except subprocess.CalledProcessError as error:
        raise SetupError(
            "{} failed (exit {}). Fix the error above and run setup again.".format(
                description, error.returncode
            )
        ) from error
    except OSError as error:
        raise SetupError("{} could not start: {}".format(description, error)) from error


def probe_environment(python, project_root):
    try:
        result = subprocess.run(
            [str(python), "-c", ENVIRONMENT_PROBE, str(project_root)],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        details = getattr(error, "stderr", None) or str(error)
        raise SetupError(
            "Cannot run the existing virtual environment at {}. "
            "Repair or rename .venv, then run setup again. {}".format(python, details.strip())
        ) from error
    for line in reversed(result.stdout.splitlines()):
        if line.startswith(PROBE_PREFIX):
            try:
                return json.loads(line[len(PROBE_PREFIX):])
            except ValueError:
                break
    raise SetupError("The virtual environment check returned an unreadable result.")


def expected_install_marker(project_root):
    return {
        "project_root": str(project_root.resolve()),
        "pyproject_sha256": hashlib.sha256(
            (project_root / "pyproject.toml").read_bytes()
        ).hexdigest(),
    }


def marker_matches(path, expected):
    try:
        return json.loads(path.read_text(encoding="utf-8")) == expected
    except (OSError, ValueError):
        return False


def prepare_environment(project_root, skip_install=False):
    python = environment_python(project_root)
    if not python.is_file():
        if skip_install:
            raise SetupError(
                "--skip-install needs an existing .venv. Run without this option "
                "once to create it and install the project."
            )
        status("Creating isolated Python environment: {}".format(project_root / ".venv"))
        run_checked(
            [sys.executable, "-m", "venv", str(project_root / ".venv")],
            project_root,
            "Virtual environment creation (Python's venv and pip support are required)",
        )
    else:
        status("Reusing virtual environment: {}".format(project_root / ".venv"))

    status("Checking project installation and runtime dependencies...")
    environment = probe_environment(python, project_root)
    usable = environment["project_installed"] and not environment["unavailable"]
    marker = project_root / ".venv" / MARKER_NAME
    expected = expected_install_marker(project_root)
    if skip_install:
        if not usable:
            details = "; ".join(environment["unavailable"])
            if not environment["project_installed"]:
                details = "this checkout is not installed in editable mode" + (
                    "; " + details if details else ""
                )
            raise SetupError(
                "--skip-install cannot use this environment: {}. "
                "Run without --skip-install to install the project.".format(details)
            )
        status("Installation skipped (--skip-install); runtime imports are available.")
    elif usable and marker_matches(marker, expected):
        status("Project and dependencies are ready; no installation needed.")
    else:
        status("Installing this project and its runtime dependencies (internet may be required)...")
        run_checked(
            [str(python), "-m", "pip", "install", "--editable", str(project_root)],
            project_root,
            "Project installation",
        )
        environment = probe_environment(python, project_root)
        if not environment["project_installed"] or environment["unavailable"]:
            details = "; ".join(environment["unavailable"])
            raise SetupError(
                "Installation finished but the environment is not ready. {}".format(details)
            )
        marker.write_text(json.dumps(expected, indent=2) + "\n", encoding="utf-8")
        status("Project installation verified.")
    return python


def run_setup_child(command, project_root):
    # subprocess.run() kills its child when interrupted. The node needs time to
    # stop camera/network workers and flush its metrics after console Ctrl+C.
    child = subprocess.Popen(command, cwd=str(project_root))
    try:
        return child.wait()
    except KeyboardInterrupt:
        status("Stopping node; allowing up to 15 seconds for cleanup. Press Ctrl+C again to force termination.")
        try:
            child.wait(timeout=15)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            status("Forcing process termination; graceful shutdown did not finish or was interrupted again.")
            try:
                child.terminate()
            except ProcessLookupError:
                pass  # It finished between wait() and terminate().
            try:
                child.wait(timeout=5)
            except (subprocess.TimeoutExpired, KeyboardInterrupt):
                status("Process has not stopped; killing it now.")
                try:
                    child.kill()
                except ProcessLookupError:
                    pass
                child.wait()
        return 130


def main(argv=None):
    parser = build_parser()
    options, setup_args = parser.parse_known_args(argv)
    if sys.version_info < MIN_PYTHON:
        parser.exit(2, "Error: setup requires Python 3.10 or newer.\n")
    project_root = Path(__file__).resolve().parents[1]
    try:
        status("Preparing this machine with Python {}.{}.{}.".format(*sys.version_info[:3]))
        python = prepare_environment(project_root, skip_install=options.skip_install)
        status("Environment ready. Starting interactive node setup...")
        return run_setup_child(
            [str(python), "-m", "reid", "setup", *setup_args],
            project_root,
        )
    except KeyboardInterrupt:
        print("\n[setup] Interrupted. Run this script again to continue.", file=sys.stderr)
        return 130
    except (SetupError, OSError) as error:
        print("[setup] Error: {}".format(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
