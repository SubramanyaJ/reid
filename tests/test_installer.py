"""Git-free installation, safe staging, and interactive launcher dispatch."""

import importlib.util
import io
import json
from pathlib import Path
import stat
import sys
import zipfile

import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "install.py"
SPEC = importlib.util.spec_from_file_location("reid_source_installer", SCRIPT_PATH)
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


def repository_archive(entries=None):
    if entries is None:
        entries = [
            ("reid-master/pyproject.toml", b"[project]\nname = 'example'\n"),
            ("reid-master/scripts/setup_node.py", b"def main(argv): return 0\n"),
            ("reid-master/src/reid/__main__.py", b"# application entry point\n"),
        ]
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, payload in entries:
            if isinstance(name, str):
                # ZipInfo normalizes OS separators on Windows; retain malicious
                # names exactly as an untrusted ZIP can encode them.
                entry = zipfile.ZipInfo(name)
                entry.filename = name
            else:
                entry = name
            archive.writestr(entry, payload)
    return output.getvalue()


def stub_download(monkeypatch, payload=None):
    downloads = []
    archive = repository_archive() if payload is None else payload

    def download(destination):
        downloads.append(destination)
        Path(destination).write_bytes(archive)

    monkeypatch.setattr(installer, "download_archive", download)
    return downloads


def test_install_extracts_project_and_records_repository_without_git(tmp_path, monkeypatch):
    downloads = stub_download(monkeypatch)
    target = tmp_path / "machine" / "reid"

    assert installer.install_project(target) == target.resolve()
    assert len(downloads) == 1
    assert all((target / name).is_file() for name in installer.REQUIRED_FILES)
    assert json.loads((target / installer.MARKER).read_text()) == {
        "version": 1,
        "repository": "https://github.com/SubramanyaJ/reid.git",
        "branch": installer.BRANCH,
    }
    assert list(target.parent.iterdir()) == [target]


def test_rerun_preserves_private_key_settings_and_database_without_downloading(tmp_path, monkeypatch):
    stub_download(monkeypatch)
    target = installer.install_project(tmp_path / "reid")
    state = {
        "keys/C2.private": b"machine-local-private-key",
        "config/node.yaml": b"node_id: C2\n",
        "data/ledger.sqlite": b"saved-ledger-state",
    }
    for relative, content in state.items():
        path = target / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    before = {path.relative_to(target): path.read_bytes() for path in target.rglob("*") if path.is_file()}
    monkeypatch.setattr(installer, "download_archive", lambda path: pytest.fail("reuse must work offline"))

    assert installer.install_project(target) == target
    after = {path.relative_to(target): path.read_bytes() for path in target.rglob("*") if path.is_file()}
    assert after == before


@pytest.mark.parametrize("existing_file", [False, True])
def test_unrelated_existing_destination_is_never_overwritten(tmp_path, monkeypatch, existing_file):
    target = tmp_path / "existing"
    if existing_file:
        target.write_bytes(b"unrelated file")
        sentinel = target
    else:
        target.mkdir()
        sentinel = target / "important.txt"
        sentinel.write_bytes(b"unrelated file")
    monkeypatch.setattr(installer, "download_archive", lambda path: pytest.fail("must not download"))

    with pytest.raises(installer.InstallError, match="already exists"):
        installer.install_project(target)
    assert sentinel.read_bytes() == b"unrelated file"
    assert list(tmp_path.iterdir()) == [target]


def test_interrupted_download_cleans_partial_files_and_can_be_retried(tmp_path, monkeypatch):
    target = tmp_path / "reid"

    def interrupted(destination):
        Path(destination).write_bytes(b"partial repository download")
        raise KeyboardInterrupt()

    monkeypatch.setattr(installer, "download_archive", interrupted)
    with pytest.raises(KeyboardInterrupt):
        installer.install_project(target)
    assert not target.exists()
    assert list(tmp_path.iterdir()) == []

    stub_download(monkeypatch)
    assert installer.install_project(target) == target
    assert (target / installer.MARKER).is_file()


