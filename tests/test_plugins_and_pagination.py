import pytest
from conftest import FixtureFetcher

from civpulse_updater.evidence import EvidenceStore
from civpulse_updater.models import digest
from civpulse_updater.pipeline import collect
from civpulse_updater.plugins import load_adapter
from civpulse_updater.state import State


def test_plugin_is_never_loaded_without_allowlist(monkeypatch, config):
    class Entry:
        name = "untrusted"

        def load(self):
            raise AssertionError("Unapproved plugin executed")

    monkeypatch.setattr(
        "civpulse_updater.plugins.entry_points", lambda **kwargs: [Entry()]
    )
    assert load_adapter(config).manifest.name == "bibb-democrats"


def test_duplicate_plugin_rejected(monkeypatch, config):
    class Entry:
        name = "bibb-democrats"

        def load(self):
            raise AssertionError("Duplicate plugin executed")

    monkeypatch.setattr(
        "civpulse_updater.plugins.entry_points", lambda **kwargs: [Entry()]
    )
    with pytest.raises(ValueError, match="Duplicate"):
        load_adapter(config.model_copy(update={"enabled_plugins": ("bibb-democrats",)}))


def test_pagination_loop_terminates_without_duplicate_changes(tmp_path, config, window):
    class Pages(FixtureFetcher):
        def get(self, url):
            body, evidence = super().get(url)
            if "format=ical" not in url:
                next_url = "/events?page=2" if "page=2" not in url else "/events"
                body += f'<section class="item-pagination"><a href="{next_url}">Next</a></section>'.encode()
                evidence = evidence.model_copy(
                    update={"sha256": digest(body), "size": len(body)}
                )
            return body, evidence

    fetcher = Pages()
    state = State(tmp_path)
    result = collect(
        state, EvidenceStore(tmp_path / "evidence"), config, window, fetcher
    )
    assert result["status"] == "complete"
    assert result["proposed_changes"] == 1
    assert len(fetcher.calls) == 3


def test_page_limit_marks_partial(tmp_path, config, window):
    class Pages(FixtureFetcher):
        def get(self, url):
            body, evidence = super().get(url)
            if "format=ical" not in url:
                body += b'<section class="item-pagination"><a href="/events?page=2">Next</a></section>'
                evidence = evidence.model_copy(
                    update={"sha256": digest(body), "size": len(body)}
                )
            return body, evidence

    result = collect(
        State(tmp_path),
        EvidenceStore(tmp_path / "evidence"),
        config.model_copy(update={"max_pages": 1}),
        window,
        Pages(),
    )
    assert result["status"] == "partial"
    assert result["proposed_changes"] == 1
