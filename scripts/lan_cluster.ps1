param([ValidateSet('start', 'status', 'stop')][string]$Action = 'status')
$ErrorActionPreference = 'Stop'
$ReidProject = Split-Path -Parent $PSScriptRoot
$ReidPython = Join-Path $ReidProject '.venv\Scripts\python.exe'
$ReidControl = Join-Path $PSScriptRoot 'lan_node.py'
$ReidSsh = Join-Path $env:WINDIR 'System32\OpenSSH\ssh.exe'
$ReidFailures = @()
try {
    & $ReidPython $ReidControl $Action --node C1
    if ($LASTEXITCODE -ne 0) { $ReidFailures += 'C1' }
} catch {
    Write-Warning "C1: $_"
    $ReidFailures += 'C1'
}
foreach ($ReidPeer in @(@{Node='C2'; HostName='10.78.223.1'}, @{Node='C3'; HostName='10.78.223.84'})) {
    try {
        & $ReidSsh -o BatchMode=yes -o ConnectTimeout=10 "sub@$($ReidPeer.HostName)" "cd /home/sub/reid && .venv/bin/python scripts/lan_node.py $Action --node $($ReidPeer.Node)"
        if ($LASTEXITCODE -ne 0) { $ReidFailures += $ReidPeer.Node }
    } catch {
        Write-Warning "$($ReidPeer.Node): $_"
        $ReidFailures += $ReidPeer.Node
    }
}
if ($ReidFailures.Count -gt 0) {
    throw "Action '$Action' failed for: $($ReidFailures -join ', '). All hosts were attempted; inspect their output above."
}
