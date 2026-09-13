param([ValidateSet('start', 'status', 'stop')][string]$Action = 'status')
$ErrorActionPreference = 'Stop'
$ReidProject = Split-Path -Parent $PSScriptRoot
$ReidPython = Join-Path $ReidProject '.venv\Scripts\python.exe'
$ReidControl = Join-Path $PSScriptRoot 'lan_node.py'
$ReidSsh = Join-Path $env:WINDIR 'System32\OpenSSH\ssh.exe'
& $ReidPython $ReidControl $Action --node C1
if ($LASTEXITCODE -ne 0) { throw 'C1 control failed' }
& $ReidSsh -o BatchMode=yes -o ConnectTimeout=10 sub@10.78.223.1 "cd /home/sub/reid && .venv/bin/python scripts/lan_node.py $Action --node C2"
if ($LASTEXITCODE -ne 0) { throw 'C2 control failed' }
& $ReidSsh -o BatchMode=yes -o ConnectTimeout=10 sub@10.78.223.84 "cd /home/sub/reid && .venv/bin/python scripts/lan_node.py $Action --node C3"
if ($LASTEXITCODE -ne 0) { throw 'C3 control failed' }
