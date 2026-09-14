r"""Start/status/stop one LAN peer, preserving its database and logs.

Usage: .venv/bin/python scripts/lan_node.py start --node C2
Windows: .venv\Scripts\python.exe scripts/lan_node.py start --node C1
"""
import argparse
import ctypes
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import httpx
from reid.config import load_config


def birth_token(pid):
    """OS process creation token; prevents stopping a reused PID."""
    if os.name != 'nt':
        try:
            text = Path(f'/proc/{pid}/stat').read_text()
            fields = text[text.rfind(')') + 2:].split()
            return fields[19] if fields[0] != 'Z' else None
        except (FileNotFoundError, ProcessLookupError):
            return None
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    created, exited, kernel_time, user_time = [wintypes.FILETIME() for _ in range(4)]
    try:
        if not kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel_time), ctypes.byref(user_time)):
            return None
        if exited.dwHighDateTime or exited.dwLowDateTime:
            return None
        return str((created.dwHighDateTime << 32) | created.dwLowDateTime)
    finally:
        kernel.CloseHandle(handle)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['start', 'status', 'stop'])
    parser.add_argument('--node', required=True, choices=['C1', 'C2', 'C3'])
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    cfg_path = root / 'config' / 'lan' / f'{args.node}.yaml'
    cfg = load_config(cfg_path)
    if cfg['node']['id'] != args.node:
        raise SystemExit('Configuration node ID mismatch')
    folder = Path(cfg['node']['database']).parent
    folder.mkdir(parents=True, exist_ok=True)
    process_path = folder / 'process.json'
    log_path = folder / 'peer.log'
    record = json.loads(process_path.read_text()) if process_path.exists() else None
    alive = bool(record and birth_token(record['pid']) == record['birth_token'])
    url = f"http://127.0.0.1:{cfg['node']['port']}"
    with httpx.Client(timeout=2, trust_env=False) as client:
        def status():
            try:
                response = client.get(url + '/api/status')
                response.raise_for_status()
                result = response.json()
                if result['node_id'] != args.node:
                    raise SystemExit('Port occupied by another node')
                return {k: result[k] for k in ['node_id', 'camera', 'height', 'block_hash', 'integrity', 'pending', 'consensus', 'quorum', 'peers']}
            except httpx.HTTPError:
                return None

        if args.action == 'status':
            print(json.dumps({'pid': record['pid'] if alive else None, 'status': status(), 'log': str(log_path)}))
            return
        if args.action == 'stop':
            if alive:
                # Let the lifespan handler flush final metrics and close the camera.
                # Windows SIGTERM terminates immediately, so try HTTP first.
                current = status()
                if current:
                    try:
                        response = client.post(url + '/api/shutdown')
                        response.raise_for_status()
                        for _ in range(120):
                            if birth_token(record['pid']) != record['birth_token']:
                                break
                            time.sleep(.1)
                    except httpx.HTTPError:
                        pass
                if birth_token(record['pid']) == record['birth_token']:
                    print('Graceful shutdown unavailable/timed out; stopping process. Last metrics checkpoint is retained.')
                    os.kill(record['pid'], signal.SIGTERM)
                for _ in range(50):
                    if birth_token(record['pid']) != record['birth_token']:
                        break
                    time.sleep(.1)
                else:
                    raise SystemExit('Shutdown still pending; inspect the process before retrying')
                process_path.unlink(missing_ok=True)
                print('Stopped ' + args.node)
            else:
                print('No managed process running for ' + args.node)
            return
        current = status()
        if current:
            print(json.dumps({'already_running': True, 'status': current}))
            return
        if alive:
            raise SystemExit('Managed process exists but HTTP is not ready; inspect ' + str(log_path))
        options = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {'start_new_session': True}
        with log_path.open('ab') as stream:
            process = subprocess.Popen([sys.executable, '-u', '-m', 'reid', 'run', '--config', str(cfg_path)],
                                       cwd=root, stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT, **options)
        token = birth_token(process.pid)
        if token is None:
            raise SystemExit('Child process exited immediately; inspect ' + str(log_path))
        process_path.write_text(json.dumps({'pid': process.pid, 'birth_token': token, 'node': args.node, 'config': str(cfg_path)}, indent=2))
        for _ in range(40):
            if process.poll() is not None:
                raise SystemExit('Node startup failed; inspect ' + str(log_path))
            current = status()
            if current:
                print(json.dumps({'started': True, 'pid': process.pid, 'status': current, 'log': str(log_path)}))
                return
            time.sleep(.25)
        raise SystemExit('Startup not ready yet; inspect ' + str(log_path))


if __name__ == '__main__':
    main()
