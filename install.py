#!/usr/bin/env python3
"""Download Re-ID from GitHub and start interactive setup, without Git.

This file also runs when downloaded and executed with ``python -c``. It uses
only Python's standard library; the project's launcher installs dependencies.
"""
import argparse
import json
from pathlib import Path, PurePosixPath
import runpy
import shutil
import stat
import sys
import tempfile
from urllib.error import URLError
from urllib.request import Request, urlopen
import zipfile


REPOSITORY = "https://github.com/SubramanyaJ/reid.git"
BRANCH = "master"
ARCHIVE_URL = "https://github.com/SubramanyaJ/reid/archive/refs/heads/master.zip"
MARKER = ".reid-install.json"
REQUIRED_FILES = ("pyproject.toml", "scripts/setup_node.py", "src/reid/__main__.py")
MAX_DOWNLOAD = 100 * 1024 * 1024
MAX_EXTRACTED = 256 * 1024 * 1024
MAX_FILES = 20000


class InstallError(Exception):
    """A failure that can be explained without a Python traceback."""


def status(message):
    print("[install] " + message, flush=True)


def download_archive(destination):
    status("Downloading " + REPOSITORY + " (" + BRANCH + ")...")
    request = Request(ARCHIVE_URL, headers={"User-Agent": "reid-machine-installer/1"})
    try:
        with urlopen(request, timeout=30) as response, Path(destination).open("xb") as stream:
            total = 0
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_DOWNLOAD:
                    raise InstallError("Repository download exceeds the 100 MiB installer limit.")
                stream.write(chunk)
            if not total:
                raise InstallError("GitHub returned an empty repository download.")
        status(f"Downloaded {total / (1024 * 1024):.1f} MiB.")
    except (URLError, TimeoutError) as error:
        raise InstallError("Could not download the repository. Check internet access and the GitHub URL. "
                           + str(error)) from error


def extract_archive(archive_path, destination):
    """Validate all ZIP paths before writing anything into a temporary directory."""
    destination = Path(destination).resolve()
    with zipfile.ZipFile(archive_path) as archive:
        entries = archive.infolist()
        if not entries or len(entries) > MAX_FILES or sum(e.file_size for e in entries) > MAX_EXTRACTED:
            raise InstallError("Repository archive is empty or exceeds extraction limits.")
        roots, seen = set(), set()
        validated = []
        for entry in entries:
            # ZipInfo.filename may already have normalized separators or
            # truncated a NUL. Check the original archive name on every OS.
            name = entry.orig_filename.rstrip("/")
            segments = name.split("/")
            if (not name or "\\" in name or "\x00" in name or any(part in ("", ".", "..") or ":" in part
                    or part.endswith((" ", ".")) for part in segments)
                    or PurePosixPath(name).is_absolute() or stat.S_ISLNK(entry.external_attr >> 16)):
                raise InstallError("Repository archive contains an unsafe path or symbolic link.")
            # Reject Windows device paths even when installing on another OS.
            if any(part.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL"}
                   or part.split(".")[0].upper() in {f"{kind}{number}" for kind in ("COM", "LPT") for number in range(1, 10)}
                   for part in segments):
                raise InstallError("Repository archive contains a reserved device name.")
            folded = name.casefold()
            if folded in seen:
                raise InstallError("Repository archive contains duplicate paths.")
            seen.add(folded)
            roots.add(segments[0])
            target = destination.joinpath(*segments)
            if not target.resolve().is_relative_to(destination):
                raise InstallError("Repository archive would escape its download directory.")
            validated.append((entry, target))
        if len(roots) != 1:
            raise InstallError("Expected one project directory in the GitHub archive.")
        for entry, target in validated:
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(entry) as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output)
    return destination / roots.pop()


def validate_project(project):
    missing = [name for name in REQUIRED_FILES if not (project / name).is_file()]
    if missing:
        raise InstallError("The repository does not contain the interactive setup files: " + ", ".join(missing)
                           + ". Publish install.py and the setup changes to GitHub's master branch first.")


def install_project(directory):
    directory = Path(directory).expanduser().resolve()
    identity = {"version": 1, "repository": REPOSITORY, "branch": BRANCH}
    if directory.exists():
        try:
            saved = json.loads((directory / MARKER).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            saved = None
        if saved != identity:
            raise InstallError(f"{directory} already exists and was not created by this installer. "
                               "Use --directory with a new location, or run scripts/setup_node.py in your existing checkout.")
        validate_project(directory)
        status(f"Reusing {directory}. Saved keys, settings and databases are preserved; no update is performed.")
        return directory

    directory.parent.mkdir(parents=True, exist_ok=True)
    # Stage beneath the explicitly selected install parent; nothing is extracted
    # over an existing installation and temporary cleanup stays in this folder.
    with tempfile.TemporaryDirectory(prefix=".reid-download-", dir=directory.parent) as temporary:
        staging = Path(temporary).resolve()
        if staging.parent != directory.parent:
            raise InstallError("Unexpected download directory; refusing to install.")
        archive = staging / "repository.zip"
        download_archive(archive)
        status("Checking and extracting repository files...")
        extracted = staging / "source"
        extracted.mkdir()
        project = extract_archive(archive, extracted)
        validate_project(project)
        (project / MARKER).write_text(json.dumps(identity, indent=2) + "\n", encoding="utf-8")
        if directory.exists() or project.resolve().parent != extracted.resolve():
            raise InstallError("Installation destination changed during download; no existing files were replaced.")
        project.rename(directory)
    status(f"Installed source in {directory}.")
    return directory


def launch_setup(project, setup_args):
    # Run the stdlib-only launcher in this process so terminal input and Ctrl+C
    # remain attached to the interactive setup, including when bootstrapped by -c.
    launcher = runpy.run_path(str(project / "scripts" / "setup_node.py"))
    return launcher["main"](setup_args)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Download Re-ID from GitHub and start per-machine setup; Git is not required.",
        allow_abbrev=False,
        epilog="Other options (such as --timeout and --skip-install) are forwarded to the setup launcher.",
    )
    parser.add_argument("--directory", type=Path, default=Path.home() / "reid",
                        help="Installation directory (default: %(default)s); existing installer-owned copies are reused")
    options, setup_args = parser.parse_known_args(argv)
    if sys.version_info < (3, 10):
        parser.exit(2, "Error: install Python 3.10 or newer before running this installer.\n")
    try:
        status("Source: " + REPOSITORY)
        project = install_project(options.directory)
        status("Starting environment installation and interactive node setup...")
        return launch_setup(project, setup_args)
    except KeyboardInterrupt:
        print("\n[install] Interrupted. Rerun the same command to continue.", file=sys.stderr)
        return 130
    except (InstallError, OSError, ValueError, zipfile.BadZipFile) as error:
        print("[install] Error: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
