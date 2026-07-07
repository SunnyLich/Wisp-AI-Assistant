"""Tests for pip install recovery around missing package RECORD metadata."""

from __future__ import annotations

from types import SimpleNamespace


def test_ensure_pip_available_bootstraps_missing_pip(monkeypatch):
    """A Python without pip should be repaired with ensurepip before installing."""
    from scripts import pip_recover_install

    calls: list[list[str]] = []
    pip_checks = iter([1, 0])  # missing before ensurepip, present after

    def fake_run(command, **_kwargs):
        calls.append(command)
        if command[2:] == ["pip", "--version"]:
            return SimpleNamespace(returncode=next(pip_checks))
        if command[2:] == ["ensurepip", "--upgrade"]:
            return SimpleNamespace(returncode=0)
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(pip_recover_install.sys, "executable", "python")
    monkeypatch.setattr(pip_recover_install.subprocess, "run", fake_run)

    assert pip_recover_install._ensure_pip_available() == 0
    assert calls == [
        ["python", "-m", "pip", "--version"],
        ["python", "-m", "ensurepip", "--upgrade"],
        ["python", "-m", "pip", "--version"],
    ]


def test_main_aborts_when_pip_cannot_be_provisioned(monkeypatch):
    """main() must run the pip preflight and stop before any install attempt."""
    from scripts import pip_recover_install

    monkeypatch.setattr(pip_recover_install, "_ensure_pip_available", lambda: 7)
    monkeypatch.setattr(
        pip_recover_install,
        "_run_pip_install",
        lambda _args: (_ for _ in ()).throw(AssertionError("install ran without pip")),
    )

    assert pip_recover_install.main(["tqdm==4.68.2"]) == 7


def test_recover_install_repairs_missing_record_package_from_lock(monkeypatch, tmp_path):
    from scripts import pip_recover_install

    requirements = tmp_path / "requirements.lock"
    requirements.write_text("tqdm==4.68.2\n", encoding="utf-8")
    commands: list[list[str]] = []
    responses = [
        (
            1,
            [
                "error: uninstall-no-record-file\n",
                "\n",
                "x Cannot uninstall tqdm None\n",
                "hint: You might be able to recover from this via: pip install --ignore-installed --no-deps tqdm==4.68.2\n",
            ],
        ),
        (0, ["Successfully installed tqdm-4.68.2\n"]),
        (0, ["Successfully installed requirements\n"]),
    ]

    class FakeProcess:
        def __init__(self, command, **_kwargs):
            commands.append(command)
            self.returncode, lines = responses.pop(0)
            self.stdout = iter(lines)

        def wait(self):
            return self.returncode

    monkeypatch.setattr(pip_recover_install.sys, "executable", "python")
    monkeypatch.setattr(pip_recover_install, "_ensure_pip_available", lambda: 0)
    monkeypatch.setattr(pip_recover_install.subprocess, "Popen", FakeProcess)

    assert pip_recover_install.main(["-r", str(requirements)]) == 0
    assert commands == [
        ["python", "-m", "pip", "install", "-r", str(requirements)],
        ["python", "-m", "pip", "install", "--ignore-installed", "--no-deps", "tqdm==4.68.2"],
        ["python", "-m", "pip", "install", "-r", str(requirements)],
    ]


