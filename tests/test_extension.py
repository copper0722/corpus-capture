"""The extension's shape, pinned where it must not drift.

This extension is deliberately privileged: it needs `<all_urls>` because it
fetches the figures with the reader's own session. A privilege that broad is
only defensible while the things it enables stay true -- no stored scripts, no
cookie API, no background collection, and one sidecar schema shared with
whatever receives the capture.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from corpus_capture.sidecar import SIDECAR_SCHEMA, download_basename

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "extension"


def _read(name: str) -> str:
    return (EXTENSION / name).read_text(encoding="utf-8")


def test_the_manifest_declares_only_what_the_capture_needs():
    manifest = json.loads(_read("manifest.json"))

    assert manifest["manifest_version"] == 3
    assert set(manifest["permissions"]) == {
        "activeTab", "scripting", "storage", "downloads", "alarms"
    }
    # Figures come back through the extension's own fetch, which is why the host
    # permission is broad; nothing else here may reach for a session directly.
    assert manifest["host_permissions"] == ["<all_urls>"]
    assert "cookies" not in manifest["permissions"]
    assert "webRequest" not in manifest["permissions"]
    assert "history" not in manifest["permissions"]
    assert manifest["background"] == {
        "service_worker": "service-worker.js", "type": "module"
    }
    assert "'unsafe-eval'" not in manifest["content_security_policy"]["extension_pages"]


def test_the_serializer_strips_what_must_never_be_stored():
    source = _read("serialize.js")

    for stripped in ("script", "noscript", "iframe"):
        assert stripped in source
    assert 'attribute.name' in source and '/^on/i' in source
    # The page's own declarations are the receiver's identity evidence: a
    # consumer reads exactly these out of the stored HTML.
    for preserved in ("citation_doi", "dc.identifier", "citation_publication_date",
                      'link[rel="canonical"]'):
        assert preserved in source


def test_the_serializer_takes_the_bytes_the_reader_actually_saw():
    """`currentSrc`, not `src`: a lazy figure's src attribute is a placeholder."""

    assert "currentSrc" in _read("serialize.js")


def test_the_client_and_the_receiver_share_one_sidecar_schema():
    """A second spelling here is a silently unadmissible download."""

    assert f'"{SIDECAR_SCHEMA}"' in _read("capture.js")


@pytest.mark.parametrize(
    "field",
    ["url", "final_url", "doi", "title", "captured_at", "html_sha256", "payload_name"],
)
def test_the_offline_sidecar_carries_every_field_a_receiver_reads(field: str):
    source = _read("capture.js")
    assert f"{field}:" in source


def test_the_capture_posts_to_the_one_typed_endpoint():
    source = _read("capture.js")

    assert "/api/v1/intake/html" in source
    assert "/api/v1/intake/${encodeURIComponent(receiptId)}" in source
    assert "x-corpus-service-token" in source
    assert "corpus-capture/${stem}.html" in source
    assert "corpus-capture/${stem}.json" in source


def test_the_hash_is_computed_over_the_bytes_that_are_sent():
    """The receiver recomputes it; a client that hashed something else fails closed."""

    source = _read("capture.js")
    assert "crypto.subtle.digest(\"SHA-256\", new TextEncoder().encode(text))" in source
    assert "sha256: await sha256Hex(html)" in source


def test_a_4xx_is_not_retried_through_the_offline_path():
    """Downloading a payload the receiver refused only moves the refusal."""

    assert "if (error.status && error.status < 500) throw error;" in _read("popup.js")


def test_the_token_is_stored_where_it_does_not_sync():
    options = _read("options.js")
    assert "chrome.storage.local.set" in options
    assert "chrome.storage.sync" not in options


def test_no_default_endpoint_is_baked_in():
    """The receiver is the user's; a built-in address is wrong for everyone."""

    assert 'DEFAULT_API_BASE = ""' in _read("capture.js")
    assert "api_base_not_configured" in _read("capture.js")


def test_the_endpoint_must_be_https_or_loopback():
    """A token sent over plaintext to a LAN address is a token on the wire."""

    options = _read("options.js") + _read("capture.js")
    assert "https:" in options
    assert "localhost" in options or "127.0.0.1" in options


