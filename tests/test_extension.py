"""The extension's shape, pinned where it must not drift.

This extension is deliberately privileged: it needs `<all_urls>` because it
fetches the figures with the reader's own session. A privilege that broad is
only defensible while the things it enables stay true -- no stored scripts, no
cookie API, no background collection, and one sidecar schema shared with
whatever receives the capture.
"""

from __future__ import annotations

import json
import re
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
        "activeTab", "scripting", "storage", "downloads", "alarms", "sidePanel"
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


def test_the_producer_names_the_version_that_actually_ran():
    """The tool string was a literal, and it had drifted two minor versions.

    A receiver records `capture_tool` and later uses it to explain a capture's
    shape. A literal that disagrees with the manifest makes that explanation
    wrong in exactly the case where somebody is trying to work out which
    version produced a bad artifact.
    """

    manifest = json.loads(_read("manifest.json"))
    source = _read("capture.js")
    assert "getManifest" in source, "the version is a literal again"
    assert not re.search(r'"chrome-capture/\d', source), "a hardcoded version is back"
    assert re.fullmatch(r"\d+\.\d+\.\d+", manifest["version"])


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

    assert "if (error.status && error.status < 500) throw error;" in _read("runner.js")


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
        "GET /api/v1/capture/profiles", SIDECAR_SCHEMA,
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

    def test_the_side_panel_shows_the_profile_status(self):
        panel = _read("sidepanel.js")
        assert "profileForUrl" in panel

    def test_the_registry_is_fetched_and_cached(self):
        source = _read("capture.js")
        assert "/api/v1/capture/profiles" in source
        assert "PROFILES_KEY" in source


def _node() -> str | None:
    return shutil.which("node") or shutil.which("nodejs")


@pytest.mark.skipif(_node() is None, reason="node is not installed on this host")
@pytest.mark.parametrize("module", ["serialize.js", "capture.js", "sidepanel.js",
                                    "options.js", "service-worker.js", "attachments.js",
                                    "runner.js", "progress.js", "launch.js", "review.js",
                                    "identity-probe.js", "bundle-view.js"])
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


def test_a_capture_runs_in_the_side_panel_not_in_a_popup():
    """Chrome closes a popup on the first click elsewhere; the side panel stays open."""

    manifest = json.loads(_read("manifest.json"))
    assert "default_popup" not in manifest["action"]
    assert manifest["side_panel"] == {"default_path": "sidepanel.html"}
    assert not (EXTENSION / "popup.html").exists()
    worker = _read("service-worker.js")
    # The property name is the trap: the "Icon" spelling throws synchronously
    # and silently aborts the worker.
    assert "setPanelBehavior({ openPanelOnActionClick: true })" in worker
    assert "openPanelOnActionIconClick" not in worker
    panel = _read("sidepanel.js")
    # Preview first: the open page's identity is read and looked up before saving.
    assert "runCapture" in panel and "probeTab(tab.id)" in panel and "lookupIdentity(doi)" in panel
    assert 'id="save" type="submit"' in _read("sidepanel.html")
    assert '<script type="module" src="sidepanel.js">' in _read("sidepanel.html")
    # The progress tab stays as the fallback for a browser without the API.
    assert "runCapture" in _read("progress.js")
    assert 'progress.html?tab=' in _read("launch.js")


def test_one_key_starts_a_capture():
    manifest = json.loads(_read("manifest.json"))
    command = manifest["commands"]["capture-current-tab"]
    assert command["suggested_key"]["default"]
    worker = _read("service-worker.js")
    assert "capture-current-tab" in worker
    assert "openCaptureTab" in worker
    # The panel must open inside the keyboard gesture: nothing awaited before it.
    handler = worker[worker.index('addListener((command, tab) => {'):]
    assert handler.index("chrome.sidePanel.open(") < handler.index("await ")
    assert "CAPTURE_REQUEST_KEY" in worker and "CAPTURE_REQUEST_KEY" in _read("sidepanel.js")
    assert 'export const CAPTURE_REQUEST_KEY' in _read("launch.js")


def test_a_closing_side_panel_publishes_an_upload_with_its_review():
    """Uploads die with the panel; what arrived is published with the reader's review."""

    source = _read("sidepanel.js")
    hide = source[source.index('addEventListener("pagehide"'):]
    assert "if (!inFlight || !inFlight.uploading) return;" in hide
    assert "keepalive: true" in hide and "review: inFlight.review" in hide


def test_a_closing_progress_tab_closes_its_held_capture():
    source = _read("progress.js")
    assert "pagehide" in source and "keepalive: true" in source
    assert "keepalive" in _read("capture.js")



