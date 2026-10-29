from importlib.metadata import entry_points
from typing import Protocol

from .models import Envelope, Manifest, Reference, SourceConfig, Window


class Adapter(Protocol):
    manifest: Manifest

    def discover(
        self, content: bytes, url: str, sha: str
    ) -> tuple[list[Reference], list[str]]: ...
    def parse(
        self, content: bytes, ref: Reference, sha: str, window: Window
    ) -> list[Envelope]: ...


def load_adapter(config: SourceConfig) -> Adapter:
    from .adapters.bibb_democrats import BibbDemocrats
    from .adapters.civicclerk import CivicClerk
    from .adapters.georgia_legislature import GeorgiaLegislature
    from .adapters.macon_water import MaconWater
    from .adapters.simbli import Simbli

    registry = {
        "bibb-democrats": BibbDemocrats,
        "civicclerk": CivicClerk,
        "simbli": Simbli,
        "macon-water-authority": MaconWater,
        "georgia-legislature": GeorgiaLegislature,
    }
    for entry in entry_points(group="civpulse_updater.sources"):
        if entry.name in config.enabled_plugins:
            if entry.name in registry:
                raise ValueError("Duplicate adapter name")
            registry[entry.name] = entry.load()
    if config.adapter not in registry:
        raise ValueError("Adapter not installed or explicitly enabled")
    adapter = registry[config.adapter](config)
    if adapter.manifest.protocol_version != config.protocol_version:
        raise ValueError("Incompatible adapter protocol")
    return adapter
