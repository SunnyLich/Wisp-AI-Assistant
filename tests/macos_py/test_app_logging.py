from pathlib import Path

from macos_py.supervisor import app as supervisor_app


def test_prepare_run_log_dir_sets_env_and_latest_pointer(tmp_path, monkeypatch):
    monkeypatch.delenv("WISP_RUN_LOG_DIR", raising=False)
    monkeypatch.setattr(supervisor_app, "repo_root", lambda: tmp_path)

    log_dir = supervisor_app._prepare_run_log_dir()

    assert log_dir.is_dir()
    assert log_dir.parent == tmp_path / "build_logs"
    assert Path(supervisor_app.os.environ["WISP_RUN_LOG_DIR"]) == log_dir
    assert (tmp_path / "build_logs" / "latest_wisp_runtime.txt").read_text(encoding="utf-8") == str(log_dir)


def test_prepare_run_log_dir_respects_existing_env(tmp_path, monkeypatch):
    configured = tmp_path / "custom-logs"
    monkeypatch.setenv("WISP_RUN_LOG_DIR", str(configured))

    log_dir = supervisor_app._prepare_run_log_dir()

    assert log_dir == configured
    assert configured.is_dir()


def test_main_exits_when_single_instance_lock_is_held(tmp_path, monkeypatch):
    monkeypatch.setenv("WISP_RUN_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(supervisor_app.single_instance, "acquire", lambda: False)

    class ShouldNotStart:
        def __init__(self):
            raise AssertionError("workers should not start for duplicate instance")

    monkeypatch.setattr(supervisor_app, "WispSupervisor", ShouldNotStart)

    assert supervisor_app.main() == 2