def test_recover_install_repairs_multiple_missing_record_packages(monkeypatch, tmp_path):
    from scripts import pip_recover_install

    requirements = tmp_path / "requirements.lock"
    requirements.write_text("tqdm==4.68.2\nprotobuf==6.33.2\n", encoding="utf-8")
    commands: list[list[str]] = []
    responses = [
        (
            1,
            [
                "error: uninstall-no-record-file\n",
                "x Cannot uninstall tqdm None\n",
            ],
        ),
        (0, ["Successfully installed tqdm-4.68.2\n"]),
        (
            1,
            [
                "error: uninstall-no-record-file\n",
                "x Cannot uninstall protobuf None\n",
            ],
        ),
        (0, ["Successfully installed protobuf-6.33.2\n"]),
        (0, ["Successfully installed requirements\n"]),
    ]

    class FakeProcess:
        def __init__(self, command, **_kwargs):
            commands.append(command)
            self.returncode, lines = responses.pop(0)
            self.stdout = iter(lines)

        def wait(self):
            return self.returncode

    monkeypatch.setattr(pip_recover_install.sys, "executable", "python")
    monkeypatch.setattr(pip_recover_install, "_ensure_pip_available", lambda: 0)
    monkeypatch.setattr(pip_recover_install.subprocess, "Popen", FakeProcess)

    assert pip_recover_install.main(["-r", str(requirements)]) == 0
    assert commands == [
        ["python", "-m", "pip", "install", "-r", str(requirements)],
        ["python", "-m", "pip", "install", "--ignore-installed", "--no-deps", "tqdm==4.68.2"],
        ["python", "-m", "pip", "install", "-r", str(requirements)],
        ["python", "-m", "pip", "install", "--ignore-installed", "--no-deps", "protobuf==6.33.2"],
        ["python", "-m", "pip", "install", "-r", str(requirements)],
    ]


def test_recover_install_removes_only_broken_metadata_before_repair(monkeypatch, tmp_path):
    from scripts import pip_recover_install

    site_packages = tmp_path / "site-packages"
    broken_metadata = site_packages / "protobuf-0.dist-info"
    healthy_metadata = site_packages / "protobuf-6.33.2.dist-info"
    broken_metadata.mkdir(parents=True)
    healthy_metadata.mkdir()
    (healthy_metadata / "RECORD").write_text("protobuf/__init__.py,,\n", encoding="utf-8")
    requirements = tmp_path / "requirements.lock"
    requirements.write_text("protobuf==6.33.2\n", encoding="utf-8")
    commands: list[list[str]] = []

    class FakeProcess:
        def __init__(self, command, **_kwargs):
            commands.append(command)
            is_recovery = "--ignore-installed" in command
            if is_recovery:
                self.returncode = 0
                self.stdout = iter(["Successfully installed protobuf-6.33.2\n"])
            elif broken_metadata.exists():
                self.returncode = 1
                self.stdout = iter([
                    "error: uninstall-no-record-file\n",
                    "x Cannot uninstall protobuf None\n",
                ])
            else:
                self.returncode = 0
                self.stdout = iter(["Successfully installed requirements\n"])

        def wait(self):
            return self.returncode

    monkeypatch.setattr(pip_recover_install.sys, "executable", "python")
    monkeypatch.setattr(pip_recover_install.sys, "path", [str(site_packages)])
    monkeypatch.setattr(pip_recover_install.sysconfig, "get_path", lambda _name: "")
    monkeypatch.setattr(pip_recover_install, "_ensure_pip_available", lambda: 0)
    monkeypatch.setattr(pip_recover_install.subprocess, "Popen", FakeProcess)

    assert pip_recover_install.main(["-r", str(requirements)]) == 0
    assert not broken_metadata.exists()
    assert healthy_metadata.exists()
    assert commands == [
        ["python", "-m", "pip", "install", "-r", str(requirements)],
        ["python", "-m", "pip", "install", "--ignore-installed", "--no-deps", "protobuf==6.33.2"],
        ["python", "-m", "pip", "install", "-r", str(requirements)],
    ]


def test_recover_install_does_not_recover_unrelated_pip_failures(monkeypatch):
    from scripts import pip_recover_install

    commands: list[list[str]] = []

    class FakeProcess:
        def __init__(self, command, **_kwargs):
            commands.append(command)
            self.stdout = iter(["ERROR: Could not find a version that satisfies the requirement missing\n"])

        def wait(self):
            return 1

    monkeypatch.setattr(pip_recover_install.sys, "executable", "python")
    monkeypatch.setattr(pip_recover_install, "_ensure_pip_available", lambda: 0)
    monkeypatch.setattr(pip_recover_install.subprocess, "Popen", FakeProcess)

    assert pip_recover_install.main(["missing==1"]) == 1
    assert commands == [["python", "-m", "pip", "install", "missing==1"]]
