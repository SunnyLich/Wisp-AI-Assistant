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
$ArchiveParent = [System.IO.Path]::GetDirectoryName($Archive)
$RestartParent = [System.IO.Path]::GetDirectoryName($RestartTarget)
$InstallRootLeaf = [System.IO.Path]::GetFileName($InstallRoot)
$BackupRootLeaf = [System.IO.Path]::GetFileName($BackupRoot)
$ErrorLog = Join-Path $ArchiveParent "apply-update-error.log"

function Restore-Backup {
    if (Test-Path -LiteralPath $BackupRoot) {
        if (Test-Path -LiteralPath $InstallRoot) {
            Remove-Item -LiteralPath $InstallRoot -Recurse -Force
        }
        Rename-Item -LiteralPath $BackupRoot -NewName $InstallRootLeaf
    }
}

try {
    if (-not (Test-Path -LiteralPath $Candidate)) {
        throw "Could not find the extracted Wisp app folder."
    }
    if (Test-Path -LiteralPath $BackupRoot) {
        Remove-Item -LiteralPath $BackupRoot -Recurse -Force
    }
    Rename-Item -LiteralPath $InstallRoot -NewName $BackupRootLeaf
    Move-Item -LiteralPath $Candidate -Destination $InstallRoot
    Start-Process -FilePath $RestartTarget -WorkingDirectory $RestartParent
    Start-Sleep -Seconds 5
    Remove-Item -LiteralPath $BackupRoot -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $WorkRoot -Recurse -Force -ErrorAction SilentlyContinue
    exit 0
} catch {
    Restore-Backup
    $_ | Out-String | Set-Content -LiteralPath $ErrorLog
    exit 1
}
