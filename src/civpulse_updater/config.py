import tomllib
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from .models import SourceConfig


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CIVPULSE_", extra="ignore")
    state_dir: Path = Path(".state")
    sources_dir: Path = Path("sources")
    json_logs: bool = False


def load_source(directory: Path, name: str) -> SourceConfig:
    # A CLI identifier must never turn into a filesystem traversal.
    if not name or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in name):
        raise ValueError("Invalid source identifier")
    with (directory / f"{name}.toml").open("rb") as handle:
        result = SourceConfig.model_validate(tomllib.load(handle))
    if result.id != name:
        raise ValueError("Source filename and ID differ")
    return result
