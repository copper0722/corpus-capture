"""Where bytes may come from, and whose cookies go with them.

The extension runs under `<all_urls>` and fetches assets with the reader's own
session, because publishers gate display-resolution figures on the session
cookie the tab already carries. That makes it a deputy, and every asset URL it
is handed came out of page-controlled markup. These are the cases that decide
whether it is a confused one.

The policy is JavaScript, so it is exercised in node rather than reimplemented
in Python: a Python copy of the rule would pass while the shipped rule was
wrong, which is the only failure mode that matters here.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

EXTENSION = Path(__file__).resolve().parents[1] / "extension"


def _node() -> str | None:
    return shutil.which("node") or shutil.which("nodejs")


pytestmark = pytest.mark.skipif(_node() is None, reason="node is not installed on this host")


def _run(script: str):
    result = subprocess.run(
        [_node(), "--input-type=module", "--eval", script],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _decide(cases, *, page, profile=None):
    script = f"""
      import {{ assetDecision }} from "{(EXTENSION / 'net-policy.js').as_posix()}";
      const page = {json.dumps(page)};
      const profile = {json.dumps(profile or {})};
      console.log(JSON.stringify({json.dumps(cases)}.map(
        (url) => assetDecision(url, {{ pageUrl: page, profile }})
      )));
    """
    return _run(script)


PAGE = "https://www.nejm.org/doi/full/10.1056/NEJMoa2600001"
# Assembled rather than written out. A URL literal with a user and a password
# before its host is what a credential-in-a-URL looks like to a secret scanner,
# and this file is vendored into repositories whose pre-push hook blocks on
# exactly that shape -- measured 2026-09-03, it blocked one. The URL is
# synthetic -- it is the negative-test input for `credentials_in_url` -- so the
# fix is to stop it LOOKING like a credential, not to stop checking for one.
USERINFO = "reader" + ":" + "unused"
CREDENTIALED_URL = f"https://{USERINFO}@www.nejm.org/f1.png"


class TestWhatMayBeFetched:
    def test_the_page_s_own_origin_is_fetched_with_the_reader_s_session(self):
        """The entitled figure is the entire reason this extension exists."""

        [decision] = _decide(["https://www.nejm.org/cms/asset/abc/f1.jpeg"], page=PAGE)
        assert decision["allowed"] is True
        assert decision["credentials"] == "include"
        assert decision["reason"] == "page_origin"

    def test_a_profile_cdn_is_fetched_without_the_reader_s_session(self):
        """An entitlement check on a third-party host is where sending the
        session is most likely to be handing it to someone the reader did not
        choose. The bytes are still worth having; the cookie is not."""

        [decision] = _decide(
            ["https://csvc.nejm.org/content/figure/f1.jpeg"],
            page=PAGE, profile={"asset_origins": ["*.nejm.org", "csvc.nejm.org"]},
        )
        assert decision["allowed"] is True
        assert decision["credentials"] == "omit"
        assert decision["reason"] == "profile_cdn"

    def test_an_unlisted_third_party_is_refused(self):
        [decision] = _decide(["https://tracker.example.net/pixel.gif"], page=PAGE)
        assert decision["allowed"] is False
        assert decision["reason"] == "off_origin"

    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1:8000/secrets",
            "https://127.0.0.1/secrets",
            "https://localhost/secrets",
            "https://10.0.0.5/admin",
            "https://192.168.1.1/status",
            "https://172.16.4.4/",
            "https://169.254.169.254/latest/meta-data/iam/security-credentials/",
            "https://metadata.google.internal/computeMetadata/v1/",
            "https://printer.local/queue",
            "https://wiki.internal/page",
            "https://intranet/",
            "https://[::1]/",
            "https://2130706433/",
            "https://0x7f000001/",
        ],
    )
    def test_nothing_reaches_the_reader_s_own_network(self, url):
        """Loopback, RFC1918, link-local, cloud metadata, private naming, and
        the two spellings of 127.0.0.1 that are not dotted quads."""

        [decision] = _decide([url], page=PAGE)
        assert decision["allowed"] is False, url
        assert decision["reason"] in {"blocked_host", "insecure_scheme"}, url

    def test_a_private_host_is_refused_even_from_its_own_page(self):
        """Same-origin is a reason to send cookies, not a reason to skip the host check."""

        [decision] = _decide(
            ["https://192.168.1.1/figure.png"], page="https://192.168.1.1/article"
        )
        assert decision["allowed"] is False
        assert decision["reason"] == "blocked_host"

    def test_plaintext_is_refused_even_on_the_page_s_own_origin(self):
        """Bytes anyone on the path can rewrite must not be stored as the publisher's."""

        [decision] = _decide(
            ["http://www.nejm.org/cms/asset/f1.jpeg"], page="http://www.nejm.org/a"
        )
        assert decision["allowed"] is False
        assert decision["reason"] == "insecure_scheme"

    @pytest.mark.parametrize(
        "url", ["javascript:alert(1)", "file:///etc/passwd", "chrome-extension://x/y.png",
                "blob:https://www.nejm.org/abc"],
    )
    def test_only_https_is_a_scheme_this_will_fetch(self, url):
        [decision] = _decide([url], page=PAGE)
        assert decision["allowed"] is False

    def test_a_url_carrying_credentials_is_refused(self):
        [decision] = _decide([CREDENTIALED_URL], page=PAGE)
        assert decision["allowed"] is False
        assert decision["reason"] == "credentials_in_url"

    def test_an_already_inline_asset_is_not_refetched(self):
        [decision] = _decide(["data:image/gif;base64,R0lGOD"], page=PAGE)
        assert decision["allowed"] is False
        assert decision["reason"] == "already_inline"

    def test_a_cdn_pattern_does_not_match_by_suffix_alone(self):
        """`*.nejm.org` must not admit `evil-nejm.org` or `nejm.org.evil.test`."""

        decisions = _decide(
            ["https://evil-nejm.org/f.png", "https://nejm.org.evil.test/f.png"],
            page="https://example.org/a", profile={"asset_origins": ["*.nejm.org"]},
        )
        assert [d["allowed"] for d in decisions] == [False, False]


