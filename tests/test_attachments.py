"""Attachments: the article's PDF, supplements, audio and video, beside the page.

Two things carry this file. Discovery is page-controlled text, so what it finds
and what it does NOT find are both asserted against a synthetic page. And the
bytes decide what a file is: a publisher's login page answered with status 200
to a PDF request is the case that made the classifier exist.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from corpus_capture.sidecar import (
    ATTACHMENT_KINDS,
    SIDECAR_SCHEMA,
    SidecarError,
    attachment_payload_name,
    validate_attachments,
    validate_sidecar,
)
from corpus_capture.submission import (
    SubmissionError,
    validate_attachment_meta,
    validate_finalize,
    validate_submission,
)

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "extension"
NODE_MODULES = ROOT / "node_modules"
ATTACHMENTS_JS = (EXTENSION / "attachments.js").as_posix()
LIMITS_JS = (EXTENSION / "limits.js").as_posix()
SHA = "c" * 64


def _node() -> str | None:
    return shutil.which("node") or shutil.which("nodejs")


def _dom_available() -> bool:
    return (NODE_MODULES / "linkedom").is_dir()


if not _dom_available() and os.environ.get("CORPUS_CAPTURE_REQUIRE_DOM") == "1":
    raise RuntimeError("linkedom is missing; run `npm ci` before pytest")

needs_node = pytest.mark.skipif(_node() is None, reason="node is not installed on this host")
needs_dom = pytest.mark.skipif(not _dom_available(), reason="run `npm ci` for the DOM tests")


def _run(script: str) -> dict:
    result = subprocess.run(
        [_node(), "--input-type=module", "--eval", script],
        capture_output=True, text=True, check=False, cwd=ROOT,
        env={**os.environ, "NODE_PATH": str(NODE_MODULES)},
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def _refusal(exc_info) -> str:
    """The stable code, not the message: `str()` of a refusal is its detail."""

    return exc_info.value.code


# A synthetic NEJM-shaped article page. Nothing here is publisher text; the
# URLs are the SHAPES the discovery keys on, and the footer link is the negative
# control: a PDF the site serves that is not the article.
_BASE = "https://www.nejm.org"
_SUPPL = f"{_BASE}/doi/suppl/10.1056/NEJMp0000001/suppl_file"
_MEDIA = f"{_BASE}/cms/asset/0f0f0f0f-0000-4000-8000-000000000000/media"
PAGE = f"""
<!doctype html><html><head>
<meta name="citation_doi" content="10.1056/NEJMp0000001">
<meta name="citation_pdf_url" content="{_BASE}/doi/pdf/10.1056/NEJMp0000001">
</head><body>
<nav><a href="{_BASE}/about/author-guidelines.pdf">Author Guidelines</a></nav>
<main id="bodyContent">
  <p>{"word " * 150}</p>
  <a href="{_BASE}/doi/pdf/10.1056/NEJMp0000001?download=true">Download PDF</a>
  <a href="{_SUPPL}/nejmp0000001_appendix.pdf">Supplementary Appendix</a>
  <a href="{_SUPPL}/nejmp0000001_disclosures.pdf">Disclosure Forms</a>
  <a href="{_MEDIA}/nejmp0000001_interview.mp3">Download audio</a>
  <audio controls src="{_MEDIA}/nejmp0000001_interview.mp3"></audio>
  <video><source src="https://media.example.org/clip.mp4" type="video/mp4"></video>
  <a href="https://media.example.org/stream/master.m3u8">Watch the stream</a>
  <div data-ajaxurl="/do/10.1056/NEJMdo000123/full/" data-nejmdo="10.1056/NEJMdo000123">
    Quick Take
  </div>
  <div data-media-id="AbCd1234"></div>
