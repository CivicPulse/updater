"""Versioned contracts; upstream parsing is separate from validated records."""

import hashlib
import json
from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class SourceConfig(Model):
    # TOML arrays become tuples; normalized event models remain strict.
    model_config = ConfigDict(extra="forbid", frozen=True, strict=False)
    schema_version: Literal[1] = 1
    id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,79}$")
    adapter: str = "bibb-democrats"
    protocol_version: Literal[1] = 1
    url: str
    allowed_hosts: tuple[str, ...]
    timezone: str = "America/New_York"
    mode: Literal["review-only"] = "review-only"
    browser_fallback: bool = False
    browser_engine: Literal["chromium", "firefox"] = "chromium"
    browser_headed: bool = False
    max_requests: int = Field(default=500, ge=1, le=5000)
    max_pages: int = Field(default=10, ge=1, le=50)
    max_events: int = Field(default=150, ge=1, le=500)
    max_bytes: int = Field(default=2_000_000, ge=1024, le=100_000_000)
    max_total_bytes: int = Field(default=100_000_000, ge=1024, le=1_000_000_000)
    max_resources: int = Field(default=100, ge=1, le=500)
    request_interval: float = Field(default=0.25, ge=0.25, le=10)
    enabled_plugins: tuple[str, ...] = ()

    @model_validator(mode="after")
    def browser_scope(self):
        if self.browser_headed and (
            not self.browser_fallback or self.browser_engine != "firefox"
        ):
            raise ValueError("Headed manual verification requires Firefox fallback")
        if self.browser_fallback and (
            self.adapter != "simbli"
            or self.allowed_hosts != ("simbli.eboardsolutions.com",)
        ):
            raise ValueError("Browser fallback is restricted to Simbli")
        return self

    @field_validator("timezone")
    @classmethod
    def timezone_exists(cls, value: str) -> str:
        ZoneInfo(value)
        return value


class Window(Model):
    start: date
    end: date  # inclusive local dates

    @model_validator(mode="after")
    def bounded(self):
        if not 0 <= (self.end - self.start).days <= 366:
            raise ValueError("Window must be ordered and no longer than 367 days")
        return self


class Manifest(Model):
    name: str
    version: str
    protocol_version: Literal[1] = 1
    capabilities: tuple[str, ...] = ("events", "ics")


class Reference(Model):
    url: str
    page_url: str
    source_title: str | None = None
    meeting_type: str | None = None
    minutes_published: bool | None = None
    listing_hash: str
    displayed_start: str | None = None
    displayed_end: str | None = None
    description: str | None = None


class Evidence(Model):
    method: Literal["GET", "POST"] = "GET"
    direction: Literal["request", "response"] = "response"
    transport: Literal["http", "playwright"] = "http"
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    url: str
    redirects: tuple[str, ...] = ()
    media_type: str
    size: int
    fetched_at: datetime


class Document(Model):
    id: str
    title: str
    role: str
    source_url: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size: int


class Event(Model):
    schema_version: Literal[1] = 1
    source_id: str
    uid: str = Field(min_length=1, max_length=1000)
    recurrence_id: str | None = None
    title: str = Field(min_length=1, max_length=10000)
    source_url: str
    starts_at: datetime | date
    ends_at: datetime | date | None = None
    timezone: str
    all_day: bool
    status: Literal["scheduled", "tentative", "cancelled", "unknown"]
    description: str | None = None
    location: str | None = None
    documents: tuple[Document, ...] = ()
    warnings: tuple[str, ...] = ()

    @model_validator(mode="after")
    def valid_times(self):
        if isinstance(self.starts_at, datetime):
            if self.all_day or self.starts_at.tzinfo is None:
                raise ValueError("Timed events require timezone-aware timestamps")
        elif not self.all_day:
            raise ValueError("Date-only events must be all-day")
        if self.ends_at is not None:
            if type(self.ends_at) is not type(self.starts_at):
                raise ValueError("Start and end must use matching date types")
            if isinstance(self.ends_at, datetime) and self.ends_at.tzinfo is None:
                raise ValueError("End must be timezone-aware")
            if self.ends_at < self.starts_at:
                raise ValueError("End precedes start")
        return self

    @property
    def external_id(self) -> str:
        # Unambiguous composite key, including an unchanged recurrence identity.
        return json.dumps([self.uid, self.recurrence_id], separators=(",", ":"))


class Envelope(Model):
    event: Event
    evidence_hashes: tuple[str, ...]
    parser_version: str
    locator: str


def canonical(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
