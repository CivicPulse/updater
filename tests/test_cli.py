import json
import os
import subprocess
import sys

from typer.testing import CliRunner

from civpulse_updater.cli import app
from civpulse_updater.config import load_source
from civpulse_updater.models import SourceConfig

runner = CliRunner()


def test_doctor_and_publishing_disabled(tmp_path):
    result = runner.invoke(app, ["--state-dir", str(tmp_path), "doctor"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["publishing_available"] is False
    result = runner.invoke(app, ["publish"])
    assert result.exit_code == 4


def test_invalid_source_and_date(tmp_path):
    result = runner.invoke(
        app, ["--state-dir", str(tmp_path), "sources", "validate", "../../secret"]
    )
    assert result.exit_code == 2
    result = runner.invoke(
        app, ["collect", "bibb-democrats", "--from", "bad", "--to", "2026-01-01"]
    )
    assert result.exit_code == 2


def test_real_cli_process(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "civpulse_updater",
            "--state-dir",
            str(tmp_path),
            "status",
        ],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ},
    )
    assert json.loads(result.stdout)["runs"] == []
    assert (tmp_path / "state.sqlite3").exists()


def test_source_validation_and_no_publish_mode():
    from pathlib import Path

    assert load_source(Path("sources"), "bibb-democrats").mode == "review-only"
    config = SourceConfig(
        id="test", url="https://example.org/", allowed_hosts=("example.org",)
    )
    import pytest

    with pytest.raises(ValueError):
        SourceConfig.model_validate({**config.model_dump(), "mode": "publish"})


def test_real_process_lock(tmp_path):
    from filelock import FileLock

    with FileLock(tmp_path / "updater.lock"):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "civpulse_updater",
                "--state-dir",
                str(tmp_path),
                "status",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    assert result.returncode == 3
    assert "owns the state lock" in result.stdout


def test_logs_do_not_expose_credentials(tmp_path):
    result = runner.invoke(
        app,
        [
            "--state-dir",
            str(tmp_path),
            "collect",
            "bibb-democrats",
            "--from",
            "secret-token-value",
            "--to",
            "2026-10-25",
        ],
    )
    assert result.exit_code == 2
    assert "secret-token-value" not in result.output


def test_browser_selection_is_explicit_and_validated(tmp_path, monkeypatch):
    seen = []

    def fake_collect(state, store, config, window, **kwargs):
        seen.append(config)
        return {"status": "complete"}

    monkeypatch.setattr("civpulse_updater.cli.collect_run", fake_collect)
    args = [
        "--state-dir",
        str(tmp_path),
        "collect",
        "bibb-school-board",
        "--from",
        "2026-09-09",
        "--to",
        "2026-09-09",
        "--browser",
        "firefox",
    ]
    assert runner.invoke(app, args).exit_code == 0
    assert seen[-1].browser_engine == "firefox" and not seen[-1].browser_fallback
    assert runner.invoke(app, args + ["--browser-fallback"]).exit_code == 0
    assert seen[-1].browser_fallback and seen[-1].mode == "review-only"
    assert runner.invoke(app, args[:-1] + ["invalid"]).exit_code == 2
    assert len(seen) == 2


def test_headed_implies_firefox_fallback_and_rejects_other_sources(
    tmp_path, monkeypatch
):
    seen = []
    monkeypatch.setattr(
        "civpulse_updater.cli.collect_run",
        lambda state, store, config, window, **kwargs: (
            seen.append(config) or {"status": "complete"}
        ),
    )
    args = [
        "--state-dir",
        str(tmp_path),
        "collect",
        "bibb-school-board",
        "--from",
        "2026-09-02",
        "--to",
        "2026-09-02",
        "--headed",
    ]
    assert runner.invoke(app, args).exit_code == 0
    assert seen[0].browser_headed and seen[0].browser_fallback
    assert seen[0].browser_engine == "firefox" and seen[0].mode == "review-only"
    assert runner.invoke(app, args + ["--browser", "chromium"]).exit_code == 2
    args[3] = "bibb-democrats"
    assert runner.invoke(app, args).exit_code == 2
    assert len(seen) == 1


def test_systematic_profile_is_explicit_and_scoped(tmp_path, monkeypatch):
    seen = []

    def capture(state, store, config, window, **kwargs):
        seen.append((config, kwargs))
        return {"status": "complete"}

    monkeypatch.setattr("civpulse_updater.cli.collect_run", capture)
    args = [
        "--state-dir",
        str(tmp_path),
        "collect",
        "bibb-school-board",
        "--from",
        "2026-01-01",
        "--to",
        "2026-12-31",
    ]
    assert runner.invoke(app, args).exit_code == 0
    assert not seen[-1][0].systematic and seen[-1][0].max_bytes == 100_000_000
    assert (
        runner.invoke(
            app, args + ["--systematic", "--resume-run", "fixture-run"]
        ).exit_code
        == 0
    )
    config, kw = seen[-1]
    assert (
        config.systematic and config.max_requests == 2000 and config.max_events == 100
    )
    assert config.max_bytes == 500_000_000 and config.max_total_bytes == 10_000_000_000
    assert config.request_interval == 1 and kw["resume_run"] == "fixture-run"
    args[3] = "macon-bibb-commission"
    assert runner.invoke(app, args + ["--systematic"]).exit_code == 0
    config, _ = seen[-1]
    assert config.systematic and config.adapter == "civicclerk"
    assert config.max_events == 100 and config.max_resources == 5000
    assert config.request_interval == 1 and not config.browser_fallback
    args[3] = "bibb-democrats"
    assert runner.invoke(app, args + ["--systematic"]).exit_code == 2
    assert len(seen) == 3


def test_resume_errors_explain_the_required_correction(tmp_path):
    result = runner.invoke(
        app,
        [
            "--state-dir",
            str(tmp_path),
            "collect",
            "bibb-school-board",
            "--from",
            "2026-01-01",
            "--to",
            "2026-12-31",
            "--resume-run",
            "missing",
        ],
    )
    assert result.exit_code == 2
    assert json.loads(result.stdout)["detail"] == "Unknown resume run"


def test_new_state_directory_is_private(tmp_path):
    import stat

    directory = tmp_path / "private-state"
    result = runner.invoke(app, ["--state-dir", str(directory), "status"])
    assert result.exit_code == 0
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
