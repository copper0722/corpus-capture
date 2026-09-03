"""The sidecar contract: identity travels beside the bytes, never inside them."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from corpus_capture.sidecar import (
    SIDECAR_SCHEMA,
    SidecarError,
    capture_slug,
    download_basename,
    normalize_doi,
    validate_sidecar,
)


class TestDoiNormalization:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("10.1056/NEJMoa2600001", "10.1056/nejmoa2600001"),
            ("doi:10.1056/NEJMoa2600001", "10.1056/nejmoa2600001"),
            ("https://doi.org/10.1056/NEJMoa2600001", "10.1056/nejmoa2600001"),
            ("http://dx.doi.org/10.1056/NEJMoa2600001", "10.1056/nejmoa2600001"),
            # A DOI lifted out of a landing-page URL keeps the viewer segment,
            # because a DOI inside a URL runs to the end of the path.
            ("10.1056/NEJMdo008670/full/", "10.1056/nejmdo008670"),
            ("10.1001/jama.2026.13187?utm=x", "10.1001/jama.2026.13187"),
        ],
    )
    def test_the_three_spellings_publishers_serve(self, raw, expected):
        assert normalize_doi(raw) == expected

    @pytest.mark.parametrize("raw", ["", None, "not a doi", "10.x/y", "https://nejm.org/a"])
    def test_a_non_doi_is_none_and_not_a_guess(self, raw):
        assert normalize_doi(raw) is None


class TestSlug:
    def test_a_doi_makes_the_better_stem(self):
        assert capture_slug(doi="10.1056/NEJMoa2600001", url="https://x.test/a") == (
            "10-1056-nejmoa2600001"
        )

    def test_without_a_doi_the_host_and_last_segment_are_used(self):
        assert capture_slug(doi=None, url="https://journals.lww.com/jasn/fulltext/x") == (
            "journals-lww-com-x"
        )

    def test_a_url_with_nothing_in_it_still_produces_a_name(self):
        """A stem is a convenience, so a missing one is never an error."""

        assert capture_slug(doi=None, url="") == "page"
        assert capture_slug(doi=None, url="not a url") == "page"

    def test_the_stem_is_bounded(self):
        long_url = "https://x.test/" + ("a" * 300)
        assert len(capture_slug(doi=None, url=long_url)) <= 72


def test_the_offline_pair_shares_one_stem():
    when = datetime(2026, 9, 3, 9, 15, tzinfo=UTC)
    stem = download_basename(doi="10.1001/JAMA.2026.13187", url="https://x.test/a",
                             captured_at=when)
    assert stem == "10-1001-jama-2026-13187-20260903-0915"


def _sidecar(**overrides):
    base = {
        "schema": SIDECAR_SCHEMA,
        "url": "https://example.org/article/1",
        "html_sha256": "a" * 64,
        "payload_name": "example-20260903-0915.html",
        "access": "login_required",
    }
    base.update(overrides)
    return base


class TestValidation:
    def test_a_well_formed_sidecar_passes(self):
        assert validate_sidecar(_sidecar(), payload_name="example-20260903-0915.html")

    def test_v1_is_still_accepted(self):
        """Bytes captured by an older producer are still bytes."""

        assert validate_sidecar(_sidecar(schema="corpus-capture-sidecar-v1"))

    def test_a_sidecar_that_names_another_payload_is_refused(self):
        """Otherwise a leftover .json lends its DOI to the next file with that stem."""

        with pytest.raises(SidecarError) as exc:
            validate_sidecar(_sidecar(), payload_name="something-else.html")
        assert exc.value.code == "sidecar_payload_mismatch"

    @pytest.mark.parametrize(
        ("overrides", "code"),
        [
            ({"schema": "corpus-capture-sidecar-v9"}, "sidecar_schema_unknown"),
            ({"url": "ftp://example.org/a"}, "sidecar_url_missing"),
            ({"html_sha256": "not-a-hash"}, "sidecar_hash_malformed"),
            ({"payload_name": ""}, "sidecar_names_no_payload"),
            ({"access": "public_domain"}, "sidecar_access_unknown"),
        ],
    )
    def test_each_refusal_carries_its_own_code(self, overrides, code):
        with pytest.raises(SidecarError) as exc:
            validate_sidecar(_sidecar(**overrides))
        assert exc.value.code == code

    def test_something_that_is_not_a_document_is_refused(self):
        with pytest.raises(SidecarError):
            validate_sidecar("corpus-capture-sidecar-v2")
