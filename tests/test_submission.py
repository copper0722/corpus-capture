"""What a receiver accepts, and what it refuses before it costs anything.

The audit found the reference receiver parsing a whole `dict` body before it
authenticated, bounding only `html` and only afterwards, keeping every receipt
for the life of the process, and handing any JSON value to a normalizer that
called `.strip()` on it. These are the refusals that replaced all four, tested
here as library code rather than as a fenced block nobody runs.
"""

from __future__ import annotations

import pytest
from corpus_capture.submission import (
    MAX_BODY_BYTES,
    MAX_METADATA_BYTES,
    ReceiptStore,
    SubmissionError,
    enforce_body_size,
    validate_submission,
)


def _submission(**overrides):
    base = {
        "url": "https://example.org/article/1",
        "html": "<!doctype html><p>the article</p>",
        "sha256": "a" * 64,
        "captured_at": "2026-09-03T09:15:00+00:00",
        "doi": "10.1056/NEJMoa2600001",
        "title": "A title",
        "access": "login_required",
    }
    base.update(overrides)
    return {key: value for key, value in base.items() if value is not _ABSENT}


_ABSENT = object()


class TestSizeIsRefusedBeforeTheBodyIsRead:
    """F-09. The cheap half of the check, and the one that stops the allocation."""

    def test_a_declared_length_over_the_limit_is_refused(self):
        with pytest.raises(SubmissionError) as exc:
            enforce_body_size(str(MAX_BODY_BYTES + 1))
        assert exc.value.code == "payload_too_large"

    def test_a_declared_length_within_the_limit_is_returned(self):
        assert enforce_body_size("1024") == 1024

    def test_a_missing_length_is_not_an_error_here(self):
        """It is the streaming reader's job; refusing would break chunked posts."""

        assert enforce_body_size(None) == 0
        assert enforce_body_size("") == 0

    @pytest.mark.parametrize("value", ["-1", "abc", "1e6", "12 "])
    def test_a_malformed_length_is_refused(self, value):
        if value == "12 ":
            assert enforce_body_size(value) == 12  # int() tolerates surrounding space
            return
        with pytest.raises(SubmissionError) as exc:
            enforce_body_size(value)
        assert exc.value.code == "content_length_malformed"