def test_the_readme_documents_installing_and_the_offline_path():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "chrome://extensions" in readme
    assert "Load unpacked" in readme
    assert "corpus-capture/" in readme


def test_the_protocol_is_documented_where_it_can_travel():
    protocol = (ROOT / "docs" / "protocol.md").read_text(encoding="utf-8")

    for expected in (
        "POST /api/v1/intake/html", "GET /api/v1/intake/{receipt_id}",
        "GET /api/v1/capture/profiles", "corpus-capture-sidecar-v2",
        "handoff.json",
    ):
        assert expected in protocol


class TestStructuralFigureSelection:
    """The rule that replaced picking images by byte size."""

    def test_the_serializer_scopes_to_the_article_container(self):
        source = _read("serialize.js")
        assert "article_container_selectors" in source
        assert "articleRoot" in source
        assert "bodyClone.appendChild(articleRoot.cloneNode(true))" in source

    def test_the_label_pattern_matches_a_number_with_no_space(self):
        """`Figure1`. `\\b` does not match between `e` and `1`."""

        source = _read("serialize.js")
        assert "efigure|etable|figure|fig" in source
        assert "\\b" not in source.split("const LABEL")[1].split("\n")[0].replace(
            "([0-9]+|[ivxlc]+|[a-z])?\\b", ""
        )

    def test_the_manifest_is_built_before_the_src_is_rewritten(self):
        source = _read("serialize.js")
        assert source.index("const figureRows") < source.index("copies.forEach")

    def test_everything_unclaimed_is_kept_but_flagged(self):
        source = _read("serialize.js")
        assert "decorative:" in source
        assert "not_declared_by_the_article" in source

    def test_the_popup_shows_the_profile_status(self):
        popup = _read("popup.js")
        assert "profileForUrl" in popup

    def test_the_registry_is_fetched_and_cached(self):
        source = _read("capture.js")
        assert "/api/v1/capture/profiles" in source
        assert "PROFILES_KEY" in source


def _node() -> str | None:
    return shutil.which("node") or shutil.which("nodejs")


@pytest.mark.skipif(_node() is None, reason="node is not installed on this host")
@pytest.mark.parametrize("module", ["serialize.js", "capture.js", "popup.js",
                                    "options.js", "service-worker.js"])
def test_every_module_parses(module: str):
    """A syntax error here is an extension that never loads and never says why.

    Chrome reports it on the extensions page, which nobody looks at until the
    button does nothing. `--check` parses without executing, so no chrome.* stub
    is needed and nothing here touches the network.
    """

    result = subprocess.run(
        [_node(), "--input-type=module", "--check"],
        input=(EXTENSION / module).read_text(encoding="utf-8"),
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(_node() is None, reason="node is not installed on this host")
def test_the_client_and_the_receiver_agree_on_the_download_name():
    """Both sides build the offline filename; a divergence splits the pair.

    The stem is not identity -- the sidecar is -- but the `.html` and the
    `.json` are matched BY stem, so a client that spells it differently from the
    contract emits two files that no longer point at each other.
    """

    cases = [
        ("10.1001/JAMA.2026.13187", "https://jamanetwork.com/a/1"),
        (None, "https://www.nejm.org/doi/full/10.1056/NEJMoa2600001"),
        (None, "https://journals.lww.com/jasn/"),
    ]
    script = f"""
      import {{ downloadName }} from "{(EXTENSION / 'capture.js').as_posix()}";
      const when = new Date("2026-09-03T09:15:00Z");
      const rows = {json.dumps(cases)};
      console.log(JSON.stringify(rows.map(([doi, url]) => downloadName(doi, url, when))));
    """
    result = subprocess.run(
        [_node(), "--input-type=module", "--eval", script],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    from_client = json.loads(result.stdout)
    when = datetime(2026, 9, 3, 9, 15, tzinfo=UTC)
    from_library = [download_basename(doi=doi, url=url, captured_at=when) for doi, url in cases]
    assert from_client == from_library
