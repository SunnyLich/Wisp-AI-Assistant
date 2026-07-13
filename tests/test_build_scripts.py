import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class BuildScriptTests(unittest.TestCase):
    def test_posix_entrypoints_are_executable_in_git(self) -> None:
        scripts = [
            "Start Wisp.sh",
            "Start Wisp Debug.sh",
            "Start Wisp.command",
            "Start Wisp Debug.command",
            "scripts/compile_dependency_locks.sh",
            "scripts/compile_macos_lock.sh",
            "scripts/run_macos_tests.command",
            "scripts/setup_dev.sh",
            "tools/build_exe.sh",
            "tools/build_macos_app.sh",
        ]

        result = subprocess.run(
            ["git", "ls-files", "--stage", *scripts],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        modes = {
            line.split(maxsplit=3)[3]: line.split(maxsplit=1)[0]
            for line in result.stdout.splitlines()
            if line.strip()
        }

        self.assertEqual(set(modes), set(scripts))
        self.assertTrue(all(mode == "100755" for mode in modes.values()))

    def test_windows_build_accepts_python_minor_or_patch_target(self) -> None:
        script = (ROOT / "tools" / "build_exe.ps1").read_text(encoding="utf-8")

        self.assertIn('$ExpectedPython = ""', script)
        self.assertNotIn('$ExpectedPython = "3.12.13"', script)
        self.assertIn(".python-version is required and must contain a Python version like 3.12 or 3.12.13", script)
        self.assertIn("$ExpectedPython -notmatch '^\\d+\\.\\d+(\\.\\d+)?$'", script)
        self.assertIn(".python-version must contain a Python version like 3.12 or 3.12.13", script)
        self.assertIn("function Get-PythonVersion", script)
        self.assertIn("sys.version_info[2]", script)
        self.assertIn("$ActualVersion -ne $ExpectedPython", script)
        self.assertIn('StartsWith("$ExpectedPython.")', script)

    def test_posix_build_accepts_python_minor_or_patch_target(self) -> None:
        script = (ROOT / "tools" / "build_exe.sh").read_text(encoding="utf-8")

        self.assertIn('if [ ! -s "$ROOT/.python-version" ]; then', script)
        self.assertIn('WANT="$(tr -d', script)
        self.assertNotIn('printf "3.12.13"', script)
        self.assertIn(".python-version is required and must contain a Python version like 3.12 or 3.12.13", script)
        self.assertIn('[[ ! "$WANT" =~ ^[0-9]+\\.[0-9]+(\\.[0-9]+)?$ ]]', script)
        self.assertIn(".python-version must contain a Python version like 3.12 or 3.12.13", script)
        self.assertIn("python_version()", script)
        self.assertIn("python_matches_want()", script)
        self.assertIn("sys.version_info[2]", script)
        self.assertIn('HAVE_VERSION="$(python_version "$PYTHON")"', script)
        self.assertIn('if ! python_matches_want "$PYTHON"; then', script)

    def test_linux_build_opens_terminal_for_gui_launches(self) -> None:
        linux = (ROOT / "tools" / "build_exe.sh").read_text(encoding="utf-8")

        self.assertIn("relaunch_in_terminal()", linux)
        self.assertIn("--relaunched-in-terminal", linux)
        self.assertIn("[[ ! -t 0 && ! -t 1 && ! -t 2 ]]", linux)
        self.assertIn('[[ -n "${DISPLAY:-}" || -n "${WAYLAND_DISPLAY:-}" ]]', linux)
        self.assertIn('[[ -z "${CI:-}" ]]', linux)
        self.assertIn('[[ "$(uname -s)" == "Linux" ]]', linux)
        for emulator in ("x-terminal-emulator", "gnome-terminal", "konsole", "xterm"):
            self.assertIn(emulator, linux)
        self.assertIn("trap report_result_and_hold EXIT", linux)
        self.assertIn("Press Enter to close this window", linux)
        # The relaunch must run before the option parser consumes "$@".
        self.assertLess(
            linux.index("relaunch_in_terminal"),
            linux.index("while [[ $# -gt 0 ]]"),
        )

        docs = (ROOT / "docs" / "BUILDING_EXE.md").read_text(encoding="utf-8")
        self.assertIn("opens a terminal window automatically", docs)

    def test_macos_build_script_uses_macos_spec_and_lockfile(self) -> None:
        script = (ROOT / "tools" / "build_macos_app.sh").read_text(encoding="utf-8")

        self.assertIn('SPEC_NAME="WispMac.spec"', script)
        self.assertIn('MACOS_LOCK_FILE="$ROOT/requirements/requirements-macos.lock"', script)
        self.assertIn('if [[ "$(uname -s)" != "Darwin" ]]', script)
        self.assertIn('require_file "$ICON_ICNS_PATH" "assets/app.icns"', script)
        self.assertIn('require_file "$ICON_PNG_PATH" "assets/app.png"', script)
        self.assertIn('"$PYTHON" -m PyInstaller --noconfirm "$SPEC"', script)
        self.assertIn('Built app bundle: $ROOT/dist/$APP_NAME.app', script)

    def test_posix_build_scripts_stage_uv_for_packaged_optional_installs(self) -> None:
        for script_name in ("tools/build_exe.sh", "tools/build_macos_app.sh"):
            with self.subTest(script=script_name):
                script = (ROOT / script_name).read_text(encoding="utf-8")
                self.assertIn("find_uv_for_python()", script)
                self.assertIn("install_uv_with_python()", script)
                self.assertIn("stage_portable_uv()", script)
                self.assertIn('portable_uv="$ROOT/tools/uv"', script)
                self.assertIn("Runtime package installs in packaged Wisp require bundled uv.", script)
                self.assertIn('stage_portable_uv "$PYTHON"', script)
                self.assertLess(script.index('stage_portable_uv "$PYTHON"'), script.index('"$PYTHON" -m PyInstaller --noconfirm "$SPEC"'))

    def test_build_scripts_check_dependency_manifests_before_mutating_outputs(self) -> None:
        powershell = (ROOT / "tools" / "build_exe.ps1").read_text(encoding="utf-8")

        self.assertIn("function Require-PackagingFile", powershell)
        self.assertIn("$RequiredBuildFiles = @(", powershell)
        self.assertIn('$RequirementsFile = "requirements/requirements-windows.lock"', powershell)
        self.assertIn('$BuildRequirementsFile = "requirements/requirements-build.lock"', powershell)
        self.assertIn('Path = (Join-Path $Root $RequirementsFile); Name = $RequirementsFile', powershell)
        self.assertIn('Path = (Join-Path $Root $BuildRequirementsFile); Name = $BuildRequirementsFile', powershell)
        self.assertIn("$Name is required for packaging.", powershell)
        self.assertLess(powershell.index("$RequiredBuildFiles = @("), powershell.index("function New-BuildVenv"))
        self.assertLess(powershell.index("$RequiredBuildFiles = @("), powershell.index("Remove-Item -LiteralPath"))

        shell = (ROOT / "tools" / "build_exe.sh").read_text(encoding="utf-8")

        self.assertIn("require_file()", shell)
        self.assertIn('REQUIREMENTS_FILE="$ROOT/requirements/requirements-linux.lock"', shell)
        self.assertIn('BUILD_REQUIREMENTS_FILE="$ROOT/requirements/requirements-build.lock"', shell)
        self.assertIn('require_file "$REQUIREMENTS_FILE" "requirements/requirements-linux.lock"', shell)
        self.assertIn('require_file "$BUILD_REQUIREMENTS_FILE" "requirements/requirements-build.lock"', shell)
        self.assertIn('echo "ERROR: $name is required for packaging."', shell)
        self.assertLess(shell.index('require_file "$BUILD_REQUIREMENTS_FILE" "requirements/requirements-build.lock"'), shell.index('"$CREATE_PYTHON" -m venv'))
        self.assertLess(shell.index('require_file "$BUILD_REQUIREMENTS_FILE" "requirements/requirements-build.lock"'), shell.index("clean_build_outputs"))

    def test_windows_build_warns_loudly_when_elevenlabs_is_skipped(self) -> None:
        powershell = (ROOT / "tools" / "build_exe.ps1").read_text(encoding="utf-8")

        self.assertIn("IMPORTANT: ElevenLabs will not be bundled in this build.", powershell)
        self.assertIn("Settings > Voice > Install ElevenLabs", powershell)
        self.assertIn("Windows long paths enabled", powershell)

    def test_build_scripts_check_packaging_inputs_before_mutating_outputs(self) -> None:
        powershell = (ROOT / "tools" / "build_exe.ps1").read_text(encoding="utf-8")

        required_windows_inputs = [
            'Path = $Spec; Name = "packaging\\$SpecName"',
            'Path = (Join-Path $Root "runtime\\supervisor\\app.py"); Name = "runtime\\supervisor\\app.py"',
            'Path = (Join-Path $Root ".env.example"); Name = ".env.example"',
            'Path = (Join-Path $Root "assets"); Name = "assets"',
            'Path = (Join-Path $Root "ui\\locales"); Name = "ui\\locales"',
            'Require-PackagingFile -Path $IconSourcePng -Name "assets\\doll\\idle.png"',
            'Require-PackagingFile -Path $IconPngPath -Name "assets\\app.png"',
        ]
        for snippet in required_windows_inputs:
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, powershell)

        self.assertIn("function Require-PackagingDirectory", powershell)
        self.assertIn("$Name must contain files for packaging.", powershell)
        first_packaging_check = powershell.index("$RequiredPackagingFiles = @(")
        self.assertLess(first_packaging_check, powershell.index("function New-BuildVenv"))
        self.assertLess(first_packaging_check, powershell.index("Remove-Item -LiteralPath"))
        self.assertLess(first_packaging_check, powershell.index('Invoke-CheckedPython -Python $Python -CommandArgs @("-m", "pip", "install"'))

        shell = (ROOT / "tools" / "build_exe.sh").read_text(encoding="utf-8")

        required_posix_inputs = [
            'require_file "$SPEC" "packaging/$SPEC_NAME"',
            'require_file "$ROOT/runtime/supervisor/app.py" "runtime/supervisor/app.py"',
            'require_file "$ROOT/.env.example" ".env.example"',
            'require_dir "$ROOT/assets" "assets"',
            'require_dir "$ROOT/ui/locales" "ui/locales"',
            'require_file "$ICON_SOURCE_PNG" "assets/doll/idle.png"',
            'require_file "$ICON_PNG_PATH" "assets/app.png"',
        ]
        for snippet in required_posix_inputs:
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, shell)

        self.assertIn("require_dir()", shell)
        self.assertIn('echo "ERROR: $name must contain files for packaging."', shell)
        first_shell_packaging_check = shell.index('require_file "$SPEC" "packaging/$SPEC_NAME"')
        self.assertLess(first_shell_packaging_check, shell.index('"$CREATE_PYTHON" -m venv'))
        self.assertLess(first_shell_packaging_check, shell.index("clean_build_outputs"))
        self.assertLess(first_shell_packaging_check, shell.index('"$PYTHON" -m pip install'))

    def test_build_clean_removes_pytest_temp_roots(self) -> None:
        powershell = (ROOT / "tools" / "build_exe.ps1").read_text(encoding="utf-8")
        linux = (ROOT / "tools" / "build_exe.sh").read_text(encoding="utf-8")
        macos = (ROOT / "tools" / "build_macos_app.sh").read_text(encoding="utf-8")

        self.assertIn("function Clear-BuildOutputs", powershell)
        self.assertIn('".pytest_cache"', powershell)
        self.assertIn('".pytest-tmp"', powershell)
        self.assertIn('".pytest_tmp"', powershell)
        self.assertIn('".tmp_pytest"', powershell)
        self.assertIn('Filter ".pytest-tmp-*"', powershell)
        self.assertIn("Clear-BuildOutputs", powershell)

        for script in (linux, macos):
            with self.subTest(script=script[:32]):
                self.assertIn("clean_build_outputs()", script)
                self.assertIn('"$ROOT/.pytest_cache"', script)
                self.assertIn('"$ROOT/.pytest-tmp"', script)
                self.assertIn('"$ROOT/.pytest_tmp"', script)
                self.assertIn('"$ROOT/.tmp_pytest"', script)
                self.assertIn("find \"$ROOT\" -maxdepth 1 -type d -name '.pytest-tmp-*'", script)
                self.assertIn("clean_build_outputs", script)

    def test_build_docs_describe_python_minor_version_requirement(self) -> None:
        docs = (ROOT / "docs" / "BUILDING_EXE.md").read_text(encoding="utf-8")

        self.assertIn("Python `3.12`", docs)
        self.assertNotIn("exact patch version", docs)

    def test_build_scripts_use_dedicated_build_venv_by_default(self) -> None:
        powershell = (ROOT / "tools" / "build_exe.ps1").read_text(encoding="utf-8")
        linux = (ROOT / "tools" / "build_exe.sh").read_text(encoding="utf-8")
        macos = (ROOT / "tools" / "build_macos_app.sh").read_text(encoding="utf-8")
        docs = (ROOT / "docs" / "BUILDING_EXE.md").read_text(encoding="utf-8")
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

        self.assertIn('[switch]$UseDevVenv', powershell)
        self.assertIn('$VenvName = if ($UseDevVenv) { ".venv" } else { ".venv-build" }', powershell)
        self.assertIn('VENV_DIR="$ROOT/.venv-build"', linux)
        self.assertIn('VENV_DIR="$ROOT/.venv-build"', macos)
        self.assertIn("--use-dev-venv", linux)
        self.assertIn("--use-dev-venv", macos)
        self.assertIn("dedicated `.venv-build` environment", docs)
        self.assertIn(".venv-build/", gitignore)
        self.assertIn(".venv-dev/", gitignore)
        self.assertIn("/tools/uv", gitignore)
        self.assertIn("/tools/uv.exe", gitignore)

    def test_build_scripts_provision_python_with_uv_when_missing(self) -> None:
        powershell = (ROOT / "tools" / "build_exe.ps1").read_text(encoding="utf-8")
        linux = (ROOT / "tools" / "build_exe.sh").read_text(encoding="utf-8")
        macos = (ROOT / "tools" / "build_macos_app.sh").read_text(encoding="utf-8")
        docs = (ROOT / "docs" / "BUILDING_EXE.md").read_text(encoding="utf-8")

        self.assertIn("function Ensure-Uv", powershell)
        self.assertIn("uv not found; installing uv to provision Python $ExpectedPython", powershell)
        self.assertIn("[string]::IsNullOrWhiteSpace($Uv)", powershell)
        self.assertIn("function Install-UvWithPython", powershell)
        self.assertIn('Invoke-CheckedPython -Python $Python -CommandArgs @("-m", "pip", "install", "uv")', powershell)
        self.assertIn('Invoke-CheckedPython -Python $Python -CommandArgs @("-m", "pip", "install", "uv") -StepName "uv Python install" | Out-Host', powershell)
        self.assertIn('"irm https://astral.sh/uv/install.ps1 | iex"', powershell)
        self.assertIn(') | Out-Host', powershell)
        self.assertIn('Invoke-Native "uv build virtual environment creation"', powershell)
        self.assertIn('@("venv", "--seed", "--python", $ExpectedPython, $VenvDir)', powershell)
        self.assertIn("function Ensure-Pip", powershell)
        self.assertIn('Invoke-CheckedPython -Python $Python -CommandArgs @("-m", "ensurepip", "--upgrade")', powershell)
        self.assertIn("ensure_uv()", linux)
        self.assertIn('"$UV" venv --seed --python "$WANT" "$VENV_DIR"', linux)
        self.assertIn("ensure_pip()", linux)
        self.assertIn('"$1" -m ensurepip --upgrade', linux)
        self.assertIn("ensure_uv()", macos)
        self.assertIn('"$UV" venv --seed --python "$WANT" "$VENV_DIR"', macos)
        self.assertIn("ensure_pip()", macos)
        self.assertIn('"$1" -m ensurepip --upgrade', macos)
        self.assertIn("installs/uses `uv` to provision that Python", docs)
        self.assertIn("bootstraps it with `ensurepip`", docs)

    def test_windows_build_stages_uv_for_portable_runtime_installs(self) -> None:
        powershell = (ROOT / "tools" / "build_exe.ps1").read_text(encoding="utf-8")
        docs = (ROOT / "docs" / "BUILDING_EXE.md").read_text(encoding="utf-8")

        self.assertIn("function Stage-PortableUv", powershell)
        self.assertIn("Stage-PortableUv -Python $Python", powershell)
        self.assertIn('Join-Path $ToolsDir "uv.exe"', powershell)
        self.assertIn("$Uv = Install-UvWithPython -Python $Python", powershell)
        self.assertIn("$UvCandidates = @($Uv)", powershell)
        self.assertIn("Test-Path -LiteralPath $_ -PathType Leaf", powershell)
        self.assertIn("if ([string]::IsNullOrWhiteSpace($Uv))", powershell)
        self.assertIn("Copy-Item -LiteralPath $SourcePath -Destination $PortableUv -Force", powershell)
        self.assertLess(powershell.index("Stage-PortableUv"), powershell.index('Invoke-CheckedPython -Python $Python -CommandArgs @("-m", "PyInstaller"'))
        self.assertIn("The Windows build script stages", docs)
        self.assertIn("`uv.exe` into `tools\\uv.exe` before PyInstaller runs", docs)

    def test_specs_bundle_version_metadata_for_updater(self) -> None:
        for spec_name in ("Wisp.spec", "WispLinux.spec", "WispMac.spec"):
            with self.subTest(spec=spec_name):
                spec = (ROOT / "packaging" / spec_name).read_text(encoding="utf-8")
                self.assertIn('pyproject.toml', spec)

    def test_specs_configure_platform_app_icons(self) -> None:
        windows = (ROOT / "packaging" / "Wisp.spec").read_text(encoding="utf-8")
        linux = (ROOT / "packaging" / "WispLinux.spec").read_text(encoding="utf-8")
        macos = (ROOT / "packaging" / "WispMac.spec").read_text(encoding="utf-8")

        self.assertIn('APP_ICON_ICO = ROOT / "assets" / "app.ico"', windows)
        self.assertIn("icon=str(APP_ICON_ICO) if APP_ICON_ICO.exists() else None", windows)
        self.assertIn('APP_ICON_ICO = ROOT / "assets" / "app.ico"', linux)
        self.assertIn("icon=str(APP_ICON_ICO) if APP_ICON_ICO.exists() else None", linux)
        self.assertIn('APP_ICON_ICNS = ROOT / "assets" / "app.icns"', macos)
        self.assertIn("icon=str(APP_ICON_ICNS) if APP_ICON_ICNS.exists() else None", macos)

    def test_specs_bundle_default_addons(self) -> None:
        for spec_name in ("Wisp.spec", "WispLinux.spec", "WispMac.spec"):
            with self.subTest(spec=spec_name):
                spec = (ROOT / "packaging" / spec_name).read_text(encoding="utf-8")
                self.assertIn("BUNDLED_ADDON_DATAS", spec)
                self.assertIn('ROOT / "addons" / "mcp_bridge"', spec)
                self.assertIn('"addons/mcp_bridge"', spec)
                self.assertIn('ROOT / "addons" / "ui_lab"', spec)
                self.assertIn('"addons/ui_lab"', spec)
                self.assertIn("if path.exists()", spec)
                self.assertIn("] + BUNDLED_ADDON_DATAS +", spec)

        docs = (ROOT / "docs" / "BUILDING_EXE.md").read_text(encoding="utf-8")
        self.assertIn("MCP Bridge and UI Lab addons are bundled when present", docs)
        self.assertIn("servers.json", docs)

    def test_specs_skip_missing_default_addon_inputs(self) -> None:
        for spec_name in ("Wisp.spec", "WispLinux.spec", "WispMac.spec"):
            with self.subTest(spec=spec_name):
                spec = (ROOT / "packaging" / spec_name).read_text(encoding="utf-8")
                start = spec.index("BUNDLED_ADDON_DATAS = [")
                end = spec.index("\n\nblock_cipher", start)
                addon_datas_block = spec[start:end]

                with tempfile.TemporaryDirectory() as tmp:
                    temp_root = Path(tmp)
                    namespace = {"ROOT": temp_root}

                    exec(addon_datas_block, namespace)
                    self.assertEqual(namespace["BUNDLED_ADDON_DATAS"], [])

                    (temp_root / "addons" / "mcp_bridge").mkdir(parents=True)
                    exec(addon_datas_block, namespace)
                    self.assertEqual(
                        namespace["BUNDLED_ADDON_DATAS"],
                        [(str(temp_root / "addons" / "mcp_bridge"), "addons/mcp_bridge")],
                    )

    def test_specs_bundle_runtime_worker_modules_for_frozen_module_dispatch(self) -> None:
        for spec_name in ("Wisp.spec", "WispLinux.spec", "WispMac.spec"):
            with self.subTest(spec=spec_name):
                spec = (ROOT / "packaging" / spec_name).read_text(encoding="utf-8")
                self.assertIn("collect_submodules", spec)
                self.assertIn("MODULE_MODE_HIDDENIMPORTS", spec)
                self.assertIn('"core.addon_host"', spec)
                self.assertIn('"scripts.optional_tts_installer"', spec)
                self.assertIn('collect_submodules("runtime.workers")', spec)
                self.assertIn("RUNTIME_WORKER_HIDDENIMPORTS", spec)
                self.assertIn('collect_submodules("wisp_brain")', spec)
                self.assertIn("BRAIN_HIDDENIMPORTS", spec)

    def test_specs_exclude_pip_from_release_bundles(self) -> None:
        """Packaged installs use bundled uv; pip belongs only to build/source environments."""
        for spec_name in ("Wisp.spec", "WispLinux.spec", "WispMac.spec"):
            with self.subTest(spec=spec_name):
                spec = (ROOT / "packaging" / spec_name).read_text(encoding="utf-8")
                self.assertNotIn('collect_submodules("pip")', spec)
                self.assertNotIn("PIP_HIDDENIMPORTS", spec)
                excludes = spec[spec.index("    excludes=[") : spec.index("    ],", spec.index("    excludes=["))]
                self.assertIn('"pip"', excludes)

    def test_specs_bundle_language_tags_data_for_local_tts(self) -> None:
        for spec_name in ("Wisp.spec", "WispLinux.spec", "WispMac.spec"):
            with self.subTest(spec=spec_name):
                spec = (ROOT / "packaging" / spec_name).read_text(encoding="utf-8")
                self.assertIn('collect_all("language_tags")', spec)
                self.assertIn("LANGUAGE_TAGS_DATAS", spec)
                self.assertIn("LANGUAGE_TAGS_BINARIES", spec)
                self.assertIn("LANGUAGE_TAGS_HIDDENIMPORTS", spec)

    def test_specs_bundle_stdlib_needed_by_runtime_installed_audio_packages(self) -> None:
        for spec_name in ("Wisp.spec", "WispLinux.spec", "WispMac.spec"):
            with self.subTest(spec=spec_name):
                spec = (ROOT / "packaging" / spec_name).read_text(encoding="utf-8")
                self.assertIn("OPTIONAL_RUNTIME_HIDDENIMPORTS", spec)
                for module in (
                    "cProfile",
                    "cmath",
                    "filecmp",
                    "huggingface_hub.dataclasses",
                    "pickletools",
                    "pstats",
                    "timeit",
                    "tqdm.contrib.logging",
                ):
                    self.assertIn(f'"{module}"', spec)


if __name__ == "__main__":
    unittest.main()