def test_incomplete_repository_does_not_leave_an_installation(tmp_path, monkeypatch):
    stub_download(monkeypatch, repository_archive([("reid-master/README.md", b"old checkout")]))
    with pytest.raises(installer.InstallError, match="interactive setup files"):
        installer.install_project(tmp_path / "reid")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("unsafe_name", [
    "reid-master/../escape.txt",
    "reid-master/dir/../../escape.txt",
    "reid-master\\escape.txt",
    "C:/escape.txt",
    "/escape.txt",
    "reid-master/file:stream",
    "reid-master/CON.txt",
    "reid-master/aux/data.txt",
    "reid-master/COM1.log",
    "reid-master/lpt9",
    "reid-master/trailing.",
    "reid-master/trailing ",
    "reid-master//empty.txt",
])
def test_unsafe_archive_is_rejected_before_any_file_is_extracted(tmp_path, unsafe_name):
    archive = tmp_path / "download.zip"
    archive.write_bytes(repository_archive([
        ("reid-master/valid.txt", b"must not be written first"),
        (unsafe_name, b"unsafe"),
    ]))
    destination = tmp_path / "extracted"
    destination.mkdir()

    with pytest.raises(installer.InstallError):
        installer.extract_archive(archive, destination)
    assert list(destination.iterdir()) == []


@pytest.mark.parametrize("entries", [
    [("reid-master/Name.txt", b"first"), ("reid-master/name.txt", b"duplicate")],
    [("reid-master/one.txt", b"first"), ("other-root/two.txt", b"second")],
])
def test_duplicate_paths_and_multiple_roots_are_rejected_before_extraction(tmp_path, entries):
    archive = tmp_path / "download.zip"
    archive.write_bytes(repository_archive(entries))
    destination = tmp_path / "extracted"
    destination.mkdir()

    with pytest.raises(installer.InstallError):
        installer.extract_archive(archive, destination)
    assert list(destination.iterdir()) == []


def test_archive_symlink_is_rejected_before_extraction(tmp_path):
    link = zipfile.ZipInfo("reid-master/link")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    archive = tmp_path / "download.zip"
    archive.write_bytes(repository_archive([
        ("reid-master/valid.txt", b"must not be extracted"),
        (link, b"../../escape.txt"),
    ]))
    destination = tmp_path / "extracted"
    destination.mkdir()

    with pytest.raises(installer.InstallError, match="symbolic link"):
        installer.extract_archive(archive, destination)
    assert list(destination.iterdir()) == []


def test_bootstrap_can_be_executed_without_file_and_help_is_offline(monkeypatch, capsys):
    namespace = {"__name__": "downloaded_installer"}
    exec(compile(SCRIPT_PATH.read_text(encoding="utf-8"), "<downloaded installer>", "exec"), namespace)
    assert "__file__" not in namespace
    namespace["install_project"] = lambda directory: pytest.fail("help must not install anything")
    monkeypatch.setattr(sys, "argv", ["-c", "--help"])

    with pytest.raises(SystemExit) as error:
        namespace["main"]()
    assert error.value.code == 0
    assert "--directory" in capsys.readouterr().out


@pytest.mark.parametrize("use_sys_argv", [False, True])
def test_main_uses_selected_directory_and_forwards_setup_arguments(tmp_path, monkeypatch, use_sys_argv):
    target = tmp_path / "install with spaces"
    calls = []

    def install(directory):
        calls.append(("install", directory))
        return target

    def launch(project, arguments):
        calls.append(("launch", project, arguments))
        return 7

    monkeypatch.setattr(installer, "install_project", install)
    monkeypatch.setattr(installer, "launch_setup", launch)
    arguments = ["--node-id", "C2", "--directory", str(target), "--timeout", "90", "--skip-install"]
    monkeypatch.setattr(sys, "argv", ["-c", *arguments] if use_sys_argv else ["-c", "--unrelated"])

    assert installer.main(None if use_sys_argv else arguments) == 7
    assert calls == [
        ("install", target),
        ("launch", target, ["--node-id", "C2", "--timeout", "90", "--skip-install"]),
    ]


def test_launch_setup_calls_launcher_main_without_consuming_terminal_input(tmp_path, monkeypatch):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "setup_node.py").write_text(
        "import sys\n"
        "def main(argv):\n"
        "    assert argv == ['--node-id', 'C2']\n"
        "    assert sys.stdin.readline() == 'interactive response\\n'\n"
        "    return 9\n"
        "if __name__ == '__main__':\n"
        "    raise AssertionError('launcher main must receive arguments explicitly')\n",
        encoding="utf-8",
    )
    terminal = io.StringIO("interactive response\n")
    monkeypatch.setattr(sys, "stdin", terminal)

    assert installer.launch_setup(tmp_path, ["--node-id", "C2"]) == 9
    assert not terminal.closed
