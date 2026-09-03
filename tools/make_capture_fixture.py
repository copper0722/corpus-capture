#!/usr/bin/env python3
"""Derive a committable SKELETON fixture from a real publisher capture.

Restricted full text never enters Git, so a fixture may not be the capture. What
it may be is the page's SHAPE: the `<head>` metadata verbatim (that is
bibliographic metadata, not payload), the article container with each paragraph
reduced to its first sentence and capped, every figure element kept with its
caption and a 1x1 placeholder image, and the adverts and modals removed.

That is enough to assert what the tests assert -- container found, identity
parsed, figure manifest and decorative count -- and not enough to be a copy of
the article.

Re-running it over an EXISTING fixture is how a leak gets closed when the
original capture is long gone: the reducer is idempotent, so a fixture may be
its own source.

Usage:
  make_capture_fixture.py --profile nejm --source <capture.html> --url <url> \
      --out fixtures/capture-profiles/nejm.html
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

from bs4 import Comment, NavigableString
from corpus_capture.figure_manifest import find_article_container, parse_markup
from corpus_capture.profiles import profile_for_url

#: A 1x1 transparent GIF. Small enough to be obviously not the figure, present
#: enough that the element still parses as an image with an alt and a caption.
PLACEHOLDER = (
    "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
)
MAX_SENTENCE_WORDS = 20


def _first_sentence(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""
    sentence = re.split(r"(?<=[.!?。！？])\s", text, maxsplit=1)[0]  # noqa: RUF001
    words = sentence.split()
    return " ".join(words[:MAX_SENTENCE_WORDS])


def _reduce_prose(container) -> None:
    """Cut every text-bearing element down to its first sentence.

    Restricting this to `<p>` and `<li>` is what let a fixture keep an article.
    Measured 2026-09-03 on the NEJM skeleton: NEJM lays its case description out
    in `<div>`s, so 310 characters sat in paragraphs and 10,000 sat in divs the
    reducer never looked at -- a fixture that was, in substance, the article.

    So the unit is any element holding DIRECT text, whatever its tag. Element
    children are left alone: the figure elements, their images and their
    captions are the shape the fixture exists to carry, and a caption's first
    sentence is what the figure manifest tests read.
    """

    for element in container.find_all(True):
        if element.name in ("script", "style", "img", "br"):
            continue
        # A node that HOLDS the figure image is structure, not payload. PMC wraps
        # its figure image in `<p class="img-box">`; clearing that paragraph
        # deleted the image the fixture exists to carry.
        if element.find(["img", "figure", "table"]) is not None:
            continue
        strings = [child for child in element.children if isinstance(child, NavigableString)]
        joined = " ".join(str(child) for child in strings)
        if not joined.strip():
            continue
        reduced = _first_sentence(joined)
        for child in strings[1:]:
            child.extract()
        strings[0].replace_with(NavigableString(reduced))


def build(source: Path, url: str, profile_id: str) -> str:

    soup = parse_markup(source.read_text(encoding="utf-8", errors="ignore"))
    profile = profile_for_url(url)

    head = soup.head
    if head is not None:
        for node in head.find_all(["script", "style", "noscript", "link"]):
            if node.name == "link" and (node.get("rel") or [""])[0].lower() == "canonical":
                continue
            node.decompose()

    container, _selector = find_article_container(soup, profile["article_container_selectors"])
    # A page comment is vendor furniture -- tag-manager markers, prehiding
    # snippets, dead markup -- and it is text nobody reads that no reducer sees.
    for comment in container.find_all(string=lambda node: isinstance(node, Comment)):
        comment.extract()
    for node in container.find_all(
        ["script", "noscript", "iframe", "frame", "object", "embed", "template"]
    ):
        node.decompose()
    for drop in profile.get("drop_selectors") or []:
        try:
            for node in container.select(drop):
                node.decompose()
        except Exception:
            continue

    _reduce_prose(container)

    for image in container.find_all("img"):
        image["src"] = PLACEHOLDER
        for attribute in ("srcset", "sizes", "data-src", "style", "width", "height"):
            image.attrs.pop(attribute, None)

    body = soup.new_tag("body")
    body.append(container)
    document = parse_markup(f"<!doctype html><html>{head or '<head></head>'}</html>")
    document.html.append(body)
    # Re-running the reducer over an existing fixture is how a leak gets closed
    # without the original capture; one banner per document, not one per pass.
    for stale in document.find_all("meta", attrs={"name": "x-corpus-fixture"}):
        stale.decompose()
    banner = document.new_tag("meta")
    banner.attrs["name"] = "x-corpus-fixture"
    banner.attrs["content"] = (
        f"skeleton for profile {profile_id}; body reduced to first sentences, "
        "figures kept with placeholder images"
    )
    (document.head or document.html).append(banner)
    return document.prettify()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--url", required=True)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(build(args.source, args.url, args.profile), encoding="utf-8")
    print(f"wrote {args.out} ({args.out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
