# Diagnose the speech installation of the released Windows Wisp v0.9.0 build.
#
# This script is intentionally standalone: copy it beside Wisp.exe on the
# affected computer, close Wisp, and run it with Windows PowerShell 5.1+.
# The default model check is offline, so it proves whether the existing
# installation/model cache is complete without repairing it during the test.
[CmdletBinding()]
param(
    [string]$WispExe = "",
    [string]$Model = "",
    [ValidateSet("auto", "cuda", "cpu")]
    [string]$Device = "cuda",
    [ValidateSet("", "int8", "float16", "int8_float16", "float32")]
    [string]$Compute = "",
    [int]$TimeoutSeconds = 90,
    [switch]$SkipModelLoad,
    [switch]$AllowModelDownload,
    [switch]$NoPause
)

$ErrorActionPreference = "Stop"

function Get-EnvFileValues {
    param([string]$Path)

    $values = @{}
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $values
    }
    foreach ($line in Get-Content -LiteralPath $Path -ErrorAction SilentlyContinue) {
        if ($line -notmatch '^\s*([^#][^=]*?)\s*=\s*(.*)\s*$') {
            continue
        }
        $name = $matches[1].Trim()
        $value = $matches[2].Trim()
        if ($value.Length -ge 2 -and (($value[0] -eq '"' -and $value[-1] -eq '"') -or ($value[0] -eq "'" -and $value[-1] -eq "'"))) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        $values[$name] = $value
    }
    return $values
}

