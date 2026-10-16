# Remove only reproducible project clutter; preserve environments and research data.
[CmdletBinding(SupportsShouldProcess)]
param()

$ErrorActionPreference = 'Stop'
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$rootPrefix = $projectRoot.TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
$targets = [System.Collections.Generic.List[string]]::new()

# Never traverse virtual environments, data, results, or directory links.
function Find-PythonCaches([string]$directory) {
    foreach ($item in Get-ChildItem -LiteralPath $directory -Force) {
        if ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) { continue }
        if ($item.PSIsContainer) {
            if ($item.Name -eq '__pycache__') {
                $targets.Add($item.FullName)
            } else {
                Find-PythonCaches $item.FullName
            }
        } elseif ($item.Extension -in '.pyc', '.pyo') {
            $targets.Add($item.FullName)
        }
    }
}

foreach ($name in 'src', 'tests', 'scripts') {
    $directory = Join-Path $projectRoot $name
    $item = Get-Item -LiteralPath $directory
    if (-not ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
        Find-PythonCaches $directory
    }
}

foreach ($item in Get-ChildItem -LiteralPath $projectRoot -Force) {
    if ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) { continue }
    if ($item.PSIsContainer -and ($item.Name -in '__pycache__', '.pytest_cache', '.ruff_cache', '.mypy_cache' -or $item.Name -like 'pytest-cache-files-*')) {
        $targets.Add($item.FullName)
    } elseif (-not $item.PSIsContainer -and ($item.Extension -in '.aux', '.bbl', '.blg', '.log', '.out', '.fls', '.fdb_latexmk' -or $item.Name -like '*.synctex.gz')) {
        $targets.Add($item.FullName)
    }
}

foreach ($name in 'config', 'config/lan') {
    $directory = Join-Path $projectRoot $name
    $item = Get-Item -LiteralPath $directory
    if ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) { continue }
    foreach ($backup in Get-ChildItem -LiteralPath $directory -File -Filter '*.yaml.before-*') {
        if (-not ($backup.Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
            $targets.Add($backup.FullName)
        }
    }
}

foreach ($target in $targets) {
    $resolved = [System.IO.Path]::GetFullPath($target)
    if (-not $resolved.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Cleanup target is outside the project: $resolved"
    }
    if ($PSCmdlet.ShouldProcess($resolved, 'Remove generated project clutter')) {
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}
