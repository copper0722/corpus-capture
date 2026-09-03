"""Choose an article's FIGURES structurally, never by how many bytes they weigh.

Purpose
-------
Picking images by byte size pulls in a pile of adverts. Measured on an NEJM
Case Challenge capture, a size-based pick returned five promos -- cover art, a
Guideline Watch banner, logos -- and not one article figure. The
article's own figures were sitting in the body as
``<figure id="f1" class="graphic">``, which is a fact about the page's STRUCTURE
that no size heuristic can see. A big image is a big image; only its position in
the document says whether it belongs to the article.

So selection is structural in three ordered rungs, and each is evidence of a
different strength:

1. the publisher's own figure elements, named by the profile registry;
2. any ``<figure>`` element inside the article container;
3. an ``<img>`` whose ``alt`` names itself a figure.

Rung 3 needs one detail the obvious regex gets wrong: NEJM writes
``alt="Figure1"`` with no space, so ``^(Figure|Table)\\b`` does not match --
``e`` and ``1`` are both word characters, so there is no boundary between them.
That single missing case is the difference between three figures and none.

Inputs
------
The captured markup, the resolved publisher profile, and the page's base URL.

Outputs
-------
A manifest of the article's figures (id, label, caption, asset URL, byte hash,
document position) plus the decorative set: every other image, kept for page
fidelity and flagged so nothing downstream adopts it.

State changes
-------------
None. This module reads markup and returns a description of it.

Failure behavior
----------------
A page with no recognizable figures returns an empty manifest, never a guess.
Unparseable markup raises; a caller that cannot parse a page has not captured it.

Public entrypoints
------------------
``build_figure_manifest()``, ``figure_label()``, ``is_decorative_asset()``.

Related tests
-------------
``tests/test_figure_manifest.py``.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urljoin, urlsplit

#: `Figure1`, `Figure 1`, `Fig. 2`, `Table 1`, `Panel A`, `eFigure 3`.
#: No `\b` after the word: NEJM writes `Figure1`, and `\b` does not match between
#: two word characters. Getting this wrong returns zero figures on a page that
#: has three, which is exactly what the first measurement showed.
_LABEL_RE = re.compile(
    r"^\s*(supplementary\s+figure|efigure|etable|figure|fig\.?|table|panel|chart|scheme)"
    r"\s*([0-9]+|[ivxlc]+|[a-z])?\b",
    re.I,
)
#: An `id` a publisher gives a figure element: f1/fig2/t3/tbl1.
_ID_LABEL_RE = re.compile(
    r"^(?P<kind>figure|fig|f|table|tbl|tab|t)[-_]?(?P<number>\d+)$", re.I
)
_KIND_WORDS = {"f": "Figure", "fig": "Figure", "figure": "Figure",
               "t": "Table", "tab": "Table", "tbl": "Table", "table": "Table"}
#: How much caption text is worth carrying. A caption is a sentence or two; a
#: page that puts an entire methods section in one is not describing a figure.
MAX_CAPTION_CHARS = 2000


@dataclass(frozen=True, slots=True)
class FigureRecord:
    """One figure the article itself declares."""

    figure_id: str
    label: str
    caption: str
    asset_url: str
    asset_sha256: str
    position: int
    selector: str
    alt: str = ""
    embedded: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class FigureManifest:
    """What the article declares, and what merely shares the page with it."""

    profile_id: str
    figures: tuple[FigureRecord, ...] = ()
    decorative: tuple[dict[str, Any], ...] = ()
    container_selector: str = ""

    @property
    def labels(self) -> tuple[str, ...]:
        return tuple(figure.label for figure in self.figures)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "corpus-capture-figures-v1",
            "profile": self.profile_id,
            "container_selector": self.container_selector,
            "figure_count": len(self.figures),
            "decorative_count": len(self.decorative),
            "figures": [figure.as_dict() for figure in self.figures],
            "decorative": list(self.decorative),
        }


def parse_markup(markup: str):
    """One parser, chosen the way the repository's other HTML readers choose it.

    lxml when it is installed, html.parser otherwise. Public because every
    consumer of this module must read a page the SAME way: two parsers disagree
    about malformed markup, and publisher markup is malformed.
    """
    from bs4 import BeautifulSoup

    try:
        return BeautifulSoup(markup, "lxml")
    except Exception:
        return BeautifulSoup(markup, "html.parser")


def figure_label(*, element_id: str = "", alt: str = "", caption: str = "") -> str:
    """The label a reader would call this figure, or "".

    Three sources, weakest last. The element id is the publisher's own key and is
    stable across renders; alt is what the page tells assistive technology; the
    caption's opening words are a last resort because a caption may begin with
    prose that merely mentions a figure.
    """

    match = _ID_LABEL_RE.match((element_id or "").strip())
    if match:
        kind = _KIND_WORDS.get(match.group("kind").lower(), "Figure")
        return f"{kind} {int(match.group('number'))}"
    for text in (alt, caption):
        found = _LABEL_RE.match((text or "").strip())
        if found:
            word = found.group(1).strip().rstrip(".").title()
            word = {"Fig": "Figure", "Efigure": "eFigure", "Etable": "eTable"}.get(word, word)
            number = (found.group(2) or "").strip()
            return f"{word} {number}".strip() if number else word
    return ""


def is_decorative_asset(url: str, *, patterns: tuple[str, ...] = ()) -> bool:
    """True for an asset whose PATH says it is furniture, not content.

    Only ever used to explain a decorative classification, never to demote
    something the article structurally declared: a figure inside the article body
    stays a figure even if the publisher happens to serve it from a path with
    `banner` in it.
    """

    lowered = (url or "").lower()
    return any(pattern.lower() in lowered for pattern in patterns if pattern)


def _text(node) -> str:
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)) if node else ""


def _asset_url(img, base_url: str) -> str:
    raw = (img.get("src") or img.get("data-src") or "").strip()
    if not raw or raw.startswith("data:"):
        # An already-embedded figure has no remote URL left to record. The
        # manifest must be built from the page BEFORE embedding rewrites src,
        # which is why the capture script builds it first.
        return ""
    return urljoin(base_url, raw) if base_url else raw


def _select(root, selectors) -> list:
    seen: list = []
    for selector in selectors:
        try:
            found = root.select(selector)
        except Exception:  # a publisher selector may be invalid
            continue
        for node in found:
            if node not in seen:
                seen.append(node)
    return seen


def find_article_container(soup, selectors) -> tuple[Any, str]:
    """The element that holds the article, and the selector that found it.

    Serializing the whole document is what carries the adverts, the modals and
    the marketing rails into the artifact. The container is the boundary; when
    no selector matches, the caller keeps the whole body and says so, because a
    silent full-page capture that claims to be scoped is worse than an honest
    unscoped one.
    """

    for selector in selectors:
        try:
            node = soup.select_one(selector)
        except Exception:
            continue
        if node is not None and len(_text(node)) > 400:
            return node, selector
    return (soup.body or soup), ""


def build_figure_manifest(
    markup: str,
    *,
    profile: dict[str, Any],
    base_url: str = "",
    asset_bytes: dict[str, bytes] | None = None,
) -> FigureManifest:
    """Select the article's figures structurally and classify everything else.

    ``asset_bytes`` maps an asset URL to the bytes the capture fetched, so the
    manifest can carry the hash of what was actually stored rather than a hash of
    a URL. Absent bytes leave the hash empty; an empty hash is an honest "not
    measured" and never a placeholder that looks like one.
    """

    soup = parse_markup(markup)
    container, container_selector = find_article_container(
        soup, profile.get("article_container_selectors") or []
    )
    figure_selectors = tuple(profile.get("figure_selectors") or ("figure",))
    caption_selectors = tuple(profile.get("caption_selectors") or ("figcaption",))
    decorative_patterns = tuple(profile.get("decorative_asset_patterns") or ())
    drop_patterns = tuple(profile.get("drop_asset_patterns") or ())
    drop_hosts = tuple(profile.get("drop_asset_hosts") or ())

    # Document order, computed once. A figure's position is where it sits in the
    # article, not the order the selectors happened to find it: iterating
    # `figure.graphic` before `figure.table` reported Figure 1, Figure 2, Table 1
    # for a page whose table comes first.
    order = {id(node): index for index, node in enumerate(soup.find_all(True))}

    candidates: list[tuple[int, int, FigureRecord]] = []
    claimed: set[int] = set()

    def record(node, img, selector: str, rank: int) -> None:
        if id(img) in claimed:
            return
        # Every caption selector, joined in order, not the first that matches.
        # PMC puts the LABEL in `<h3 class="obj_head">Fig. 1.</h3>` and the
        # description in a separate element; taking only the first match gets a
        # label with no caption or a caption with no label, and the label is what
        # decides whether this is a figure at all.
        parts: list[str] = []
        for caption_selector in caption_selectors:
            try:
                found = node.select(caption_selector)
            except Exception:
                found = []
            for element in found:
                text = _text(element)
                if text and text not in parts:
                    parts.append(text)
        caption = " ".join(parts)[:MAX_CAPTION_CHARS]
        alt = (img.get("alt") or "").strip()
        element_id = (node.get("id") or img.get("id") or "").strip()
        label = figure_label(element_id=element_id, alt=alt, caption=caption)
        if not label:
            return
        url = _asset_url(img, base_url)
        payload = (asset_bytes or {}).get(url)
        claimed.add(id(img))
        candidates.append((
            rank,
            order.get(id(img), order.get(id(node), len(order))),
            FigureRecord(
                figure_id=element_id or f"{label.split()[0].lower()}{len(candidates) + 1}",
                label=label,
                caption=caption,
                asset_url=url,
                asset_sha256=hashlib.sha256(payload).hexdigest() if payload else "",
                position=0,
                selector=selector,
                alt=alt,
                embedded=bool(payload) or (img.get("src") or "").startswith("data:"),
            ),
        ))

    # Rung 1 and 2: elements the publisher marked as figures, inside the article.
    for selector in figure_selectors:
        for node in _select(container, (selector,)):
            image = node.find("img")
            if image is not None:
                record(node, image, selector, 0)

    # Rung 3: an image that names itself, anywhere in the article container. This
    # is what catches a publisher that ships figures as bare <img> in a <div>.
    for image in container.find_all("img"):
        if id(image) in claimed:
            continue
        if figure_label(element_id=(image.get("id") or ""), alt=(image.get("alt") or "")):
            record(image.parent if image.parent is not None else image, image, "img[alt]", 1)

    # One label, one figure. A publisher renders the same table twice -- once in
    # the body and once as a thumbnail in a rail -- and both images carry
    # alt="Table1"; listing it twice would make the manifest disagree with the
    # article. The declared element wins over the alt fallback, and a captioned
    # candidate wins over a bare one, because both are the stronger evidence.
    best: dict[str, tuple[int, int, FigureRecord]] = {}
    for rank, index, figure in candidates:
        current = best.get(figure.label)
        if current is None or (rank, -len(figure.caption)) < (current[0], -len(current[2].caption)):
            best[figure.label] = (rank, index, figure)
    figures = [
        FigureRecord(**{**item[2].as_dict(), "position": position})
        for position, item in enumerate(sorted(best.values(), key=lambda item: item[1]))
    ]

    decorative: list[dict[str, Any]] = []
    for image in soup.find_all("img"):
        if id(image) in claimed:
            continue
        url = _asset_url(image, base_url)
        host = urlsplit(url).hostname or ""
        reason = (
            "blocked_asset_host" if any(host.endswith(h) for h in drop_hosts if h)
            else "blocked_asset_path" if is_decorative_asset(url, patterns=drop_patterns)
            else "known_furniture" if is_decorative_asset(url, patterns=decorative_patterns)
            else "outside_article_container"
            if container is not soup and image not in container.find_all("img")
            else "unlabelled"
        )
        decorative.append({
            "asset_url": url,
            "alt": (image.get("alt") or "").strip()[:200],
            "reason": reason,
        })

    return FigureManifest(
        profile_id=str(profile.get("id") or "generic"),
        figures=tuple(figures),
        decorative=tuple(decorative),
        container_selector=container_selector,
    )


#: Kept for callers that want the drop list without loading the whole registry.
DEFAULT_DROP_SELECTORS: tuple[str, ...] = (
    "script", "noscript", "iframe", "frame", "object", "embed", "template",
)
_ = field  # dataclasses.field is re-exported for callers building profiles
