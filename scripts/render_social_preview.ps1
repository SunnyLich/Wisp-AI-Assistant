param(
    [string]$Source = (Join-Path $PSScriptRoot "..\assets\social-preview.svg"),
    [string]$Output = (Join-Path $PSScriptRoot "..\assets\social-preview.png")
)

$ErrorActionPreference = "Stop"

$browserCandidates = @(
    "C:\Program Files\Google\Chrome\Application\chrome.exe",
    "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "C:\Program Files\Microsoft\Edge\Application\msedge.exe"
)
$browser = $browserCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $browser) {
    throw "Chrome or Microsoft Edge is required to render the social preview."
}

$sourcePath = (Resolve-Path -LiteralPath $Source).Path
$outputPath = [System.IO.Path]::GetFullPath($Output)
$outputDirectory = Split-Path -Parent $outputPath
if (-not (Test-Path -LiteralPath $outputDirectory)) {
    New-Item -ItemType Directory -Path $outputDirectory | Out-Null
}

$profilePath = Join-Path ([System.IO.Path]::GetTempPath()) ("wisp-social-preview-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $profilePath | Out-Null

try {
    $fileUri = [System.Uri]::new($sourcePath).AbsoluteUri
    $arguments = @(
        "--headless=new",
        "--disable-gpu",
        "--hide-scrollbars",
        "--force-device-scale-factor=1",
        "--window-size=1280,640",
        "--user-data-dir=$profilePath",
        "--screenshot=$outputPath",
        $fileUri
    )

    $process = Start-Process -FilePath $browser -ArgumentList $arguments -Wait -PassThru -WindowStyle Hidden
    if ($process.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $outputPath)) {
        throw "Browser rendering failed with exit code $($process.ExitCode)."
    }
}
finally {
    if (Test-Path -LiteralPath $profilePath) {
        Remove-Item -LiteralPath $profilePath -Recurse -Force
    }
}

Add-Type -AssemblyName System.Drawing
$image = [System.Drawing.Image]::FromFile($outputPath)
try {
    if ($image.Width -ne 1280 -or $image.Height -ne 640) {
        throw "Expected a 1280x640 image, got $($image.Width)x$($image.Height)."
    }
}
finally {
    $image.Dispose()
}

Write-Output "Rendered $outputPath (1280x640)"
