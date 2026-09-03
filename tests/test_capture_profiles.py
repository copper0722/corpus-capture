"""The registry is DATA, and `status` in it is a measurement.

One set of selectors drives the browser extension, the acquisition script and
the intake sidecar mapping. Written three times they drift, and the copy that
drifts silently is the one nobody runs by hand.
"""

from __future__ import annotations

import json

import pytest
from corpus_capture.profiles import (
    REGISTRY_PATH,
    SCHEMA_VERSION,
    ProfileRegistryError,
    fixture_path,
    load_registry,
    profile_for_url,
    profile_status_table,
    public_registry,
    validate_registry,
)


def test_the_shipped_registry_satisfies_its_own_schema():
    assert load_registry()["schema_version"] == SCHEMA_VERSION


def test_the_registry_is_pure_data():
    """No code, no URLs, no absolute paths -- only selectors and host patterns.

    This file is served to a browser extension over the network. The forbidden
    private-deployment vocabulary is checked repository-wide by
    `tests/test_public_safety.py`, which is the only file that spells those
    strings out; here the rule is the shape of the data itself.
    """

    raw = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    text = json.dumps(raw, ensure_ascii=False)

    assert "://" not in text, "a registry entry names a machine"
    # Selector patterns legitimately start with "/" (`/logo`, `/banner` are asset
    # path fragments). The value that must never be absolute is the one that IS a
    # path: `fixture` is resolved against the registry's own repository root.
    for profile in [raw["generic"], *raw["profiles"]]:
        fixture = profile.get("fixture")
        assert not fixture or not str(fixture).startswith("/"), profile["id"]


class TestSchemaFailsClosed:
    """A registry that is wrong must say so at load, not at capture time."""

    def _base(self) -> dict:
        return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))

    def test_a_supported_profile_without_a_fixture_is_refused(self):
        registry = self._base()
        registry["profiles"][0]["fixture"] = None
        with pytest.raises(ProfileRegistryError, match="fixture"):
            validate_registry(registry)

    def test_an_unsupported_profile_without_a_measured_reason_is_refused(self):
        registry = self._base()
        registry["profiles"][0]["status"] = "unsupported"
        registry["profiles"][0].pop("reason", None)
        with pytest.raises(ProfileRegistryError, match="reason"):
            validate_registry(registry)

    def test_an_unknown_key_is_refused(self):
        """A typo in a selector key is otherwise a profile that does nothing."""

        registry = self._base()
        registry["profiles"][0]["figure_selector"] = ["figure"]
        with pytest.raises(ProfileRegistryError, match="unknown keys"):
            validate_registry(registry)

    def test_a_duplicate_id_is_refused(self):
        registry = self._base()
        registry["profiles"].append(dict(registry["profiles"][0]))
        with pytest.raises(ProfileRegistryError, match="duplicates"):
            validate_registry(registry)

    def test_a_wrong_schema_version_is_refused(self):
        registry = self._base()
        registry["schema_version"] = "capture-profiles-v99"
        with pytest.raises(ProfileRegistryError, match="schema_version"):
            validate_registry(registry)

    def test_a_selector_list_of_the_wrong_shape_is_refused(self):
        registry = self._base()
        registry["profiles"][0]["figure_selectors"] = ["figure", ""]
        with pytest.raises(ProfileRegistryError, match="non-empty strings"):
            validate_registry(registry)


class TestResolution:
    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            ("https://www.nejm.org/do/10.1056/NEJMdo008670/full/", "nejm"),
            ("https://nejm.org/doi/full/10.1056/x", "nejm"),
            ("https://journals.lww.com/jasn/fulltext/10.1681/asn.1", "lww"),
            ("https://www.ovid.com/jnls/kidney360/fulltext/x", "lww"),
            ("https://jamanetwork.com/journals/jama/fullarticle/1", "jama"),
            ("https://www.nature.com/articles/s41581-026-1", "nature"),
            ("https://pmc.ncbi.nlm.nih.gov/articles/PMC1/", "pmc"),
        ],
    )
    def test_a_known_host_resolves_to_its_profile(self, url, expected):
        profile = profile_for_url(url)
        assert profile["id"] == expected
        assert profile["matched"] is True

    def test_an_unknown_host_is_generic_and_not_an_error(self):
        """Capture must still work on a site nobody has profiled."""

        profile = profile_for_url("https://some-journal.example/article/1")
        assert profile["id"] == "generic"
        assert profile["status"] == "generic"
        assert profile["matched"] is False
        assert profile["figure_selectors"]

    def test_a_profile_inherits_what_it_does_not_override(self):
        profile = profile_for_url("https://www.nejm.org/x")
        assert profile["decorative_asset_patterns"], "inherited from generic"
        assert profile["figure_selectors"][0] == "figure.graphic", "its own"

    def test_a_malformed_url_still_resolves(self):
        assert profile_for_url("not a url")["id"] == "generic"
        assert profile_for_url("")["id"] == "generic"


class TestPublicProjection:
    """What the extension fetches. It will be published, so it carries no paths."""

    def test_the_fixture_path_is_dropped(self):
        payload = public_registry()
        assert all("fixture" not in profile for profile in payload["profiles"])
        assert payload["schema_version"] == SCHEMA_VERSION

    def test_every_profile_keeps_what_the_popup_shows(self):
        for profile in public_registry()["profiles"]:
            assert profile["id"] and profile["display_name"] and profile["status"]


def test_the_status_table_reports_every_profile():
    table = profile_status_table()
    assert {row["id"] for row in table} == {
        profile["id"] for profile in load_registry()["profiles"]
    }
    for row in table:
        if row["status"] == "supported":
            assert fixture_path(row).is_file()
        if row["status"] == "unsupported":
            assert row["reason"]
