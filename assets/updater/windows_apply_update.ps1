param(
    [Parameter(Mandatory = $true)][string]$Archive,
    [Parameter(Mandatory = $true)][string]$InstallRoot,
    [Parameter(Mandatory = $true)][string]$Candidate,
    [Parameter(Mandatory = $true)][string]$RestartTarget,
    [Parameter(Mandatory = $true)][string]$BackupRoot,
    [Parameter(Mandatory = $true)][string]$WorkRoot,
    [Parameter(Mandatory = $false)][string]$SingleInstanceLock = ""
)

$ErrorActionPreference = "Stop"
$ErrorLog = Join-Path (Split-Path -LiteralPath $Archive -Parent) "apply-update-error.log"

function Restore-Backup {
    if ((Test-Path -LiteralPath $BackupRoot) -and -not (Test-Path -LiteralPath $InstallRoot)) {
        Rename-Item -LiteralPath $BackupRoot -NewName (Split-Path -Leaf $InstallRoot)
    }
}

try {
    if (-not (Test-Path -LiteralPath $Candidate)) {
        throw "Could not find the extracted Wisp app folder."
    }
    if (Test-Path -LiteralPath $BackupRoot) {
        Remove-Item -LiteralPath $BackupRoot -Recurse -Force
    }
    Rename-Item -LiteralPath $InstallRoot -NewName (Split-Path -Leaf $BackupRoot)
    Move-Item -LiteralPath $Candidate -Destination $InstallRoot
    Start-Process -FilePath $RestartTarget -WorkingDirectory (Split-Path -LiteralPath $RestartTarget -Parent)
    Start-Sleep -Seconds 5
    Remove-Item -LiteralPath $BackupRoot -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $WorkRoot -Recurse -Force -ErrorAction SilentlyContinue
    exit 0
} catch {
    Restore-Backup
    $_ | Out-String | Set-Content -LiteralPath $ErrorLog
    exit 1
}
