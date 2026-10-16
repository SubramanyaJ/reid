"""Interactive setup lifecycle, independent restarts, and interruption recovery."""
from copy import deepcopy
import json

import pytest

from reid import commands, setup
from reid.cli import main
from reid.config import load_config


def answers(monkeypatch, values):
    values = iter(values)
    monkeypatch.setattr("builtins.input", lambda prompt: next(values))


def local_exchange(enrollment, **kwargs):
    members = enrollment.snapshot()
    kwargs["ready"](members)
    return members


def test_wizard_enrolls_then_restarts_without_exchanging_keys(tmp_path, monkeypatch):
    # A one-peer topology isolates the interactive lifecycle from LAN availability.
    answers(monkeypatch, ["Local", "Local", "127.0.0.1:19000", "none", "24", "", "n"])
    monkeypatch.setattr(setup.getpass, "getpass", lambda prompt: "shared-enrollment-code")
    monkeypatch.setattr(setup, "exchange", local_exchange)
    main(["setup", "--out", str(tmp_path)])
    cfg = load_config(tmp_path / "Local.yaml")
    assert cfg["camera"]["source"] is None
    assert cfg["camera"]["fps"] == 24
    key = (tmp_path / "keys" / "Local.pem").read_bytes()
    public = (tmp_path / "public.json").read_text()
    state = (tmp_path / "setup.json").read_text()
    assert "shared-enrollment-code" not in public + state
    assert "PRIVATE KEY" not in public + state
    assert json.loads(public)["member"] == cfg["members"][0]

    # Subsequent startup needs neither other setup services nor the enrollment code.
    answers(monkeypatch, ["1"])
    monkeypatch.setattr(setup.getpass, "getpass", lambda prompt: pytest.fail("must not ask for enrollment again"))
    monkeypatch.setattr(setup, "exchange", lambda *a, **k: pytest.fail("must not exchange on independent startup"))
    started = []
    monkeypatch.setattr(commands, "run_peer", lambda args: started.append(args.config))
    main(["setup", "--out", str(tmp_path)])
    assert started == [str(tmp_path / "Local.yaml")]
    assert (tmp_path / "keys" / "Local.pem").read_bytes() == key


def test_interrupted_wizard_retries_using_the_saved_local_identity(tmp_path, monkeypatch):
    answers(monkeypatch, ["Local", "Local", "127.0.0.1:19000", "0", "20"])
    monkeypatch.setattr(setup.getpass, "getpass", lambda prompt: "shared-enrollment-code")

    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(setup, "exchange", interrupt)
    with pytest.raises(SystemExit) as error:
        main(["setup", "--out", str(tmp_path)])
    assert error.value.code == 130
    key = (tmp_path / "keys" / "Local.pem").read_bytes()
    assert not (tmp_path / "Local.yaml").exists()

    # Retry uses the saved settings; only retry/ready/launch answers remain.
    answers(monkeypatch, ["1", "", "n"])
    monkeypatch.setattr(setup, "exchange", local_exchange)
    main(["setup", "--out", str(tmp_path)])
    assert (tmp_path / "keys" / "Local.pem").read_bytes() == key
    assert load_config(tmp_path / "Local.yaml")["camera"]["source"] == 0


def test_unfinished_settings_can_be_corrected_without_replacing_the_key(tmp_path, monkeypatch):
    key = setup.identity(tmp_path, "Local")
    state = {"version": 1, "id": "Local", "peers": [{"id": "Local", "url": "http://127.0.0.1:18000"}],
             "camera": {"source": None, "fps": 20}, "database": "state.sqlite"}
    (tmp_path / "setup.json").write_text(json.dumps(state))
    pem = (tmp_path / "keys" / "Local.pem").read_bytes()
    answers(monkeypatch, ["2", "Local", "127.0.0.1:19000", "1", "24", "", "n"])
    monkeypatch.setattr(setup.getpass, "getpass", lambda prompt: "shared-enrollment-code")
    monkeypatch.setattr(setup, "exchange", local_exchange)
    main(["setup", "--out", str(tmp_path)])
    cfg = load_config(tmp_path / "Local.yaml")
    assert cfg["members"][0]["url"] == "http://127.0.0.1:19000"
    assert cfg["camera"]["source"] == 1
    assert cfg["camera"]["fps"] == 24
    assert (tmp_path / "keys" / "Local.pem").read_bytes() == pem


def test_invalid_config_is_not_committed_and_private_fields_cannot_be_exported(tmp_path):
    key = setup.identity(tmp_path, "Local")
    peers = [{"id": "Local", "url": "http://[::1]:19000"}]
    enrollment = setup.Enrollment("Local", key, peers, "shared-enrollment-code")
    state = {"id": "Local", "peers": peers, "camera": {"fps": -1}, "database": "state.sqlite"}
    with pytest.raises(ValueError, match="positive and finite"):
        setup.write_config(tmp_path, state, enrollment.snapshot(), key)
    assert not (tmp_path / "Local.yaml").exists()
    assert not list(tmp_path.glob("*.tmp"))
    state["camera"]["fps"] = 20
    invalid = deepcopy(enrollment.snapshot())
    invalid[0]["private_key"] = "not allowed"
    with pytest.raises(ValueError, match="only public identities"):
        setup.write_config(tmp_path, state, invalid, key)
    path = setup.write_config(tmp_path, state, enrollment.snapshot(), key)
    assert load_config(path)["node"]["host"] == "::"


@pytest.mark.parametrize("timeout", ["0", "nan", "-1", "inf"])
def test_invalid_timeout_does_not_create_identity_files(tmp_path, timeout):
    out = tmp_path / "node"
    with pytest.raises(SystemExit) as error:
        main(["setup", "--out", str(out), "--timeout", timeout])
    assert error.value.code == 2
    assert not out.exists()
