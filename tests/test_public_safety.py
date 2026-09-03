"""This repository is public. These are the things that must never be in it.

The extension was written inside a private operations repository, against one
particular receiver on one particular private network. None of that is useful
to anyone else, and a hardcoded internal hostname is a disclosure of where one
person's corpus lives. The scrub was done by hand once; this test is what keeps
it done, because the next contributor will not remember the list.

Two lists, because they have different scopes. Deployment markers are forbidden
everywhere. Identity markers are forbidden in everything except the fixtures,
which are skeletons of real published articles and legitimately carry publisher
URLs and clinical prose -- `copper intrauterine devices` is a contraceptive, not
an operator.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
#: This file spells every forbidden string out loud. It is the one file that
#: must not be scanned, or the scanner always reports itself.
SELF = Path(__file__).resolve().relative_to(ROOT).as_posix()

#: Anything that would identify the receiver, its network, or its secrets.
#: Forbidden in every tracked file, fixtures included.
DEPLOYMENT_MARKERS = [
    "tailcb6bd8", "tailcb", ".ts.net", "100.96.",
    "/home/copper", "_admin-private", "agent-share", "inbox-drain",
    "CORPUS_WEB_MUTATION_TOKEN", "CORPUS_WEB_PRIVATE_TOKEN",
    "PGSERVICE", "vault_main", "BEGIN OPENSSH PRIVATE KEY", "BEGIN RSA PRIVATE KEY",
]
#: Anything that names the operator, their machines, or the private tracker the
#: work was planned in. Skipped for fixtures.
IDENTITY_PATTERNS = [
    # The GitHub account that owns this repository is public and allowed;
    # anything else spelled with that name is a leak from the private tree.
    (r"copper(?!0722)", "the operator's name outside the public account handle"),
    (r"/data/", "an absolute path from the private host"),
    (r"#1[0-9]{3}(?![0-9a-fA-F])", "a private tracker card number"),
    (r"\u738b\u4ecb\u7acb", "the operator's name in Chinese"),
    (r"\b(?:cu5|hm4|hmj|cm1|mbp|mba|boa|boan)\b", "a private host name"),
]
#: Files whose bytes are a skeleton of a published article. They carry publisher
#: URLs and clinical text by design.
FIXTURE_PREFIX = "fixtures/"
BINARY_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".woff2"}


#: Directories git would never publish. The fallback walk has to skip them by
#: hand: a stale `.pyc` of THIS file carries every forbidden string in it.
GENERATED_PREFIXES = (".git/", "__pycache__/", ".pytest_cache/", ".ruff_cache/",
                      ".venv/", "node_modules/", "dist/", "build/")


def _generated(name: str) -> bool:
    return any(part + "/" in name + "/" for part in
               (prefix.rstrip("/") for prefix in GENERATED_PREFIXES))


def _tracked_files() -> list[str]:
    """Every file git would publish. Untracked scratch is not shipped."""

    result = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=False
    )
    if result.returncode != 0 or not result.stdout.strip():
        # Before the first commit there is nothing tracked; fall back to a walk
        # so the test is meaningful when it matters most -- before the push.
        names = [
            path.relative_to(ROOT).as_posix()
            for path in ROOT.rglob("*")
            if path.is_file() and not _generated(path.relative_to(ROOT).as_posix())
        ]
    else:
        names = [line for line in result.stdout.splitlines() if line.strip()]
    return [name for name in names if name != SELF]


def _text_files() -> list[str]:
    return [
        name for name in _tracked_files()
        if Path(name).suffix.lower() not in BINARY_SUFFIXES
    ]


def test_there_is_something_to_scan():
    """A scan that found no files would pass by being empty."""

    names = _text_files()
    assert len(names) > 15, names
    assert "extension/capture.js" in names
    assert any(name.startswith(FIXTURE_PREFIX) for name in names)


@pytest.mark.parametrize("marker", DEPLOYMENT_MARKERS)
def test_no_file_names_the_receiver_or_its_network(marker: str):
    hits = []
    for name in _text_files():
        body = (ROOT / name).read_text(encoding="utf-8", errors="ignore")
        if marker.lower() in body.lower():
            hits.append(name)
    assert not hits, f"{marker!r} appears in {hits}"


@pytest.mark.parametrize(("pattern", "what"), IDENTITY_PATTERNS)
def test_no_source_file_names_the_operator(pattern: str, what: str):
    compiled = re.compile(pattern, re.I)
    hits = []
    for name in _text_files():
        if name.startswith(FIXTURE_PREFIX):
            continue
        body = (ROOT / name).read_text(encoding="utf-8", errors="ignore")
        for match in compiled.finditer(body):
            line = body.count("\n", 0, match.start()) + 1
            hits.append(f"{name}:{line}: {match.group(0)!r}")
    assert not hits, f"{what}: {hits}"


def test_no_source_file_carries_an_email_address():
    """Checked after URLs are removed, because a URL is not a mailbox.

    `https://user:pw@host/` and `https://corpus.example@phishing.example/x` are
    both test fixtures for the network policy, and both look exactly like an
    address to a naive pattern. Stripping URLs first keeps the check sharp
    instead of teaching everyone to add exceptions to it.
    """

    pattern = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
    hits = []
    for name in _text_files():
        if name.startswith(FIXTURE_PREFIX):
            continue
        body = (ROOT / name).read_text(encoding="utf-8", errors="ignore")
        body = re.sub(r"[a-z][a-z0-9+.-]*://\S+", " ", body, flags=re.I)
        for match in pattern.finditer(body):
            line = body.count("\n", 0, match.start()) + 1
            hits.append(f"{name}:{line}: {match.group(0)!r}")
    assert not hits, hits


def test_the_extension_ships_no_endpoint_and_no_credential():
    """The receiver is the user's. Nothing here may point at anyone else's."""

    for name in _text_files():
        if not name.startswith("extension/"):
            continue
        body = (ROOT / name).read_text(encoding="utf-8", errors="ignore")
        assert "Bearer " not in body
        for host in re.findall(r"https://([A-Za-z0-9.-]+)", body):
            assert host.endswith("example") or host.endswith(".example") or host in {
                "doi.org", "dx.doi.org", "www.w3.org", "developer.chrome.com",
            }, f"{name} points at {host}"


def test_every_fixture_declares_itself_a_skeleton():
    """A fixture is the page's SHAPE. Restricted full text never enters Git."""

    fixtures = [name for name in _text_files() if name.startswith(FIXTURE_PREFIX)]
    assert fixtures
    for name in fixtures:
        body = (ROOT / name).read_text(encoding="utf-8")
        assert 'name="x-corpus-fixture"' in body, name


def test_no_fixture_carries_more_than_a_first_sentence_per_block():
    """The reducer's output, asserted -- not the reducer's intention.

    Measured 2026-09-03: reducing only `<p>` and `<li>` left ten thousand
    characters of a subscription article in the `<div>`s a publisher actually
    used, which is a copy of the article wearing a fixture's name. The check is
    on the committed bytes, so it fails whoever regenerates a fixture with a
    reducer that misses a tag.
    """

    from bs4 import Comment, NavigableString
    from corpus_capture.figure_manifest import parse_markup

    for name in [n for n in _text_files() if n.startswith(FIXTURE_PREFIX)]:
        soup = parse_markup((ROOT / name).read_text(encoding="utf-8"))
        for element in soup.find_all(True):
            direct = " ".join(
                str(child)
                for child in element.children
                if isinstance(child, NavigableString) and not isinstance(child, Comment)
            ).strip()
            assert len(direct.split()) <= 25, f"{name}: {direct[:80]!r}"
