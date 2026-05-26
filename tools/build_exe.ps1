param(
    [switch]$Clean,
    [switch]$SkipInstall,
    [switch]$Yes,
    [switch]$UseGlobalPython,
    [switch]$Lite
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$VenvDir = Join-Path $Root ".venv"
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
$SpecName = if ($Lite) { "WispLite.spec" } else { "Wisp.spec" }
$AppName = if ($Lite) { "WispLite" } else { "Wisp" }
$RequirementsFile = if ($Lite) { "requirements-light.txt" } else { "requirements.txt" }
$Spec = Join-Path $Root "packaging\$SpecName"
$DistExe = Join-Path $Root "dist\$AppName\$AppName.exe"
$IconPath = Join-Path $Root "assets\app.ico"
$DollIconPng = Join-Path $Root "assets\doll\idle.png"

Set-Location $Root

function Test-Yes($Value) {
    return $Value -match '^(y|yes)$'
}

if (-not $UseGlobalPython) {
    if (-not (Test-Path $VenvPython)) {
        $CreateVenv = $false
        if ($Yes) {
            $CreateVenv = $true
        } else {
            Write-Host "Project virtual environment not found:"
            Write-Host "  $VenvDir"
            $Answer = Read-Host "Create it now? [y/N]"
            $CreateVenv = Test-Yes $Answer
        }

        if (-not $CreateVenv) {
            throw "Build cancelled: project .venv is required unless you pass -UseGlobalPython."
        }

        if (Get-Command py.exe -ErrorAction SilentlyContinue) {
            & py -3 -m venv $VenvDir
        } else {
            & python -m venv $VenvDir
        }
    }
    $Python = $VenvPython
} else {
    Write-Host "Using global Python because -UseGlobalPython was provided."
    $Python = "python"
}

if (-not (Test-Path $IconPath)) {
    if (-not (Test-Path $DollIconPng)) {
        throw "Cannot create exe icon because the doll source image is missing: $DollIconPng"
    }
    Write-Host "Creating exe icon from doll image: $IconPath"
    & $Python -c "from pathlib import Path; from PIL import Image; src=Path(r'$DollIconPng'); dst=Path(r'$IconPath'); img=Image.open(src).convert('RGBA'); img.save(dst, format='ICO', sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])"
}

if (Test-Path $DistExe) {
    $RunningWisp = Get-Process -Name "Wisp" -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -eq $DistExe }
    if ($RunningWisp) {
        $Pids = ($RunningWisp | ForEach-Object { $_.Id }) -join ", "
        throw "Cannot rebuild while the packaged app is running. Close Wisp.exe first. Running process id(s): $Pids"
    }
}

if ($Clean) {
    Remove-Item -LiteralPath (Join-Path $Root "build") -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath (Join-Path $Root "dist") -Recurse -Force -ErrorAction SilentlyContinue
}

if (-not $SkipInstall) {
    $InstallDeps = $false
    if ($Yes) {
        $InstallDeps = $true
    } else {
        Write-Host "This can install/update Python packages in the selected environment:"
        Write-Host "  $Python"
        $Answer = Read-Host "Install/update dependencies before building? [y/N]"
        $InstallDeps = Test-Yes $Answer
    }

    if ($InstallDeps) {
        & $Python -m pip install --upgrade pip
        & $Python -m pip install -r $RequirementsFile -r requirements-build.txt
    } else {
        Write-Host "Skipping dependency install. Use -Yes to install automatically or -SkipInstall to suppress this prompt."
    }
}

try {
    & $Python -m PyInstaller --version | Out-Null
} catch {
    throw "PyInstaller is not installed in $Python. Run without -SkipInstall, or run: $Python -m pip install -r requirements-build.txt"
}

& $Python -m PyInstaller --noconfirm $Spec
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE."
}

Write-Host ""
Write-Host "Built app folder: $Root\dist\$AppName"
Write-Host "Executable:       $Root\dist\$AppName\$AppName.exe"