def test_a_retry_for_the_page_doi_is_still_the_proposal():
    """A lookup retried after "in progress" is the proposal, not the reader's correction."""

    source = _read("sidepanel.js")
    start = source.index("async function lookup(doi)")
    body = source[start:source.index("async function loadPreview(")]
    assert "normalizeDoi(preview.detectedDoi)" in body
    assert "if (own) preview.baseline" in body
    assert "overwriteTouched: !own" in body


def test_opening_the_panel_reads_the_page_once():
    """The panel's own tab events must not read the page and ask the receiver again."""

    source = _read("sidepanel.js")
    start = source.index("async function ensurePreview(tab)")
    ensure = source[start:source.index("function fieldControl(")]
    assert "if (same(preview)) return undefined;" in ensure
    assert "if (same(reading)) return reading.done;" in ensure
    start = source.index("async function loadPreview(tab)")
    load = source[start:source.index("async function readPreview(")]
    assert "reading = { tabId: tab.id, url: tab.url, done };" in load
    assert "if (mine === generation) reading = null;" in load
    # Startup and the keyboard both go through the same gate.
    keyboard = source[source.index("async function takeCaptureRequest("):]
    assert "await ensurePreview(tab);" in keyboard
    assert "force" not in source[source.index("async function showTab("):start]


def test_the_panel_says_which_build_is_loaded():
    """A reload is how every fix reaches an unpacked extension; the panel says which."""

    assert '<span id="version" class="version"></span>' in _read("sidepanel.html")
    assert '$("#version").textContent = EXTENSION_VERSION;' in _read("sidepanel.js")
    capture = _read("capture.js")
    assert "export const EXTENSION_VERSION" in capture and "getManifest" in capture
    # One copy of the number, in the manifest: the literal that used to live in
    # capture.js had drifted two minor versions before it was read from there.
    version = json.loads(_read("manifest.json"))["version"]
    for name in ("sidepanel.js", "sidepanel.html", "options.js", "options.html", "capture.js"):
        assert version not in _read(name), f"{name} pins the version instead of reading it"


def test_the_receivers_reading_page_turns_the_panel_into_its_card():
    """On the receiver's own origin the panel shows the declared work; it captures nothing."""

    panel = _read("sidepanel.js")
    show = panel[panel.index("async function showTab("):
                 panel.index("async function ensurePreview(")]
    assert "isReceiverPage(tab.url, await receiverBase())" in show
    assert show.index("isReceiverPage(") < show.index("await ensurePreview(tab)")
    assert "followBundle(tab)" in show
    # The page is watched from inside, not polled from the panel.
    assert "func: awaitBundleDeclaration" in panel
    assert "BUNDLE_WAIT_MS" in panel and "BUNDLE_SETTLE_MS" in panel
    # A declared DOI is completed from the receiver's record, once per DOI.
    assert "identity = await lookupIdentity(doi);" in panel
    assert "identities.has(doi)" in panel
    # Neither the button nor the keyboard captures a reading page.
    start = panel[panel.index("async function startCapture("):panel.index("function receiptItem(")]
    assert "if (isReceiverPage(tab.url, await receiverBase())) return;" in start
    keyboard = panel[panel.index("async function takeCaptureRequest("):]
    assert "isReceiverPage(tab.url, await receiverBase())" in keyboard
    assert 'id="bundle"' in _read("sidepanel.html")
    assert 'body[data-mode="reader"] #run' in _read("sidepanel.css")


def test_the_panel_says_only_what_the_values_do_not():
    """Every visible word earns its place (operator request, 2026-09-18).

    The static markup is labels and controls only: the headings, the notes about
    the normal case and the instructions that used to fill the panel are gone,
    and the budget keeps them from growing back one sentence at a time. The
    hover titles and accessible names do not count; they are not on screen.
    """

    html = _read("sidepanel.html")
    body = html[html.index("<body>"):]
    body = re.sub(r"<!--.*?-->", "", body, flags=re.S)
    body = re.sub(r"<script.*?</script>", "", body, flags=re.S)
    visible = re.sub(r"<[^>]+>", " ", body)
    visible = re.sub(r"\s+", "", visible)
    assert len(visible) <= 40, visible
    for gone in ("目前分頁", "書目資料", "最近的收據", "儲存並建立", "直接以目前預覽儲存"):
        assert gone not in html
    panel = _read("sidepanel.js")
    for gone in ("已載入${source}", "corpus 已有這篇", "這個分頁不是 http(s) 文章頁",
                 "收據 ${outcome.receiptId}"):
        assert gone not in panel
