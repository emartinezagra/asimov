$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    $python = Get-Command py -ErrorAction SilentlyContinue
}
if (-not $python) {
    Write-Host "Python 3 not found. Install it from https://python.org before continuing."
    exit 1
}

& $python.Source install_windows.py