function ConvertTo-CommandLineArgument {
    param([AllowEmptyString()][string]$Value)

    if ($Value.Length -gt 0 -and $Value -notmatch '[\s"]') {
        return $Value
    }
    $builder = New-Object System.Text.StringBuilder
    [void]$builder.Append('"')
    $slashes = 0
    foreach ($character in $Value.ToCharArray()) {
        if ($character -eq '\') {
            $slashes += 1
            continue
        }
        if ($character -eq '"') {
            [void]$builder.Append(('\' * (($slashes * 2) + 1)))
            [void]$builder.Append('"')
        } else {
            if ($slashes -gt 0) {
                [void]$builder.Append(('\' * $slashes))
            }
            [void]$builder.Append($character)
        }
        $slashes = 0
    }
    if ($slashes -gt 0) {
        [void]$builder.Append(('\' * ($slashes * 2)))
    }
    [void]$builder.Append('"')
    return $builder.ToString()
}

function Get-TextTail {
    param([string]$Text, [int]$Lines = 160)

    if ([string]::IsNullOrWhiteSpace($Text)) {
        return ""
    }
    $all = @($Text -split "`r?`n")
    if ($all.Count -le $Lines) {
        return ($all -join "`n").Trim()
    }
    return (($all | Select-Object -Last $Lines) -join "`n").Trim()
}

function ConvertFrom-LastJsonLine {
    param([string]$Text)

    $lines = @($Text -split "`r?`n")
    for ($index = $lines.Count - 1; $index -ge 0; $index -= 1) {
        $candidate = $lines[$index].Trim()
        if (-not ($candidate.StartsWith("{") -and $candidate.EndsWith("}"))) {
            continue
        }
        try {
            return ($candidate | ConvertFrom-Json)
        } catch {
            continue
        }
    }
    return $null
}

function Write-NewProbeProgress {
    param(
        [string]$Path,
        [string]$Label,
        [ref]$SeenLines
    )

    if (-not $Path -or -not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return
    }
    try {
        $lines = @(Get-Content -LiteralPath $Path -ErrorAction SilentlyContinue)
        for ($index = [int]$SeenLines.Value; $index -lt $lines.Count; $index += 1) {
            Write-Host "  [$Label] $($lines[$index])"
        }
        $SeenLines.Value = $lines.Count
    } catch {
        # The child can briefly have the file open between breadcrumb writes.
    }
}

function Invoke-WispProbe {
    param(
        [string]$Executable,
        [string[]]$ProbeArguments,
        [int]$Timeout,
        [hashtable]$ChildEnvironment = @{},
        [string]$ProgressLogPath = "",
        [string]$ProgressLabel = "probe"
    )

    $arguments = @("-m", "runtime.workers.optional_deps_probe") + $ProbeArguments
    $info = New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName = $Executable
    $info.Arguments = (($arguments | ForEach-Object { ConvertTo-CommandLineArgument ([string]$_) }) -join " ")
    $info.WorkingDirectory = Split-Path -Parent $Executable
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    foreach ($name in $ChildEnvironment.Keys) {
        $info.EnvironmentVariables[[string]$name] = [string]$ChildEnvironment[$name]
    }

    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $info
    $startedAt = Get-Date
    [void]$process.Start()
    $stdoutTask = $process.StandardOutput.ReadToEndAsync()
    $stderrTask = $process.StandardError.ReadToEndAsync()
    $deadline = $startedAt.AddSeconds([Math]::Max(1, $Timeout))
    $nextHeartbeat = $startedAt.AddSeconds(10)
    $seenProgressLines = 0
    while (-not $process.HasExited -and (Get-Date) -lt $deadline) {
        [void]$process.WaitForExit(1000)
        Write-NewProbeProgress -Path $ProgressLogPath -Label $ProgressLabel -SeenLines ([ref]$seenProgressLines)
        $now = Get-Date
        if ($ProgressLogPath -and $now -ge $nextHeartbeat -and -not $process.HasExited) {
            $elapsed = [int]($now - $startedAt).TotalSeconds
            Write-Host "  [$ProgressLabel] still running after ${elapsed}s (timeout: ${Timeout}s)"
            $nextHeartbeat = $now.AddSeconds(10)
        }
    }
    $finished = $process.HasExited
    if (-not $finished) {
        try { $process.Kill() } catch {}
        try { $process.WaitForExit() } catch {}
    } else {
        $process.WaitForExit()
    }
    Write-NewProbeProgress -Path $ProgressLogPath -Label $ProgressLabel -SeenLines ([ref]$seenProgressLines)
    $stdout = $stdoutTask.Result
    $stderr = $stderrTask.Result
    $exitCode = $null
    if ($finished) {
        $exitCode = $process.ExitCode
    }
    return [ordered]@{
        command = "$Executable $($info.Arguments)"
        completed = [bool]$finished
        timed_out = -not [bool]$finished
        exit_code = $exitCode
        elapsed_seconds = [Math]::Round(((Get-Date) - $startedAt).TotalSeconds, 1)
        result = ConvertFrom-LastJsonLine $stdout
        stdout_tail = Get-TextTail $stdout
        stderr_tail = Get-TextTail $stderr
    }
}

function Read-JsonFile {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $null
    }
    try {
        return (Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json)
    } catch {
        return [ordered]@{ error = "$($_.Exception.GetType().Name): $($_.Exception.Message)" }
    }
}

function Get-DistInfoPackages {
    param([string]$Root)

    $wanted = @(
        "faster-whisper", "ctranslate2", "av", "onnxruntime", "huggingface-hub",
        "tokenizers", "protobuf", "kokoro", "torch", "misaki", "spacy", "soundfile"
    )
    $packages = @()
    if (-not (Test-Path -LiteralPath $Root -PathType Container)) {
        return $packages
    }
    foreach ($directory in Get-ChildItem -LiteralPath $Root -Directory -Filter "*.dist-info" -ErrorAction SilentlyContinue) {
        $metadataPath = Join-Path $directory.FullName "METADATA"
        $name = ""
        $version = ""
        if (Test-Path -LiteralPath $metadataPath -PathType Leaf) {
            foreach ($line in Get-Content -LiteralPath $metadataPath -TotalCount 30 -ErrorAction SilentlyContinue) {
                if (-not $name -and $line -match '^Name:\s*(.+)$') { $name = $matches[1].Trim() }
                if (-not $version -and $line -match '^Version:\s*(.+)$') { $version = $matches[1].Trim() }
            }
        }
        $canonical = $name.ToLowerInvariant().Replace("_", "-")
        if ($wanted -contains $canonical) {
            $packages += [ordered]@{ name = $name; version = $version; metadata = $directory.FullName }
        }
    }
    return @($packages | Sort-Object name, version)
}

function Find-NvidiaSmi {
    $command = Get-Command "nvidia-smi.exe" -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    $candidate = Join-Path $env:ProgramFiles "NVIDIA Corporation\NVSMI\nvidia-smi.exe"
    if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
    return ""
}

function Get-NvidiaStatus {
    $path = Find-NvidiaSmi
    if (-not $path) {
        return [ordered]@{ available = $false; path = ""; exit_code = $null; rows = @(); error = "nvidia-smi.exe was not found." }
    }
    try {
        $output = & $path "--query-gpu=name,driver_version,memory.total,memory.free" "--format=csv,noheader,nounits" 2>&1
        $exitCode = $LASTEXITCODE
        $rows = @()
        if ($exitCode -eq 0) {
            foreach ($line in $output) {
                $parts = @(([string]$line).Split(",") | ForEach-Object { $_.Trim() })
                if ($parts.Count -ge 4) {
                    $rows += [ordered]@{
                        name = $parts[0]
                        driver_version = $parts[1]
                        memory_total_mib = $parts[2]
                        memory_free_mib = $parts[3]
                    }
                }
            }
        }
        return [ordered]@{
            available = ($exitCode -eq 0 -and $rows.Count -gt 0)
            path = $path
            exit_code = $exitCode
            rows = $rows
            error = if ($exitCode -eq 0) { "" } else { (($output | Out-String).Trim()) }
        }
    } catch {
        return [ordered]@{ available = $false; path = $path; exit_code = $null; rows = @(); error = "$($_.Exception.GetType().Name): $($_.Exception.Message)" }
    }
}

if (-not ("WispSpeechNativeLoader" -as [type])) {
    Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class WispSpeechNativeLoader {
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    public static extern IntPtr LoadLibraryW(string path);
    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool FreeLibrary(IntPtr module);
}
"@
}

function Test-NativeLibraryLoad {
    param([string]$Path)

    $handle = [WispSpeechNativeLoader]::LoadLibraryW($Path)
    $errorCode = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
    if ($handle -ne [IntPtr]::Zero) {
        [void][WispSpeechNativeLoader]::FreeLibrary($handle)
        return [ordered]@{ path = $Path; loadable = $true; win32_error = 0 }
    }
    return [ordered]@{ path = $Path; loadable = $false; win32_error = $errorCode }
}

function Get-CudaDllStatus {
    param([string]$ExecutableRoot, [string]$OptionalRoot)

    $names = @("cublas64_12.dll", "cudnn64_9.dll", "cudnn_ops64_9.dll", "cudnn_cnn64_9.dll")
    $directories = New-Object System.Collections.Generic.List[string]
    foreach ($candidate in @(
        (Join-Path $ExecutableRoot "_internal"),
        $OptionalRoot,
        (Join-Path $OptionalRoot "torch\lib"),
        (Join-Path $OptionalRoot "nvidia\cublas\bin"),
        (Join-Path $OptionalRoot "nvidia\cudnn\bin"),
        (Join-Path $env:SystemRoot "System32")
    )) {
        if ($candidate -and -not $directories.Contains($candidate)) { $directories.Add($candidate) }
    }
    foreach ($name in @($env:CUDA_PATH) + @($env:CUDA_PATH_V12_0, $env:CUDA_PATH_V12_1, $env:CUDA_PATH_V12_2, $env:CUDA_PATH_V12_3, $env:CUDA_PATH_V12_4, $env:CUDA_PATH_V12_5, $env:CUDA_PATH_V12_6, $env:CUDA_PATH_V12_7, $env:CUDA_PATH_V12_8, $env:CUDA_PATH_V12_9)) {
        if ($name) {
            $candidate = Join-Path $name "bin"
            if (-not $directories.Contains($candidate)) { $directories.Add($candidate) }
        }
    }
    foreach ($directory in ([string]$env:Path).Split(';')) {
        $trimmed = $directory.Trim().Trim('"')
        if ($trimmed -and -not $directories.Contains($trimmed)) { $directories.Add($trimmed) }
    }

    $result = @()
    foreach ($name in $names) {
        $found = @()
        foreach ($directory in $directories) {
            $candidate = Join-Path $directory $name
            if (Test-Path -LiteralPath $candidate -PathType Leaf) {
                $found += (Test-NativeLibraryLoad $candidate)
            }
        }
        $result += [ordered]@{
            name = $name
            basename_load = Test-NativeLibraryLoad $name
            found = @($found)
        }
    }
    return $result
}

function Get-KokoroAssets {
    param([string]$Voice)

    if ($env:HF_HUB_CACHE) {
        $hubRoot = $env:HF_HUB_CACHE
    } elseif ($env:HF_HOME) {
        $hubRoot = Join-Path $env:HF_HOME "hub"
    } else {
        $hubRoot = Join-Path $env:USERPROFILE ".cache\huggingface\hub"
    }
    $modelRoot = Join-Path $hubRoot "models--hexgrad--Kokoro-82M\snapshots"
    $modelFiles = @()
    $configFiles = @()
    $voiceFiles = @()
    if (Test-Path -LiteralPath $modelRoot -PathType Container) {
        $modelFiles = @(Get-ChildItem -LiteralPath $modelRoot -Recurse -File -Filter "kokoro-v1_0.pth" -ErrorAction SilentlyContinue | ForEach-Object {
            [ordered]@{ path = $_.FullName; size = $_.Length; expected_size = 327212226; valid = ($_.Length -eq 327212226) }
        })
        $configFiles = @(Get-ChildItem -LiteralPath $modelRoot -Recurse -File -Filter "config.json" -ErrorAction SilentlyContinue | ForEach-Object {
            [ordered]@{ path = $_.FullName; size = $_.Length; expected_size = 2351; valid = ($_.Length -eq 2351) }
        })
        foreach ($voiceName in @($Voice.Split(',') | ForEach-Object { $_.Trim() } | Where-Object { $_ })) {
            $fileName = if ($voiceName.EndsWith(".pt")) { Split-Path -Leaf $voiceName } else { "$voiceName.pt" }
            $voiceFiles += @(Get-ChildItem -LiteralPath $modelRoot -Recurse -File -Filter $fileName -ErrorAction SilentlyContinue | ForEach-Object {
                [ordered]@{ voice = $voiceName; path = $_.FullName; size = $_.Length; valid = ($_.Length -ge 100000) }
            })
        }
    }
    return [ordered]@{
        hub_root = $hubRoot
        model_root = $modelRoot
        model_files = $modelFiles
        config_files = $configFiles
        voice_files = $voiceFiles
        valid = (($modelFiles | Where-Object { $_.valid }).Count -gt 0 -and ($configFiles | Where-Object { $_.valid }).Count -gt 0 -and ($voiceFiles | Where-Object { $_.valid }).Count -gt 0)
    }
}

function Add-Conclusion {
    param([string]$Area, [string]$Level, [string]$Code, [string]$Summary, [string]$Evidence = "")

    [void]$script:conclusions.Add([ordered]@{
        area = $Area
        level = $Level
        code = $Code
        summary = $Summary
        evidence = $Evidence
    })
}

if (-not $WispExe) {
    foreach ($candidate in @((Join-Path $PSScriptRoot "Wisp.exe"), (Join-Path (Get-Location) "Wisp.exe"))) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            $WispExe = $candidate
            break
        }
    }
}
if (-not $WispExe) {
    $WispExe = Read-Host "Full path to the released Wisp.exe"
}
if (-not (Test-Path -LiteralPath $WispExe -PathType Leaf)) {
    throw "Wisp.exe was not found: $WispExe"
}
$WispExe = (Resolve-Path -LiteralPath $WispExe).Path
$wispRoot = Split-Path -Parent $WispExe
$userRoot = Join-Path $env:APPDATA "Wisp"
$optionalRoot = Join-Path $userRoot "python_packages"
$settingsPath = Join-Path $userRoot ".env"
$settings = Get-EnvFileValues $settingsPath
if (-not $Model) { $Model = if ($settings.ContainsKey("STT_MODEL") -and $settings["STT_MODEL"]) { $settings["STT_MODEL"] } else { "base" } }
if (-not $Compute) { $Compute = if ($settings.ContainsKey("STT_COMPUTE_TYPE") -and $settings["STT_COMPUTE_TYPE"]) { $settings["STT_COMPUTE_TYPE"] } else { "int8" } }
$kokoroVoice = if ($settings.ContainsKey("KOKORO_VOICE") -and $settings["KOKORO_VOICE"]) { $settings["KOKORO_VOICE"] } else { "af_heart" }
$ttsProvider = if ($settings.ContainsKey("TTS_PROVIDER")) { $settings["TTS_PROVIDER"].ToLowerInvariant() } else { "none" }
$kokoroDevice = if ($settings.ContainsKey("KOKORO_DEVICE")) { $settings["KOKORO_DEVICE"].ToLowerInvariant() } else { "auto" }

Write-Host "Testing released Wisp speech installation" -ForegroundColor Cyan
Write-Host "  EXE:     $WispExe"
Write-Host "  Runtime: bundled inside that EXE; no source checkout or separate Python is used"
Write-Host "  STT:     model=$Model device=$Device compute=$Compute"
Write-Host "  Network: $(if ($AllowModelDownload) { 'model download allowed' } else { 'offline; existing installation only' })"
Write-Host ""

$running = @()
foreach ($process in Get-Process -Name "Wisp" -ErrorAction SilentlyContinue) {
    try {
        if ($process.Path -eq $WispExe) { $running += $process.Id }
    } catch {}
}
if ($running.Count -gt 0) {
    $preflightGpu = Get-NvidiaStatus
    Write-Host ""
    Write-Host "PREFLIGHT FAILED: Wisp is still running." -ForegroundColor Red
    Write-Host "Close Wisp and every Wisp.exe process before testing so old models do not consume VRAM."
    Write-Host "Existing Wisp PID(s): $($running -join ', ')"
    if ([bool]$preflightGpu.available) {
        foreach ($gpu in $preflightGpu.rows) {
            Write-Host "GPU before test: $($gpu.name); VRAM $($gpu.memory_free_mib)/$($gpu.memory_total_mib) MiB free"
        }
    }
    Write-Host "Result: WISP_PREFLIGHT_BLOCKED"
    Write-Host "No model probe was started."
    if (-not $NoPause) {
        [void](Read-Host "Press Enter to close this window")
    }
    exit 3
}

$bundleVersion = ""
$bundlePyproject = Join-Path $wispRoot "_internal\pyproject.toml"
if (Test-Path -LiteralPath $bundlePyproject -PathType Leaf) {
    $versionMatch = Select-String -LiteralPath $bundlePyproject -Pattern '^version\s*=\s*"([^"]+)"' | Select-Object -First 1
    if ($versionMatch -and $versionMatch.Matches.Count -gt 0) { $bundleVersion = $versionMatch.Matches[0].Groups[1].Value }
}

$osInfo = Get-CimInstance Win32_OperatingSystem -ErrorAction SilentlyContinue
$computerInfo = [ordered]@{
    computer_name = $env:COMPUTERNAME
    windows_caption = if ($osInfo) { $osInfo.Caption } else { "" }
    windows_version = if ($osInfo) { $osInfo.Version } else { "" }
    windows_build = if ($osInfo) { $osInfo.BuildNumber } else { "" }
    powershell_version = $PSVersionTable.PSVersion.ToString()
    architecture = $env:PROCESSOR_ARCHITECTURE
}

$logRoot = Join-Path $optionalRoot "_logs"
$sttStatusPath = Join-Path $logRoot "stt-install.status.json"
$sttPlanPath = Join-Path $logRoot "stt-install.plan.json"
$sttStatus = Read-JsonFile $sttStatusPath
$sttPlan = Read-JsonFile $sttPlanPath

$sttPlanMismatch = @()
if ($sttPlan) {
    if ($sttPlan.stt_model -and [string]$sttPlan.stt_model -ne $Model) { $sttPlanMismatch += "model plan=$($sttPlan.stt_model) test=$Model" }
    if ($sttPlan.stt_device -and [string]$sttPlan.stt_device -ne $Device) { $sttPlanMismatch += "device plan=$($sttPlan.stt_device) test=$Device" }
    if ($sttPlan.stt_compute_type -and [string]$sttPlan.stt_compute_type -ne $Compute) { $sttPlanMismatch += "compute plan=$($sttPlan.stt_compute_type) test=$Compute" }
}

$nvidia = Get-NvidiaStatus
$cudaDlls = Get-CudaDllStatus $wispRoot $optionalRoot
$unavailableCudaDlls = @(
    $cudaDlls | Where-Object {
        -not [bool]$_.basename_load.loadable -and
        @($_.found | Where-Object { [bool]$_.loadable }).Count -eq 0
    } | ForEach-Object { $_.name }
)
# CTranslate2 4.8 has a pure-CUDA Conv1d implementation, so cuDNN is useful
# diagnostic evidence but is not a universal blocker for Whisper. cuBLAS 12
# remains required and its LoadLibrary check also catches missing dependencies
# such as cublasLt.
$unavailableRequiredCudaDlls = @($unavailableCudaDlls | Where-Object { $_ -eq "cublas64_12.dll" })
$modelBlockedByCudaDlls = $Device -eq "cuda" -and $unavailableRequiredCudaDlls.Count -gt 0
$packages = Get-DistInfoPackages $optionalRoot
$kokoroAssets = Get-KokoroAssets $kokoroVoice

$scratchRoot = Join-Path ([IO.Path]::GetTempPath()) ("wisp-speech-tester-" + [guid]::NewGuid().ToString("N"))
$childEnvironment = @{
    "WISP_OPTIONAL_PACKAGES_DIR" = $optionalRoot
    "WISP_RUN_LOG_DIR" = $scratchRoot
    "PYTHONUNBUFFERED" = "1"
}
if (-not $AllowModelDownload) {
    $childEnvironment["HF_HUB_OFFLINE"] = "1"
    $childEnvironment["TRANSFORMERS_OFFLINE"] = "1"
    $childEnvironment["HF_DATASETS_OFFLINE"] = "1"
}

Write-Host "Running packaged STT import probe..."
$sttRuntimeProbe = Invoke-WispProbe $WispExe @("stt-runtime-status", $optionalRoot) 120 $childEnvironment
$kokoroProbe = $null
$torchProbe = $null
if ($ttsProvider -eq "kokoro") {
    Write-Host "Running packaged Kokoro/Torch import probes..."
    $kokoroProbe = Invoke-WispProbe $WispExe @("kokoro-runtime-status", $optionalRoot) 180 $childEnvironment
    $torchProbe = Invoke-WispProbe $WispExe @("torch-status", $optionalRoot) 180 $childEnvironment
} else {
    Write-Host "Skipping Kokoro/Torch probes because Kokoro is not selected."
}
$sttModelProbe = $null
$sttFloat16VerificationProbe = $null
if ($modelBlockedByCudaDlls) {
    Write-Host "Skipping model warm-up because required CUDA DLLs are unavailable: $($unavailableCudaDlls -join ', ')"
} elseif (-not $SkipModelLoad) {
    $modelTestDeadline = (Get-Date).AddSeconds([Math]::Max(1, $TimeoutSeconds))
    Write-Host "Running real Whisper model construction and warm-up. This can take several minutes..."
    $initialModelLogRoot = Join-Path $scratchRoot "initial-model"
    $initialModelEnvironment = $childEnvironment.Clone()
    $initialModelEnvironment["WISP_RUN_LOG_DIR"] = $initialModelLogRoot
    $sttModelProbe = Invoke-WispProbe `
        -Executable $WispExe `
        -ProbeArguments @("stt-model-status", $optionalRoot, $Model, $Device, $Compute) `
        -Timeout $TimeoutSeconds `
        -ChildEnvironment $initialModelEnvironment `
        -ProgressLogPath (Join-Path $initialModelLogRoot "stt-debug.log") `
        -ProgressLabel "STT model"
    $initialModelResult = $sttModelProbe.result
    if (
        $initialModelResult -and
        [bool]$initialModelResult.valid -and
        $Device -eq "cuda" -and
        $Compute -ne "float16" -and
        [string]$initialModelResult.device -eq "cuda" -and
        [string]$initialModelResult.compute -eq "float16"
    ) {
        # v0.9.0 swallowed an exception from the second warm-up after its INT8
        # fallback. A separate probe requested as float16 does not take that
        # buggy branch, so its success/failure verifies the fallback for real.
        Write-Host "Wisp fell back to float16; running an independent v0.9-compatible float16 verification..."
        $remainingSeconds = [int][Math]::Floor(($modelTestDeadline - (Get-Date)).TotalSeconds)
        if ($remainingSeconds -gt 0) {
            $float16LogRoot = Join-Path $scratchRoot "float16-verification"
            $float16Environment = $childEnvironment.Clone()
            $float16Environment["WISP_RUN_LOG_DIR"] = $float16LogRoot
            $sttFloat16VerificationProbe = Invoke-WispProbe `
                -Executable $WispExe `
                -ProbeArguments @("stt-model-status", $optionalRoot, $Model, "cuda", "float16") `
                -Timeout $remainingSeconds `
                -ChildEnvironment $float16Environment `
                -ProgressLogPath (Join-Path $float16LogRoot "stt-debug.log") `
                -ProgressLabel "float16 verification"
        } else {
            Write-Host "The three-minute model-test deadline was exhausted before float16 could be verified."
        }
    }
}

# The packaged STT code writes a diagnostic breadcrumb when it warms a model.
# Keep that transient output isolated, then remove only our verified temp child.
$tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\', '/')
$resolvedScratch = [IO.Path]::GetFullPath($scratchRoot).TrimEnd('\', '/')
$expectedPrefix = $tempRoot + [IO.Path]::DirectorySeparatorChar
if (
    $resolvedScratch.StartsWith($expectedPrefix, [StringComparison]::OrdinalIgnoreCase) -and
    (Split-Path -Leaf $resolvedScratch).StartsWith("wisp-speech-tester-", [StringComparison]::OrdinalIgnoreCase)
) {
    Remove-Item -LiteralPath $resolvedScratch -Recurse -Force -ErrorAction SilentlyContinue
}

$script:conclusions = New-Object System.Collections.ArrayList
if (-not $bundleVersion) {
    Add-Conclusion "release" "warning" "VERSION_UNREADABLE" "The bundled release version could not be read." $bundlePyproject
} elseif (-not $bundleVersion.StartsWith("0.9")) {
    Add-Conclusion "release" "failure" "WRONG_RELEASE_VERSION" "The tested executable is not a 0.9 release." "Bundled version: $bundleVersion"
} else {
    Add-Conclusion "release" "pass" "RELEASE_0_9_CONFIRMED" "The tested executable reports release $bundleVersion." $bundlePyproject
}
if ($sttPlanMismatch.Count -gt 0) {
    Add-Conclusion "status" "warning" "STT_STATUS_IS_STALE_FOR_REQUEST" "The saved STT install plan does not match this test request; a green saved status cannot prove the requested backend works." ($sttPlanMismatch -join "; ")
}

$sttRuntime = $sttRuntimeProbe.result
if (-not $sttRuntime) {
    Add-Conclusion "stt" "failure" "PACKAGED_STT_PROBE_FAILED" "The released EXE could not return the packaged STT diagnostic result." ($sttRuntimeProbe.stderr_tail + "`n" + $sttRuntimeProbe.stdout_tail).Trim()
} elseif (-not [bool]$sttRuntime.installed -or -not [bool]$sttRuntime.valid) {
    Add-Conclusion "stt" "failure" "STT_OPTIONAL_INSTALL_INCOMPLETE" "The STT package layer is missing or cannot import inside the released EXE." ([string]$sttRuntime.error)
} else {
    Add-Conclusion "stt" "pass" "STT_RUNTIME_IMPORT_OK" "faster-whisper imports from the released EXE's optional package layer." "version=$($sttRuntime.version); origin=$($sttRuntime.origin)"
}

if ($modelBlockedByCudaDlls) {
    Add-Conclusion `
        "stt-gpu" `
        "failure" `
        "CUDA_RUNTIME_DLLS_UNAVAILABLE" `
        "Required CUDA 12 cuBLAS is missing or unloadable; a model warm-up would only wait on an incomplete GPU runtime. cuDNN results are diagnostic for this CTranslate2 version." `
        ($unavailableRequiredCudaDlls -join ", ")
} elseif ($SkipModelLoad) {
    Add-Conclusion "stt-gpu" "inconclusive" "MODEL_WARMUP_SKIPPED" "GPU STT cannot be concluded because real model construction/warm-up was skipped."
} elseif (-not $sttModelProbe.completed) {
    $timeoutEvidence = ([string]$sttModelProbe.stderr_tail + "`n" + [string]$sttModelProbe.stdout_tail).Trim()
    $timeoutEvidenceLower = $timeoutEvidence.ToLowerInvariant()
    if ($timeoutEvidenceLower -match 'starting cuda warmup' -and $timeoutEvidenceLower -notmatch 'finished cuda warmup|warmup encode succeeded') {
        Add-Conclusion "stt-gpu" "failure" "CUDA_WARMUP_TIMEOUT" "The model was constructed, but its real CUDA encoder warm-up did not finish within $TimeoutSeconds seconds." $timeoutEvidence
    } elseif ($timeoutEvidenceLower -match 'building whispermodel|building whisper model' -and $timeoutEvidenceLower -notmatch 'whispermodel constructed|whisper model constructed') {
        Add-Conclusion "stt-gpu" "failure" "WHISPER_MODEL_CONSTRUCTION_TIMEOUT" "CUDA was detected, but constructing/loading the Whisper model did not finish within $TimeoutSeconds seconds." $timeoutEvidence
    } else {
        Add-Conclusion "stt-gpu" "failure" "MODEL_PROBE_TIMEOUT" "Whisper model construction/warm-up did not finish within $TimeoutSeconds seconds." $timeoutEvidence
    }
} elseif (-not $sttModelProbe.result) {
    Add-Conclusion "stt-gpu" "failure" "PACKAGED_MODEL_PROBE_FAILED" "The released EXE did not return a model diagnostic result." ($sttModelProbe.stderr_tail + "`n" + $sttModelProbe.stdout_tail).Trim()
} else {
    $modelResult = $sttModelProbe.result
    $modelEvidence = (
        (@($modelResult.diagnostics) -join "`n") + "`n" +
        [string]$modelResult.error + "`n" +
        [string]$sttModelProbe.stderr_tail + "`n" +
        [string]$sttModelProbe.stdout_tail
    ).Trim()
    $modelEvidenceLower = $modelEvidence.ToLowerInvariant()
    if ([bool]$modelResult.valid) {
        if ($Device -eq "cuda" -and [string]$modelResult.device -ne "cuda") {
            Add-Conclusion "stt-gpu" "failure" "CUDA_NOT_VERIFIED_CPU_FALLBACK" "CUDA was requested, but the released backend resolved to CPU." $modelEvidence
        } elseif ([string]$modelResult.device -eq "cuda" -and [string]$modelResult.compute -eq "float16" -and $Compute -ne "float16") {
            $verificationResult = if ($sttFloat16VerificationProbe) { $sttFloat16VerificationProbe.result } else { $null }
            $verificationEvidence = if ($sttFloat16VerificationProbe) {
                (
                    (@($verificationResult.diagnostics) -join "`n") + "`n" +
                    [string]$verificationResult.error + "`n" +
                    [string]$sttFloat16VerificationProbe.stderr_tail + "`n" +
                    [string]$sttFloat16VerificationProbe.stdout_tail
                ).Trim()
            } else {
                "The independent float16 verification probe did not run."
            }
            if (
                $sttFloat16VerificationProbe -and
                $sttFloat16VerificationProbe.completed -and
                $verificationResult -and
                [bool]$verificationResult.valid -and
                [string]$verificationResult.device -eq "cuda" -and
                [string]$verificationResult.compute -eq "float16"
            ) {
                Add-Conclusion "stt-gpu" "warning" "GPU_WORKS_WITH_VERIFIED_FLOAT16_FALLBACK" "INT8 did not remain active, but a separate v0.9-compatible probe verified CUDA float16 construction and warm-up." ($modelEvidence + "`nIndependent float16 verification:`n" + $verificationEvidence)
            } else {
                Add-Conclusion "stt-gpu" "failure" "FLOAT16_FALLBACK_FAILED_VERIFICATION" "Wisp v0.9 reported an INT8-to-float16 fallback, but an independent float16 construction/warm-up did not succeed." ($modelEvidence + "`nIndependent float16 verification:`n" + $verificationEvidence)
            }
        } elseif ([string]$modelResult.device -eq "cuda" -and [string]$modelResult.compute -ne $Compute) {
            Add-Conclusion "stt-gpu" "warning" "GPU_WORKS_WITH_COMPUTE_FALLBACK" "GPU STT works, but not with the requested compute type; Wisp used $($modelResult.compute) instead of $Compute." $modelEvidence
        } elseif ([string]$modelResult.device -eq "cuda") {
            Add-Conclusion "stt-gpu" "pass" "GPU_STT_WARMUP_OK" "The released EXE constructed and warmed Whisper on CUDA using $($modelResult.compute)." $modelEvidence
        } else {
            Add-Conclusion "stt-gpu" "inconclusive" "CPU_STT_ONLY_CONFIRMED" "The model works on CPU; GPU was not exercised." $modelEvidence
        }
    } elseif ($modelEvidenceLower -match 'out of memory|memory allocation|alloc_failed') {
        Add-Conclusion "stt-gpu" "failure" "GPU_VRAM_EXHAUSTED" "GPU STT failed because CUDA ran out of available VRAM." $modelEvidence
    } elseif ($modelEvidenceLower -match 'cublas64_12|cublas.*not found|cublas.*cannot be loaded') {
        Add-Conclusion "stt-gpu" "failure" "CUBLAS12_MISSING_OR_UNLOADABLE" "CUDA 12 cuBLAS is missing, unloadable, or shadowed by another DLL." $modelEvidence
    } elseif ($modelEvidenceLower -match 'cudnn.*not found|cudnn.*cannot|cudnn.*symbol') {
        Add-Conclusion "stt-gpu" "failure" "CUDNN_MISSING_OR_INCOMPATIBLE" "cuDNN is missing or incompatible with the CTranslate2 runtime." $modelEvidence
    } elseif ($modelEvidenceLower -match 'driver version is insufficient|insufficient_driver') {
        Add-Conclusion "stt-gpu" "failure" "NVIDIA_DRIVER_TOO_OLD" "The NVIDIA driver is too old for the requested CUDA runtime." $modelEvidence
    } elseif ($modelEvidenceLower -match 'cublas_status_not_supported') {
        Add-Conclusion "stt-gpu" "failure" "INT8_CUBLAS_OPERATION_UNSUPPORTED" "The GPU/cuBLAS runtime rejected the INT8 operation, and the fallback did not complete successfully." $modelEvidence
    } elseif (-not $AllowModelDownload -and $modelEvidenceLower -match 'offline|local cache|not found in the cached files|couldn.t connect|could not locate') {
        Add-Conclusion "stt-model" "failure" "WHISPER_MODEL_NOT_INSTALLED" "The selected Whisper model is not complete in the existing cache." $modelEvidence
    } else {
        Add-Conclusion "stt-gpu" "failure" "GPU_MODEL_LOAD_OR_WARMUP_FAILED" "The released EXE imported STT but failed model construction/warm-up." $modelEvidence
    }
}

$kokoroRuntime = $kokoroProbe.result
$torchRuntime = $torchProbe.result
if ($ttsProvider -eq "kokoro") {
    if (-not $kokoroRuntime -or -not [bool]$kokoroRuntime.installed -or -not [bool]$kokoroRuntime.valid) {
        Add-Conclusion "tts" "failure" "KOKORO_INSTALL_INCOMPLETE" "Kokoro is selected but missing or cannot import in the released EXE." $(if ($kokoroRuntime) { [string]$kokoroRuntime.error } else { $kokoroProbe.stderr_tail })
    } elseif (-not $torchRuntime -or -not [bool]$torchRuntime.installed -or -not [bool]$torchRuntime.valid) {
        Add-Conclusion "tts" "failure" "KOKORO_TORCH_INCOMPLETE" "Kokoro imports, but its Torch runtime is missing or invalid." $(if ($torchRuntime) { [string]$torchRuntime.error } else { $torchProbe.stderr_tail })
    } elseif ($kokoroDevice -eq "cuda" -and -not [bool]$torchRuntime.cuda_available) {
        Add-Conclusion "tts" "failure" "KOKORO_CUDA_UNAVAILABLE" "Kokoro is configured for CUDA, but Torch cannot use CUDA." "torch=$($torchRuntime.version); torch_cuda=$($torchRuntime.cuda_version); error=$($torchRuntime.error)"
    } elseif (-not [bool]$kokoroAssets.valid) {
        Add-Conclusion "tts" "failure" "KOKORO_ASSETS_INCOMPLETE" "Kokoro packages import, but its model/configured voice assets are missing or damaged." $kokoroAssets.model_root
    } else {
        Add-Conclusion "tts" "pass" "KOKORO_INSTALL_PRESENT" "Kokoro, Torch, and the configured local assets are present." "torch=$($torchRuntime.version); cuda=$($torchRuntime.cuda_available); voice=$kokoroVoice"
    }
} else {
    Add-Conclusion "tts" "info" "KOKORO_NOT_SELECTED" "Kokoro installation completeness is not required because TTS_PROVIDER is '$ttsProvider'."
}

if (-not [bool]$nvidia.available) {
    Add-Conclusion "gpu" "failure" "NVIDIA_SMI_UNAVAILABLE" "The NVIDIA driver utility could not report a GPU." $nvidia.error
}

$failureCount = @($script:conclusions | Where-Object { $_.level -eq "failure" }).Count
$inconclusiveCount = @($script:conclusions | Where-Object { $_.level -eq "inconclusive" }).Count
$fallbackCount = @($script:conclusions | Where-Object { $_.code -eq "GPU_WORKS_WITH_COMPUTE_FALLBACK" }).Count
$overall = if ($failureCount -gt 0) { "FAIL" } elseif ($inconclusiveCount -gt 0) { "INCONCLUSIVE" } elseif ($fallbackCount -gt 0) { "PASS_WITH_FALLBACK" } else { "PASS" }

Write-Host ""
$color = if ($overall -eq "PASS") { "Green" } elseif ($overall -eq "PASS_WITH_FALLBACK") { "Yellow" } else { "Red" }
Write-Host "OVERALL: $overall" -ForegroundColor $color
Write-Host "Release: $bundleVersion"
Write-Host "Windows: $($computerInfo.windows_caption) $($computerInfo.windows_version) build $($computerInfo.windows_build)"
if ([bool]$nvidia.available) {
    foreach ($gpu in $nvidia.rows) {
        Write-Host "GPU: $($gpu.name); driver $($gpu.driver_version); VRAM $($gpu.memory_free_mib)/$($gpu.memory_total_mib) MiB free"
    }
} else {
    Write-Host "GPU: nvidia-smi unavailable - $($nvidia.error)"
}
Write-Host "STT request: model=$Model device=$Device compute=$Compute offline=$(-not [bool]$AllowModelDownload)"
Write-Host "Configured TTS: provider=$ttsProvider device=$kokoroDevice voice=$kokoroVoice"

if ($packages.Count -gt 0) {
    Write-Host ""
    Write-Host "Optional speech packages:"
    foreach ($package in $packages) {
        Write-Host "  $($package.name) $($package.version)"
    }
} else {
    Write-Host ""
    Write-Host "Optional speech packages: none found in $optionalRoot"
}

Write-Host ""
Write-Host "CUDA DLL loader checks:"
foreach ($dll in $cudaDlls) {
    $basenameState = if ([bool]$dll.basename_load.loadable) { "loadable by name" } else { "not loadable by name (Win32 $($dll.basename_load.win32_error))" }
    Write-Host "  $($dll.name): $basenameState"
    foreach ($foundDll in $dll.found) {
        $pathState = if ([bool]$foundDll.loadable) { "loadable" } else { "not loadable (Win32 $($foundDll.win32_error))" }
        Write-Host "    $pathState - $($foundDll.path)"
    }
}

if ($sttStatus) {
    Write-Host ""
    Write-Host "Saved STT installer status: ok=$($sttStatus.ok); $($sttStatus.message)"
    if ($sttPlanMismatch.Count -gt 0) {
        Write-Host "Saved status mismatch: $($sttPlanMismatch -join '; ')"
    }
}

Write-Host ""
Write-Host "Conclusions:"
foreach ($item in $script:conclusions) {
    Write-Host "[$($item.level.ToUpperInvariant())] $($item.code): $($item.summary)"
    if ($item.evidence) {
        foreach ($line in @(([string]$item.evidence) -split "`r?`n")) {
            Write-Host "  $line"
        }
    }
}
Write-Host ""
Write-Host "No report files were created by this tester."

if (-not $NoPause) {
    [void](Read-Host "Press Enter to close this window")
}

if ($overall -eq "PASS" -or $overall -eq "PASS_WITH_FALLBACK") { exit 0 }
if ($overall -eq "INCONCLUSIVE") { exit 2 }
exit 1