class TestTheSchemaIsExact:
    def test_a_well_formed_submission_passes_and_normalizes_the_doi(self):
        accepted = validate_submission(_submission())
        assert accepted["doi"] == "10.1056/nejmoa2600001"
        assert accepted["profile"] == "generic"
        assert accepted["final_url"] == accepted["url"]

    def test_a_non_string_doi_is_a_refusal_and_not_a_500(self):
        """F-11 exactly: `{}` reached `.strip()` and raised AttributeError."""

        for value in ({}, [], 7, True):
            with pytest.raises(SubmissionError) as exc:
                validate_submission(_submission(doi=value))
            assert exc.value.code == "doi_not_a_string"

    def test_an_unparseable_doi_is_dropped_rather_than_refused(self):
        """The page said something; it was not a DOI. That is not the caller's fault."""

        assert validate_submission(_submission(doi="see the article"))["doi"] is None

    @pytest.mark.parametrize(
        ("field", "code"),
        [
            ("source_uid", "producer_asserted_identity"),
            ("bundle_path", "producer_asserted_identity"),
            ("rights", "producer_asserted_identity"),
            ("tags", "producer_asserted_identity"),
            ("reader_url", "producer_asserted_identity"),
        ],
    )
    def test_a_producer_may_not_assert_identity_or_rights(self, field, code):
        """Refused loudly. Dropping it silently lets the producer believe it did."""

        with pytest.raises(SubmissionError) as exc:
            validate_submission(_submission(**{field: "x"}))
        assert exc.value.code == code

    def test_an_unknown_field_is_refused_rather_than_ignored(self):
        with pytest.raises(SubmissionError) as exc:
            validate_submission(_submission(surprise="x"))
        assert exc.value.code == "unknown_field"

    @pytest.mark.parametrize(
        ("overrides", "code"),
        [
            ({"html": ""}, "html_missing"),
            ({"html": 5}, "html_missing"),
            ({"sha256": "nope"}, "sha256_malformed"),
            ({"sha256": None}, "sha256_missing"),
            ({"url": "ftp://example.org/a"}, "url_not_http"),
            ({"url": "https://example.org/" + "a" * 4000}, "url_too_long"),
            ({"title": 42}, "title_not_a_string"),
            ({"title": "t" * 5000}, "title_too_long"),
            ({"captured_at": "yesterday"}, "captured_at_malformed"),
            ({"access": "public_domain"}, "access_unknown"),
            ({"access": ["open"]}, "access_unknown"),
            ({"authors": "one author"}, "authors_not_a_list"),
            ({"authors": ["ok", 5]}, "author_malformed"),
            ({"authors": ["x"] * 500}, "authors_too_many"),
            ({"publisher_meta": []}, "publisher_meta_not_an_object"),
            ({"publisher_meta": {"journal": 5}}, "publisher_meta_value_malformed"),
            ({"figures": {}}, "figures_not_a_list"),
            ({"figures": [{"label": "Figure 1", "surprise": "x"}]}, "figure_field_unknown"),
            ({"figures": [{"position": "first"}]}, "figure_position_malformed"),
        ],
    )
    def test_each_shape_has_its_own_refusal(self, overrides, code):
        with pytest.raises(SubmissionError) as exc:
            validate_submission(_submission(**overrides))
        assert exc.value.code == code

    def test_something_that_is_not_an_object_is_refused(self):
        for value in ("a string", [1, 2], None, 7):
            with pytest.raises(SubmissionError) as exc:
                validate_submission(value)
            assert exc.value.code == "body_not_an_object"

    def test_forty_bounded_fields_still_add_up(self):
        """F-10. Per-field limits are not a total limit."""

        meta = {f"k{index}": "v" * 2000 for index in range(40)}
        figures = [{"label": f"Figure {n}", "caption": "c" * 2000} for n in range(100)]
        with pytest.raises(SubmissionError) as exc:
            validate_submission(
                _submission(publisher_meta=meta, authors=["a" * 200] * 100, figures=figures)
            )
        assert exc.value.code == "metadata_too_large"
        # The ceiling has to sit BELOW the sum of the per-field maxima, or it is
        # a check that can never fire, which is worse than no check at all.
        assert MAX_METADATA_BYTES < 40 * 2000 + 200 * 2000 + 100 * 200

    def test_a_caption_may_be_long_and_an_asset_url_may_not_be_a_novel(self):
        long_caption = {"label": "Figure 1", "caption": "c" * 1999}
        assert validate_submission(_submission(figures=[long_caption]))["figures"]
        with pytest.raises(SubmissionError):
            validate_submission(_submission(figures=[{"caption": "c" * 3000}]))


class TestReceiptsExpire:
    """F-10. Every receipt was kept for the life of the process."""

    def test_a_receipt_round_trips(self):
        store = ReceiptStore()
        receipt_id = store.put({"state": "received"})
        assert store.get(receipt_id)["state"] == "received"
        assert store.get(receipt_id)["receipt_id"] == receipt_id

    def test_an_expired_receipt_is_gone(self, monkeypatch):
        store = ReceiptStore(ttl_seconds=10)
        clock = [1000.0]
        monkeypatch.setattr(store, "_now", lambda: clock[0])
        receipt_id = store.put({"state": "received"})
        clock[0] += 11
        assert store.get(receipt_id) is None
        assert len(store) == 0

    def test_the_store_has_a_ceiling_and_evicts_the_oldest(self):
        store = ReceiptStore(max_entries=3)
        ids = [store.put({"state": "received", "n": n}) for n in range(5)]
        assert len(store) == 3
        assert store.get(ids[0]) is None
        assert store.get(ids[-1])["n"] == 4

    def test_an_update_keeps_the_original_expiry(self, monkeypatch):
        """A receipt polled every minute must not become immortal."""

        store = ReceiptStore(ttl_seconds=10)
        clock = [1000.0]
        monkeypatch.setattr(store, "_now", lambda: clock[0])
        receipt_id = store.put({"state": "received"})
        clock[0] += 5
        assert store.update(receipt_id, state="admitted")["state"] == "admitted"
        clock[0] += 6
        assert store.get(receipt_id) is None