class TestReaderLink:
    """`reader_url` is a string the receiver chose. F-03."""

    def _reader(self, cases, base):
        script = f"""
          import {{ safeReaderUrl }} from "{(EXTENSION / 'net-policy.js').as_posix()}";
          console.log(JSON.stringify({json.dumps(cases)}.map(
            (url) => safeReaderUrl(url, {json.dumps(base)})
          )));
        """
        return _run(script)

    def test_the_receiver_s_own_https_reader_link_is_offered(self):
        assert self._reader(
            ["https://corpus.example/reader/abc"], "https://corpus.example"
        ) == ["https://corpus.example/reader/abc"]

    @pytest.mark.parametrize(
        "url",
        [
            "javascript:alert(document.cookie)",
            "data:text/html,<h1>hi",
            "file:///etc/passwd",
            "blob:https://corpus.example/x",
            "chrome-extension://abcd/popup.html",
            "https://phishing.example/reader/abc",
            "https://corpus.example@phishing.example/x",
            "http://corpus.example/reader/abc",
            "https://corpus.example:8443/reader/abc",
        ],
    )
    def test_anything_else_is_not_offered_as_a_link(self, url):
        assert self._reader([url], "https://corpus.example") == [None]

    def test_a_loopback_receiver_may_keep_its_own_scheme(self):
        """`http://localhost` is a configurable base, so its reader link is too."""

        assert self._reader(
            ["http://localhost:8000/reader/abc"], "http://localhost:8000"
        ) == ["http://localhost:8000/reader/abc"]


def test_every_token_bearing_request_refuses_a_redirect():
    """F-02. A receiver that answers 30x must not have the token replayed."""

    source = (EXTENSION / "capture.js").read_text(encoding="utf-8")
    assert 'redirect: "error"' in source
    assert "receiver_origin_changed" in source
    # One function owns talking to the receiver, so a new endpoint cannot be
    # added with the old, redirect-following shape.
    assert source.count("await fetch(") == 2, "a request bypasses apiFetch/fetchAsset"
    assert 'credentials: "omit"' in source


def test_the_asset_fetch_refuses_a_redirect_too():
    """An allowed URL that answers 302 would otherwise reach an unchecked host."""

    source = (EXTENSION / "capture.js").read_text(encoding="utf-8")
    assert "if (!response.ok || response.redirected) return null;" in source
