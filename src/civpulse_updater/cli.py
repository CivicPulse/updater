import json
import sys
from contextlib import contextmanager
from datetime import date
from functools import wraps
from pathlib import Path
from typing import Annotated

import typer
from filelock import FileLock, Timeout
from loguru import logger

from . import __version__
from .config import Settings, load_source
from .evidence import EvidenceStore
from .models import Window
from .pipeline import ResumeError, latest_resumable_run
from .pipeline import collect as collect_run
from .pipeline import replay as replay_run
from .plugins import load_adapter
from .state import State
from .transport import validate_url

app = typer.Typer(no_args_is_help=True)
sources_app = typer.Typer(no_args_is_help=True)
changes_app = typer.Typer(no_args_is_help=True)
app.add_typer(sources_app, name="sources")
app.add_typer(changes_app, name="changes")
settings = Settings()


def emit(value):
    # ensure_ascii prevents source-controlled terminal escapes/control characters.
    typer.echo(json.dumps(value, ensure_ascii=True, default=str))


def guarded(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except Timeout as error:
            emit({"error": "Another updater process owns the state lock"})
            raise typer.Exit(3) from error
        except (ValueError, OSError, KeyError) as error:
            # Exception messages may contain hostile source text or credentials.
            emit(
                {
                    "error": type(error).__name__,
                    "detail": str(error)
                    if isinstance(error, ResumeError)
                    else "Invalid input, unavailable source, or state integrity failure",
                }
            )
            raise typer.Exit(2) from error

    return wrapper


@app.callback()
def main(
    state_dir: Annotated[Path | None, typer.Option()] = None,
    sources_dir: Annotated[Path | None, typer.Option()] = None,
    json_logs: Annotated[bool, typer.Option()] = False,
):
    global settings
    settings = Settings()
    if state_dir is not None:
        settings.state_dir = state_dir
    if sources_dir is not None:
        settings.sources_dir = sources_dir
    logger.remove()
    logger.add(
        sys.stderr,
        serialize=json_logs or settings.json_logs,
        diagnose=False,
        backtrace=False,
        format="{level} {message}",
    )


@contextmanager
def local_state():
    settings.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    with FileLock(settings.state_dir / "updater.lock", timeout=0):
        state = State(settings.state_dir)
        try:
            yield state
        finally:
            state.engine.dispose()


@app.command()
def doctor():
    emit(
        {
            "version": __version__,
            "mode": "review-only",
            "publishing_available": False,
            "state_dir": str(settings.state_dir),
            "sources_dir": str(settings.sources_dir),
        }
    )


@sources_app.command("list")
@guarded
def sources_list():
    emit([path.stem for path in sorted(settings.sources_dir.glob("*.toml"))])


@sources_app.command("validate")
@guarded
def sources_validate(source: str):
    config = load_source(settings.sources_dir, source)
    validate_url(config.url, config.allowed_hosts)
    manifest = load_adapter(config).manifest
    emit({"source": config.id, "adapter": manifest.model_dump(), "mode": config.mode})


@app.command()
@guarded
def collect(
    source: str,
    start: Annotated[str, typer.Option("--from")],
    end: Annotated[str, typer.Option("--to")],
    browser_fallback: Annotated[bool, typer.Option("--browser-fallback")] = False,
    browser: Annotated[str | None, typer.Option("--browser")] = None,
    headed: Annotated[
        bool,
        typer.Option(
            "--headed",
            help="Open Firefox for manual CAPTCHA verification; rate and download limits apply.",
        ),
    ] = False,
    systematic: Annotated[
        bool,
        typer.Option(
            "--systematic",
            help="Bibb archive capture: bounded 2,000 requests, 100 meetings, 500 MB/file and 10 GB/run; normal pacing remains.",
        ),
    ] = False,
    full_session: Annotated[
        bool,
        typer.Option(
            "--full-session",
            help="Allow up to two years for a Georgia legislative session capture.",
        ),
    ] = False,
    resume_latest: Annotated[
        bool,
        typer.Option(
            "--resume-latest",
            help="Resume the newest matching unfinished capture; start fresh when no usable checkpoint exists.",
        ),
    ] = False,
    resume_run: Annotated[
        str | None,
        typer.Option(
            "--resume-run",
            help="Continue saved record/resource checkpoints; source, session and dates must match.",
        ),
    ] = None,
):
    if resume_latest and resume_run is not None:
        raise ResumeError("Choose either --resume-run or --resume-latest")
    config = load_source(settings.sources_dir, source)
    if systematic:
        config = type(config).model_validate(
            config.model_dump()
            | {
                "systematic": True,
                "max_requests": 2000,
                "max_pages": 10,
                "max_events": 100,
                "max_resources": 5000,
                "max_bytes": 500_000_000,
                "max_total_bytes": 10_000_000_000,
                "response_seconds": 180,
            }
        )
    if browser_fallback or browser is not None or headed:
        config = type(config).model_validate(
            config.model_dump()
            | {
                "browser_fallback": browser_fallback
                or headed
                or config.browser_fallback,
                "browser_engine": browser
                or ("firefox" if headed else config.browser_engine),
                "browser_headed": headed or config.browser_headed,
            }
        )
    window = Window(
        start=date.fromisoformat(start),
        end=date.fromisoformat(end),
        full_session=full_session,
    )
    with local_state() as state:
        if resume_latest:
            resume_run = latest_resumable_run(state, config, window)
            logger.info(
                "Resuming checkpointed run {}"
                if resume_run
                else "Starting a fresh capture",
                *([resume_run] if resume_run else []),
            )
        result = collect_run(
            state,
            EvidenceStore(settings.state_dir / "evidence"),
            config,
            window,
            resume_run=resume_run,
        )
    emit(result)
    if result["status"] != "complete":
        raise typer.Exit(1)


@app.command()
@guarded
def replay(run: str):
    with local_state() as state:
        result = replay_run(state, EvidenceStore(settings.state_dir / "evidence"), run)
    emit(result)
    if result["status"] != "complete":
        raise typer.Exit(1)


@app.command()
@guarded
def status():
    with local_state() as state:
        emit(
            {
                "mode": "review-only",
                "runs": state.rows(
                    "SELECT id,source,status,started_at,finished_at,record_count,change_count FROM runs ORDER BY started_at DESC LIMIT 20"
                ),
                "changes": state.rows(
                    "SELECT status,count(*) AS count FROM changes GROUP BY status"
                ),
            }
        )


@changes_app.command("list")
@guarded
def changes_list():
    with local_state() as state:
        emit(
            state.rows(
                "SELECT id,run_id,source,external_id,status,hash FROM changes ORDER BY created_at"
            )
        )


@changes_app.command("show")
@guarded
def changes_show(change_id: str):
    with local_state() as state:
        rows = state.rows("SELECT * FROM changes WHERE id=:id", id=change_id)
        if not rows:
            raise ValueError("Unknown change")
        row = rows[0]
        for key in ("payload", "before_payload", "evidence"):
            row[key] = json.loads(row[key]) if row[key] else None
        row["decisions"] = state.rows(
            "SELECT * FROM decisions WHERE change_id=:id", id=change_id
        )
        emit(row)


def decide(change_id, actor, reason, decision, acknowledge=False):
    with local_state() as state:
        emit(state.review(change_id, decision, actor, reason, acknowledge))


@changes_app.command("approve")
@guarded
def approve(
    change_id: str,
    actor: Annotated[str, typer.Option()],
    reason: Annotated[str, typer.Option()],
    acknowledge_warnings: bool = False,
):
    decide(change_id, actor, reason, "approved", acknowledge_warnings)


@changes_app.command("reject")
@guarded
def reject(
    change_id: str,
    actor: Annotated[str, typer.Option()],
    reason: Annotated[str, typer.Option()],
):
    decide(change_id, actor, reason, "rejected")


@app.command()
def publish():
    emit({"error": "Publishing is unavailable in this review-only release"})
    raise typer.Exit(4)


@app.command("probe-simbli-attachment")
@guarded
def probe_simbli_attachment(
    run: str,
    index: Annotated[int, typer.Option(min=0)] = 0,
    browser: Annotated[str | None, typer.Option("--browser")] = None,
):
    """Retry one challenged attachment saved by a prior Simbli collection."""
    from .browser_probe import probe

    with local_state() as state:
        result = probe(
            state,
            EvidenceStore(settings.state_dir / "evidence"),
            run,
            index,
            browser_engine=browser,
        )
    emit(result)
    if not result["recovered"]:
        raise typer.Exit(1)
