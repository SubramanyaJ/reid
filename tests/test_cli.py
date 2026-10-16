"""Command dispatch contracts that must survive CLI reorganization."""
import pytest

from reid.cli import main
from reid.config import load_config


def test_init_dispatch_creates_loadable_configs_and_preserves_existing_keys(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    out = tmp_path / 'config'
    args = ['init', '--nodes', 'A', 'B', '--out', str(out)]
    main(args)
    first, second = (load_config(out / f'{node}.yaml') for node in ('A', 'B'))
    assert first['node']['port'] == 9000
    assert second['node']['port'] == 9001
    assert first['members'] == second['members']
    key = (out / 'keys' / 'A.pem').read_bytes()
    with pytest.raises(SystemExit) as error:
        main(args)
    assert error.value.code == 2
    assert (out / 'keys' / 'A.pem').read_bytes() == key


def test_command_validation_uses_cli_error_without_creating_output(tmp_path, capsys):
    out = tmp_path / 'unused'
    with pytest.raises(SystemExit) as error:
        main(['metrics', '--queries', '0', '--out', str(out)])
    assert error.value.code == 2
    assert 'positive queries' in capsys.readouterr().err
    assert not out.exists()