</main>
<footer><a href="{_BASE}/doi/pdf/10.1056/NEJMp0000001">PDF</a></footer>
</body></html>
"""


JW_TEMPLATES = {"jwplayer": "cdn.jwplayer.com/v2/media/{media_id}"}


def _discover(markup: str = PAGE, profile: dict | None = None) -> dict:
    profile = {"media_metadata_endpoints": JW_TEMPLATES, **(profile or {})}
    return _run(f"""
      import {{ parseHTML }} from "linkedom";
      import {{ discoverAttachmentsInPage }} from "{ATTACHMENTS_JS}";
      import {{ LIMITS }} from "{LIMITS_JS}";
      const {{ document }} = parseHTML({json.dumps(markup)});
      globalThis.document = document;
      globalThis.location = {{
        origin: "https://www.nejm.org",
        href: "https://www.nejm.org/doi/full/10.1056/NEJMp0000001",
      }};
      console.log(JSON.stringify(discoverAttachmentsInPage({json.dumps(profile)}, LIMITS)));
    """)


@needs_node
@needs_dom
class TestDiscovery:
    def test_every_kind_is_found_once_and_classified(self):
        found = _discover()
        rows = found["attachments"]
        by_url = {row["url"]: row for row in rows}
        assert {row["kind"] for row in rows} == set(ATTACHMENT_KINDS) - {"other"}

        # The page's own declaration wins the PDF slot; the same link in the
        # body and the footer is the same URL and is listed once.
        pdfs = [row for row in rows if row["kind"] == "pdf"]
        assert pdfs[0]["source"] == "citation_pdf_url"
        assert all("NEJMp0000001" in row["url"] for row in pdfs)
        # A supplement that happens to be a PDF is a supplement, not the article.
        supplements = sorted(row["url"] for row in rows if row["kind"] == "supplement")
        assert len(supplements) == 2 and all("/doi/suppl/" in url for url in supplements)
        # The downloadable interview anchor and the <audio> element are the same
        # bytes and are listed once, under the anchor that found them first.
        audio = [row for row in rows if row["kind"] == "audio"]
        assert len(audio) == 1 and audio[0]["url"].endswith("_interview.mp3")
        assert audio[0]["source"] == "anchor"
        # Video: a direct mp4, an HLS stream that will be a recorded gap, a JW
        # media id and an NEJM card that both still need resolving.
        videos = {row["url"]: row for row in rows if row["kind"] == "video"}
        assert "https://media.example.org/clip.mp4" in videos
        assert videos["https://media.example.org/stream/master.m3u8"]["resolver"] == "hls"
        assert videos["https://cdn.jwplayer.com/v2/media/AbCd1234"]["resolver"] == "jwplayer"
        card = videos[f"{_BASE}/do/10.1056/NEJMdo000123/full/"]
        assert card["resolver"] == "nejm_do" and card["nejmdo"] == "10.1056/NEJMdo000123"
        # The negative control: the site's author guidelines are a PDF, but not
        # the article's, and nothing about the link says otherwise.
        assert f"{_BASE}/about/author-guidelines.pdf" not in by_url

    def test_indices_are_dense_and_ordered(self):
        rows = _discover()["attachments"]
        assert [row["index"] for row in rows] == list(range(1, len(rows) + 1))

    def test_the_count_is_a_ceiling_the_page_cannot_raise(self):
        many = "".join(
            f'<a href="{_SUPPL}/s{i}.pdf">Supplement {i}</a>' for i in range(60)
        )
        found = _discover(PAGE.replace("</main>", many + "</main>"))
        assert len(found["attachments"]) == 40
        assert found["overflow"] > 0

    def test_a_profile_selector_adds_what_the_generic_rules_miss(self):
        extra = f'<a class="pub-download" href="{_BASE}/files/odd-name">Data</a></main>'
        found = _discover(
            PAGE.replace("</main>", extra), {"attachment_link_selectors": ["a.pub-download"]}
        )
        row = next(r for r in found["attachments"] if r["url"] == f"{_BASE}/files/odd-name")
        assert row["kind"] == "other"
        assert row["source"].startswith("profile:")

    def test_a_pdf_on_another_site_is_a_citation_not_an_attachment(self):
        # The cycling position statement that found this links the WADA
        # Prohibited List from its body; the link text is the address.
        cited = "https://www.wada-ama.org/sites/default/files/2025list_en.pdf"
        extra = (
            f'<p>See <a href="{cited}">{cited}</a>.</p>'
            f'<a href="{_BASE}/doi/story/figure-legends.pdf">figure legends</a>'
            '<a href="https://cdn.example-publisher.net/articles/x.pdf">Download PDF</a>'
            '</main><aside><a href="https://www.nejm.org/help/guide.pdf">'
            'https://www.nejm.org/help/guide.pdf</a></aside>'
        )
        markup = PAGE.replace("</main>", extra)
        urls = {row["url"] for row in _discover(markup)["attachments"]}
        assert cited not in urls
        # A bare PDF link on the page's own origin inside the article stays.
        assert f"{_BASE}/doi/story/figure-legends.pdf" in urls
        # Off-site is off-site until the profile names the host.
        assert "https://cdn.example-publisher.net/articles/x.pdf" not in urls
        named = _discover(markup, {"attachment_origins": ["*.example-publisher.net"]})
        assert "https://cdn.example-publisher.net/articles/x.pdf" in {
            row["url"] for row in named["attachments"]
        }
        # Outside the article, an address is not a label that says "PDF".
        assert "https://www.nejm.org/help/guide.pdf" not in urls

    def test_only_http_targets_are_ever_candidates(self):
        extra = '<a href="javascript:void(0)">x.pdf</a><a href="ftp://x.test/a.mp3">a</a></main>'
        found = _discover(PAGE.replace("</main>", extra))
        assert all(row["url"].startswith("https://") for row in found["attachments"])


@needs_node
class TestBytesDecideWhatAFileIs:
    @pytest.mark.parametrize(
        ("kind", "mime", "head", "ok", "reason"),
        [
            ("pdf", "application/pdf", "255044462d312e37", True, None),
            # The case that made this exist: a login page, status 200, named .pdf.
            ("pdf", "text/html", "3c21646f63747970", False, "html_instead_of_pdf"),
            ("pdf", "application/octet-stream", "504b030414000000", False, "not_a_pdf"),
            ("audio", "application/octet-stream", "494433040000", True, None),
            ("audio", "audio/mpeg", "fffb90c4", True, None),
            ("audio", "text/html", "3c68746d6c3e", False, "html_instead_of_audio"),
            ("video", "video/mp4", "0000001c6674797069736f6d", True, None),
            ("video", "application/octet-stream", "0000001c6674797069736f6d", True, None),
            ("video", "text/html", "3c21", False, "html_instead_of_video"),
            ("supplement", "application/zip", "504b0304", True, None),
            ("supplement", "text/html", "3c21", False, "html_instead_of_file"),
            ("other", "application/octet-stream", "00000000", True, None),
        ],
    )
    def test_classification(self, kind, mime, head, ok, reason):
        verdict = _run(f"""
          import {{ classifyAttachmentBytes }} from "{ATTACHMENTS_JS}";
          console.log(JSON.stringify(classifyAttachmentBytes(
            {json.dumps(kind)}, {json.dumps(mime)}, {json.dumps(head)}
          )));
        """)
        assert verdict["ok"] is ok
        if reason:
            assert verdict["reason"] == reason

    def test_the_stored_suffix_comes_from_the_type_then_the_link(self):
        out = _run(f"""
          import {{ extensionFor }} from "{ATTACHMENTS_JS}";
          console.log(JSON.stringify([
            extensionFor("application/pdf", "https://x.test/download?id=1"),
            extensionFor("application/octet-stream", "https://x.test/a/interview.mp3?dl=1"),
            extensionFor("", "https://x.test/a/file.weird"),
            extensionFor("text/html", "https://x.test/a"),
          ]));
        """)
        assert out == [".pdf", ".mp3", ".bin", ".bin"]

    def test_jw_rendition_prefers_the_largest_within_the_ceiling(self):
        picked = _run(f"""
          import {{ pickJwRendition }} from "{ATTACHMENTS_JS}";
          const cdn = "https://cdn.jwplayer.com/videos/";
          const media = {{ playlist: [{{ sources: [
            {{ file: "https://cdn.jwplayer.com/manifests/x.m3u8", type: "application/x-mpegurl" }},
            {{ file: cdn + "x-480.mp4", type: "video/mp4", width: 480 }},
            {{ file: cdn + "x-1080.mp4", type: "video/mp4", width: 1080 }},
            {{ file: cdn + "x-2160.mp4", type: "video/mp4", width: 2160 }},
            {{ file: "http://cdn.jwplayer.com/videos/insecure.mp4", type: "video/mp4",
               width: 720 }},
          ] }}] }};
          const none = pickJwRendition({{ playlist: [] }});
          console.log(JSON.stringify([pickJwRendition(media), none]));
        """)
        assert picked == ["https://cdn.jwplayer.com/videos/x-1080.mp4", None]


@needs_node
def test_the_nejm_player_document_names_its_media_id_in_the_json_shape_it_has_today():
    """Measured 2026-09-24 on NEJMdo008691: the ajax document is JSON whose html
    carries mediaID with backslash-escaped quotes. The old pattern never matched it,
    so every NEJM Quick Take / Double Take failed as not_a_video."""

    out = _run(f"""
      import {{ playerMediaId }} from "{ATTACHMENTS_JS}";
      const esc = String.fromCharCode(92);
      const doc = JSON.stringify({{ hasAccess: true, html:
        '<media-player-app isModal="true" brand="nejm" mediaID="BOV6WXtL" player="numbered"></media-player-app>' }});
      console.log(JSON.stringify([
        doc.includes(esc + '"BOV6WXtL' + esc + '"'),
        playerMediaId(doc),
        playerMediaId(JSON.stringify({{ hasAccess: true }})),
        playerMediaId('<media-player-app mediaid="AbCd1234" player="qt"></media-player-app>'),
        playerMediaId('<iframe src="https://content.jwplatform.com/players/x.html?media_id=Zz9Yy8Xx">'),
        playerMediaId('<p>a Research Summary</p>'),
      ]));
    """)
    assert out == [True, "BOV6WXtL", "", "AbCd1234", "Zz9Yy8Xx", ""]


@needs_node
def test_both_sides_spell_the_stored_name_the_same_way():
    """The offline path has no receiver to choose the name; the two must agree."""

    cases = [("10-1056-nejmp0000001-20260917-0300", 1, "audio", ".mp3"),
             ("stem", 12, "supplement", ".xlsx"),
             ("stem", 3, "nonsense", ".exe"),
             ("stem", 4, "video", None)]
    js = _run(f"""
      import {{ attachmentPayloadName }} from "{ATTACHMENTS_JS}";
      const cases = {json.dumps(cases)};
      console.log(JSON.stringify(cases.map(([s, i, k, e]) => attachmentPayloadName(s, i, k, e))));
    """)
    assert js == [attachment_payload_name(*case) for case in cases]
    assert js[0] == "10-1056-nejmp0000001-20260917-0300--01-audio.mp3"
    assert js[2].endswith("--03-other.bin")


def test_the_injected_functions_reach_for_nothing_outside_the_page():
    """They are stringified into the page. Module scope does not travel with them."""

    source = (EXTENSION / "attachments.js").read_text(encoding="utf-8")
    for name in ("discoverAttachmentsInPage", "fetchAttachmentInPage",
                 "readAttachmentChunkInPage", "releaseAttachmentInPage", "fetchTextInPage"):
        start = source.index(f"function {name}(")
        depth = 0
        end = start
        for position in range(source.index("{", start), len(source)):
            depth += {"{": 1, "}": -1}.get(source[position], 0)
            if depth == 0:
                end = position
                break
        body = source[start:end]
        for forbidden in ("LIMITS.", "attachmentDecision", "ATTACHMENT_EXTENSIONS",
                          "KNOWN_SUFFIXES", "chrome."):
            assert forbidden not in body, f"{name} references module scope: {forbidden}"


def _captured_row(**overrides) -> dict:
    row = {"index": 1, "kind": "audio", "url": "https://www.nejm.org/a.mp3", "label": "Audio",
           "source": "anchor", "status": "captured", "payload_name": "stem--01-audio.mp3",
           "sha256": "b" * 64, "bytes": 1234, "mime": "audio/mpeg"}
    row.update(overrides)
    return row


def _failed_row(**overrides) -> dict:
    row = {"index": 2, "kind": "video", "url": "https://media.example.org/m.m3u8",
           "status": "failed", "reason": "hls_not_supported"}
    row.update(overrides)
    return row


class TestSidecarV3:
    def _sidecar(self, **overrides) -> dict:
        record = {
            "schema": SIDECAR_SCHEMA,
            "url": "https://www.nejm.org/doi/full/10.1056/NEJMp0000001",
            "html_sha256": "a" * 64,
            "payload_name": "stem.html",
            "access": "unknown",
            "attachments": [_captured_row(), _failed_row()],
            "attachments_complete": False,
            "attachments_discovered": 2,
        }
        record.update(overrides)
        return record

    def test_a_v3_sidecar_with_a_manifest_validates(self):
        assert validate_sidecar(self._sidecar(), payload_name="stem.html")

    def test_older_schemas_are_still_admissible(self):
        for schema in ("corpus-capture-sidecar-v1", "corpus-capture-sidecar-v2"):
            record = self._sidecar(schema=schema)
            del record["attachments"]
            assert validate_sidecar(record, payload_name="stem.html")

    def test_a_captured_row_must_carry_its_hash_and_name(self):
        broken = _captured_row()
        del broken["sha256"]
        with pytest.raises(SidecarError) as refused:
            validate_attachments([broken])
        assert _refusal(refused) == "attachment_hash_malformed"
        with pytest.raises(SidecarError) as refused:
            validate_attachments([_captured_row(payload_name="../escape.mp3")])
        assert _refusal(refused) == "attachment_payload_name_invalid"

    def test_a_failed_row_must_say_why(self):
        with pytest.raises(SidecarError) as refused:
            validate_attachments([{"kind": "pdf", "url": "https://x.test/a", "status": "failed"}])
        assert _refusal(refused) == "attachment_failed_without_reason"

    def test_unknown_kinds_fields_and_statuses_fail_closed(self):
        with pytest.raises(SidecarError) as refused:
            validate_attachments([_failed_row(kind="malware")])
        assert _refusal(refused) == "attachment_kind_unknown"
        with pytest.raises(SidecarError) as refused:
            validate_attachments([_failed_row(bundle_path="/etc")])
        assert _refusal(refused) == "attachment_field_unknown"
        with pytest.raises(SidecarError) as refused:
            validate_attachments([_failed_row(status="pending")])
        assert _refusal(refused) == "attachment_status_unknown"

    def test_an_attachment_may_not_be_the_payload_and_names_are_unique(self):
        record = self._sidecar()
        record["attachments"][0]["payload_name"] = "stem.html"
        with pytest.raises(SidecarError) as refused:
            validate_sidecar(record, payload_name="stem.html")
        assert _refusal(refused) == "attachment_is_the_payload"
        record = self._sidecar(attachments=[_captured_row(), _captured_row(index=3)])
        with pytest.raises(SidecarError) as refused:
            validate_sidecar(record)
        assert _refusal(refused) == "attachment_payload_name_duplicate"

    def test_the_manifest_is_bounded(self):
        with pytest.raises(SidecarError) as refused:
            validate_attachments([_failed_row() for _ in range(41)])
        assert _refusal(refused) == "attachments_too_many"


class TestSubmissionHoldAndFinalize:
    def _submission(self, **overrides) -> dict:
        body = {
            "url": "https://www.nejm.org/doi/full/10.1056/NEJMp0000001",
            "html": "<html><body>x</body></html>",
            "sha256": "a" * 64,
        }
        body.update(overrides)
        return body

    def test_hold_is_accepted_and_defaults_false(self):
        assert validate_submission(self._submission())["hold_attachments"] is False
        held = validate_submission(self._submission(hold_attachments=True))
        assert held["hold_attachments"] is True
        with pytest.raises(SubmissionError) as refused:
            validate_submission(self._submission(hold_attachments="yes"))
        assert _refusal(refused) == "hold_attachments_not_a_bool"

    def _meta(self, **overrides) -> dict:
        meta = {"index": 1, "kind": "pdf", "url": "https://www.nejm.org/doi/pdf/x",
                "sha256": SHA, "bytes": 10, "mime": "application/pdf", "ext": ".pdf"}
        meta.update(overrides)
        return meta

    def test_attachment_meta_is_exact(self):
        accepted = validate_attachment_meta(self._meta(label="PDF", ext=".exe"))
        assert accepted["ext"] == ".bin", "an unknown suffix is replaced, never stored"
        assert accepted["final_url"] == accepted["url"]

    @pytest.mark.parametrize(
        ("override", "code"),
        [
            ({"source_uid": "x"}, "attachment_meta_field_unknown"),
            ({"bytes": 0}, "attachment_bytes_malformed"),
            ({"bytes": 257 * 1024 * 1024}, "attachment_bytes_malformed"),
            ({"kind": "binary"}, "attachment_kind_unknown"),
            ({"sha256": "nope"}, "attachment_sha256_malformed"),
            ({"mime": "not a mime"}, "attachment_mime_malformed"),
            ({"index": 0}, "attachment_index_malformed"),
        ],
    )
    def test_attachment_meta_refusals(self, override, code):
        with pytest.raises(SubmissionError) as refused:
            validate_attachment_meta(self._meta(**override))
        assert _refusal(refused) == code

    def test_finalize_reuses_the_manifest_rule(self):
        rows = [_captured_row(kind="pdf", url="https://x.test/a.pdf", payload_name="s--01-pdf.pdf",
                              sha256="d" * 64, bytes=5, mime="application/pdf")]
        accepted = validate_finalize({"attachments": rows, "complete": True, "discovered": 1})
        assert accepted["complete"] is True
        assert accepted["discovered"] == 1 and accepted["overflow"] == 0
        with pytest.raises(SubmissionError) as refused:
            validate_finalize({"attachments": [dict(rows[0], sha256="x")], "complete": True})
        assert _refusal(refused) == "attachment_hash_malformed"
        with pytest.raises(SubmissionError) as refused:
            validate_finalize({"attachments": [], "source_uid": "x"})
        assert _refusal(refused) == "unknown_field"
        with pytest.raises(SubmissionError) as refused:
            validate_finalize({"attachments": [], "complete": "yes"})
        assert _refusal(refused) == "complete_not_a_bool"


class TestDuplicates:
    def test_a_duplicate_points_at_a_captured_row(self):
        rows = [_captured_row(index=1),
                {"index": 2, "kind": "pdf", "url": "https://x.test/a.pdf?download=true",
                 "status": "duplicate", "duplicate_of": 1, "sha256": "b" * 64, "bytes": 1234}]
        assert validate_attachments(rows) == rows

    @pytest.mark.parametrize(
        ("row", "code"),
        [
            ({"duplicate_of": None}, "attachment_duplicate_without_target"),
            ({"duplicate_of": 2}, "attachment_duplicate_of_itself"),
            ({"duplicate_of": 9}, "attachment_duplicate_target_missing"),
            ({"duplicate_of": 1, "sha256": "zz"}, "attachment_hash_malformed"),
        ],
    )
    def test_a_duplicate_that_points_nowhere_is_refused(self, row, code):
        duplicate = {"index": 2, "kind": "pdf", "url": "https://x.test/a.pdf",
                     "status": "duplicate"}
        duplicate.update({k: v for k, v in row.items() if v is not None})
        with pytest.raises(SidecarError) as refused:
            validate_attachments([_captured_row(index=1), duplicate])
        assert _refusal(refused) == code


@needs_node
@needs_dom
def test_a_download_link_and_the_declared_pdf_are_one_attachment():
    markup = PAGE.replace(
        "</main>",
        f'<a href="{_BASE}/doi/pdf/10.1056/NEJMp0000001?download=true&utm_source=x">PDF</a></main>',
    )
    pdfs = [row for row in _discover(markup)["attachments"] if row["kind"] == "pdf"]
    assert len(pdfs) == 1
    # A query that names a DIFFERENT file is not presentation and stays distinct.
    markup = PAGE.replace(
        "</main>",
        f'<a href="{_SUPPL}/get?file=1">Supplementary Appendix 1</a>'
        f'<a href="{_SUPPL}/get?file=2">Supplementary Appendix 2</a></main>',
    )
    urls = [row["url"] for row in _discover(markup)["attachments"] if "get?file=" in row["url"]]
    assert len(urls) == 2


@needs_node
def test_a_schema_refusal_reads_as_a_sentence():
    """"[object Object],[object Object]" was the whole message for a refused capture."""

    capture_js = (EXTENSION / "capture.js").as_posix()
    out = _run(f"""
      import {{ describeRefusal }} from "{capture_js}";
      console.log(JSON.stringify([
        describeRefusal({{ detail: [
          {{ type: "extra_forbidden", loc: ["body", "profile"],
             msg: "Extra inputs are not permitted" }},
          {{ type: "extra_forbidden", loc: ["body", "figures"],
             msg: "Extra inputs are not permitted" }},
        ] }}, 422),
        describeRefusal({{ detail: "capture_not_held" }}, 409),
        describeRefusal(null, 502),
      ]));
    """)
    assert out == [
        "http_422 profile: Extra inputs are not permitted; figures: Extra inputs are not permitted",
        "capture_not_held",
        "http_502",
    ]


@needs_node
@needs_dom
def test_without_a_registered_resolver_a_media_id_is_not_a_candidate():
    """The extension ships no player endpoint; the registry names it."""

    found = _run(f"""
      import {{ parseHTML }} from "linkedom";
      import {{ discoverAttachmentsInPage }} from "{ATTACHMENTS_JS}";
      const {{ document }} = parseHTML({json.dumps(PAGE)});
      globalThis.document = document;
      globalThis.location = {{ origin: "https://www.nejm.org", href: "https://www.nejm.org/x" }};
      console.log(JSON.stringify(discoverAttachmentsInPage({{}}, {{ maxAttachments: 40 }})));
    """)
    assert not [row for row in found["attachments"] if row.get("resolver") == "jwplayer"]


def test_the_registry_names_the_player_endpoint_and_validates_it():
    from corpus_capture.profiles import ProfileRegistryError, load_registry, validate_registry

    registry = load_registry()
    assert "{media_id}" in registry["generic"]["media_metadata_endpoints"]["jwplayer"]
    broken = json.loads(json.dumps(registry))
    for bad in ("https://cdn.x.test/v/{media_id}", "cdn.x.test/v/no-id"):
        broken["profiles"][0]["media_metadata_endpoints"] = {"jwplayer": bad}
        with pytest.raises(ProfileRegistryError):
            validate_registry(broken)



@needs_node
@needs_dom
def test_read_aloud_is_not_an_attachment_but_an_interview_still_is():
    markup = PAGE.replace('</main>', '''
      <div class="audio-player audio-player--tts">
        <audio><source src="https://play.example.org/download?mediaId=tts"
          type="audio/mpeg"></audio>
        <a href="https://play.example.org/read-aloud.mp3">Listen</a>
        <span data-audio-src="https://play.example.org/lazy.mp3"></span>
      </div>
      <audio src="https://play.example.org/download?mediaId=interview"></audio>
      </main>''')
    rows = _discover(markup, {"attachment_link_selectors": [".audio-player a", ".audio-player source"]})['attachments']
    audio = [row['url'] for row in rows if row['kind'] == 'audio']
    assert f'{_MEDIA}/nejmp0000001_interview.mp3' in audio
    assert 'https://play.example.org/download?mediaId=interview' in audio
    assert not any('tts' in row['url'] or 'read-aloud' in row['url'] or 'lazy' in row['url'] for row in rows)


@needs_node
@needs_dom
@pytest.mark.parametrize('markup, expected', [
    ('<a href="/doi/pdf/10.1126/example?download=true">DOWNLOAD</a>',
     'https://publisher.example/doi/pdf/10.1126/example?download=true'),
    ('<base href="https://evil.example"><a href="/doi/pdf/10.1126/example">PDF</a>',
     'https://publisher.example/doi/pdf/10.1126/example'),
    ('<a href="https://evil.example/doi/pdf/10.1126/example">PDF</a>', None),
    ('<a href="/doi/pdf/10.1126/another">PDF</a>', None),
    ('<a href="https://user:pass@publisher.example/doi/pdf/10.1126/example">PDF</a>', None),
    ('<form>Sign in</form>', None),
])
def test_pdf_reader_requires_explicit_same_origin_same_doi_download(markup, expected):
    out = _run(f'''
      import {{ DOMParser }} from "linkedom";
      import {{ pdfDownloadInPage }} from "{ATTACHMENTS_JS}";
      globalThis.DOMParser = DOMParser;
      globalThis.location = {{ origin: "https://publisher.example" }};
      globalThis.__corpusCaptureAttachments = {{ token: new TextEncoder().encode({json.dumps(markup)}) }};
      console.log(JSON.stringify(pdfDownloadInPage("token", "https://publisher.example/doi/epdf/10.1126/example")));
    ''')
    assert out == expected


@needs_node
@needs_dom
@pytest.mark.parametrize('download_body, expected_status', [('%PDF-1.4\nsynthetic', 'captured'), ('<html>Login</html>', 'failed')])
def test_collect_resolves_viewer_and_checks_download_bytes(download_body, expected_status):
    out = _run(f'''
      import {{ DOMParser }} from "linkedom";
      import {{ webcrypto }} from "node:crypto";
      import {{ collectAttachments }} from "{ATTACHMENTS_JS}";
      globalThis.DOMParser = DOMParser;
      Object.defineProperty(globalThis, "crypto", {{ value: webcrypto, configurable: true }});
      globalThis.location = {{ origin: "https://www.nejm.org" }};
      globalThis.chrome = {{ scripting: {{ executeScript: async (s) => [{{ result: await s.func(...s.args) }}] }} }};
      const calls = [], stored = [];
      globalThis.fetch = async (url) => {{
        calls.push(url);
        const viewer = url.includes('/epdf/');
        const response = new Response(viewer
          ? '<a href="/doi/pdf/10.1126/example?download=true">DOWNLOAD</a>'
          : {json.dumps(download_body)}, {{ headers: {{ 'content-type': viewer ? 'text/html' : 'application/pdf' }} }});
        Object.defineProperty(response, 'url', {{ value: url }});
        return response;
      }};
      const rows = await collectAttachments({{
        tabId: 1, pageUrl: "https://www.nejm.org/story", profile: {{}},
        discovered: [{{index:1,kind:'pdf',url:'https://www.nejm.org/doi/epdf/10.1126/example',source:'anchor'}}],
        sink: async (meta, bytes) => {{ stored.push(new TextDecoder().decode(bytes)); return {{payload_name:'source.pdf'}}; }},
      }});
      console.log(JSON.stringify({{ rows, calls, stored, retained: Object.keys(globalThis.__corpusCaptureAttachments) }}));
    ''')
    assert len(out['calls']) == 2
    assert out['rows'][0]['status'] == expected_status
    assert out['retained'] == []
    if expected_status == 'captured':
        assert out['stored'] == [download_body]
        assert out['rows'][0]['final_url'].endswith('/doi/pdf/10.1126/example?download=true')
    else:
        assert out['stored'] == []
        assert out['rows'][0]['reason'] == 'html_instead_of_pdf'
