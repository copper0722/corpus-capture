"""Figures are chosen by STRUCTURE. The defect this pins is a size heuristic.

Measured on the NEJM Case Challenge capture: picking embedded images by byte
size returned five promos -- cover art, a Guideline Watch banner, logos -- and
zero article figures. The article's three figures were `<figure id="t1">`,
`<figure id="f1">` and `<figure id="f2">` in the body, which is a fact about the
document that no size comparison can reach.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from corpus_capture.figure_manifest import (
    build_figure_manifest,
    figure_label,
    find_article_container,
    is_decorative_asset,
    is_unnumbered_asset,
    parse_markup,
)
from corpus_capture.profiles import fixture_path, load_registry, profile_for_url

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "capture-profiles"
NEJM_URL = "https://www.nejm.org/do/10.1056/NEJMdo008670/full/"
LWW_URL = "https://www.ovid.com/jnls/kidney360/fulltext/10.34067/kid.0000001134~x"
PMC_URL = "https://pmc.ncbi.nlm.nih.gov/articles/PMC10589027/"


def _manifest(fixture: str, url: str):
    markup = (FIXTURES / fixture).read_text(encoding="utf-8")
    return build_figure_manifest(markup, profile=profile_for_url(url), base_url=url)


class TestLabelRecognition:
    """The one detail that decided three figures versus none."""

    @pytest.mark.parametrize(
        ("alt", "expected"),
        [
            # NEJM writes the number with NO space. `^(Figure|Table)\b` does not
            # match this, because `e` and `1` are both word characters.
            ("Figure1", "Figure 1"),
            ("Table1", "Table 1"),
            ("Figure 2", "Figure 2"),
            ("Fig. 3", "Figure 3"),
            ("Panel A", "Panel A"),
            ("eFigure 4", "eFigure 4"),
            ("Supplementary Figure 2", "Supplementary Figure 2"),
        ],
    )
    def test_a_self_naming_image_is_recognized(self, alt, expected):
        assert figure_label(alt=alt) == expected

    @pytest.mark.parametrize(
        "alt",
        [
            "NEJM logo",
            "Subscribe today",
            "Guideline Watch banner",
            "",
            "A photograph of the ward",
            "Figurative language in medicine",
        ],
    )
    def test_furniture_and_prose_are_not_labels(self, alt):
        assert figure_label(alt=alt) == ""

    @pytest.mark.parametrize(
        ("element_id", "expected"),
        [("f1", "Figure 1"), ("t1", "Table 1"), ("fig2", "Figure 2"),
         ("tbl3", "Table 3"), ("F10", "Figure 10")],
    )
    def test_the_publisher_key_is_the_strongest_source(self, element_id, expected):
        assert figure_label(element_id=element_id) == expected

    def test_an_id_that_is_not_a_figure_key_says_nothing(self):
        assert figure_label(element_id="footer") == ""
        assert figure_label(element_id="t") == ""


class TestNejmCaseChallenge:
    """The page the size heuristic was first measured against."""

    def test_the_manifest_is_the_article_and_only_the_article(self):
        manifest = _manifest("nejm.html", NEJM_URL)
        assert manifest.profile_id == "nejm"
        assert manifest.labels == ("Table 1", "Figure 1", "Figure 2")

    def test_the_asset_stems_are_the_publisher_s_own(self):
        """t1/f1/f2, in document order -- the table comes first on this page."""
        manifest = _manifest("nejm.html", NEJM_URL)
        assert [figure.figure_id for figure in manifest.figures] == ["t1", "f1", "f2"]
        assert [figure.position for figure in manifest.figures] == [0, 1, 2]

    def test_every_figure_carries_the_caption_a_reader_sees(self):
        manifest = _manifest("nejm.html", NEJM_URL)
        captions = {figure.label: figure.caption for figure in manifest.figures}
        assert captions["Table 1"].startswith("Laboratory Data")
        assert "CT Images of the Chest" in captions["Figure 1"]
        assert "Initial MRI of the Head" in captions["Figure 2"]

    def test_one_label_is_one_figure(self):
        """A table rendered twice must not be listed twice."""
        manifest = _manifest("nejm.html", NEJM_URL)
        assert len(manifest.labels) == len(set(manifest.labels))

    def test_the_article_container_is_found(self):
        assert _manifest("nejm.html", NEJM_URL).container_selector != ""


class TestLwwArticle:
    """Ovid serves the platform's name as og:site_name; the figures are plain."""

    def test_the_container_and_figures_resolve(self):
        manifest = _manifest("lww.html", LWW_URL)
        assert manifest.profile_id == "lww"
        assert manifest.container_selector != ""
        assert manifest.labels == ("Figure 1",)


class TestPmcArticle:
    """Open access, and the id rung does not fire: the label comes from the alt."""

    def test_the_declared_figure_is_found_through_the_caption_rung(self):
        manifest = _manifest("pmc.html", PMC_URL)
        assert manifest.profile_id == "pmc"
        assert manifest.container_selector != ""
        assert manifest.labels == ("Figure 1",)
        assert "Chest x-ray" in manifest.figures[0].caption

    def test_a_publisher_suffixed_id_does_not_break_the_label(self):
        """`f1-jchim-13-04-074` is not `f1`; the alt and caption still name it."""

        manifest = _manifest("pmc.html", PMC_URL)
        assert manifest.figures[0].figure_id.startswith("f1-")


class TestDecorativeClassification:
    """Everything else is kept for fidelity and flagged so nothing adopts it."""

    MARKUP = """
    <html><head><title>t</title></head><body>
      <img src="https://at.nejm.org/tracking/pixel.gif" alt="">
      <img src="/pb-assets/nejm-site/images/system/NEJM_defaultlogo.png" alt="NEJM">
      <img src="/sda/MTECH/banner.jpg" alt="Guideline Watch">
      <main>
        <p>%s</p>
        <figure id="f1" class="graphic">
          <img src="/cms/asset/abc/nejmcpc2603180_f1.jpg" alt="Figure1">
          <figcaption>A real figure.</figcaption>
        </figure>
        <img src="/promo/subscribe-banner.png" alt="Subscribe">
      </main>
    </body></html>
    """ % ("word " * 120)

    def test_the_article_figure_is_selected_and_the_promos_are_not(self):
        profile = profile_for_url("https://www.nejm.org/doi/full/10.1056/x")
        manifest = build_figure_manifest(
            self.MARKUP, profile=profile, base_url="https://www.nejm.org/doi/full/10.1056/x"
        )
        assert manifest.labels == ("Figure 1",)
        assert len(manifest.decorative) == 4

    def test_each_decorative_image_says_why(self):
        profile = profile_for_url("https://www.nejm.org/doi/full/10.1056/x")
        manifest = build_figure_manifest(
            self.MARKUP, profile=profile, base_url="https://www.nejm.org/doi/full/10.1056/x"
        )
        reasons = {item["alt"]: item["reason"] for item in manifest.decorative}
        assert reasons["Guideline Watch"] == "blocked_asset_path"
        assert reasons["Subscribe"] in {"known_furniture", "unlabelled"}
        assert "" in reasons  # the tracking pixel keeps its empty alt

    def test_a_declared_figure_survives_a_furniture_shaped_path(self):
        """Structure decides. A figure served from /banner/ is still a figure."""

        markup = """
        <html><body><main><p>%s</p>
          <figure id="f1" class="graphic">
            <img src="/banner/figure-one.jpg" alt="Figure1">
            <figcaption>Still the article's figure.</figcaption>
          </figure>
        </main></body></html>
        """ % ("word " * 120)
        manifest = build_figure_manifest(
            markup, profile=profile_for_url("https://www.nejm.org/x"),
            base_url="https://www.nejm.org/x",
        )
        assert manifest.labels == ("Figure 1",)

    def test_a_page_with_no_figures_manifests_nothing(self):
        markup = "<html><body><main><p>%s</p></main></body></html>" % ("word " * 120)
        manifest = build_figure_manifest(markup, profile=profile_for_url("https://x.test/a"))
        assert manifest.figures == ()

    def test_the_path_test_never_runs_on_an_empty_pattern_set(self):
        assert is_decorative_asset("https://x.test/logo.png", patterns=()) is False
        assert is_decorative_asset("https://x.test/logo.png", patterns=("/logo",)) is True


class TestArticleContainer:
    """Serializing the whole document is what carries the adverts in."""

    def test_a_short_match_is_not_the_article(self):
        """A nav <main> with three words must not be mistaken for the body."""

        soup = parse_markup("<html><body><main>Skip to content</main>"
                            "<article><p>%s</p></article></body></html>" % ("word " * 200))
        _node, selector = find_article_container(soup, ["main", "article"])
        assert selector == "article"

    def test_no_match_says_so_rather_than_pretending(self):
        soup = parse_markup("<html><body><div>%s</div></body></html>" % ("word " * 200))
        _unused, selector = find_article_container(soup, ["#nothing-here"])
        assert selector == ""


ELSEVIER_URL = "https://www.sciencedirect.com/science/article/pii/S0140673626016399"
#: The shape that produced the defect, reduced to what matters: an id whose
#: number is an internal counter, an `-fx` asset, an empty alt, no caption
#: element at all, and the description behind `aria-describedby`.
ELSEVIER_GRAPHICAL_ABSTRACT = """
<html><body><div id="body"><p>Prose.</p>
<figure class="figure" id="f10">
  <span><img src="https://ars.els-cdn.com/content/image/1-s2.0-S0140673626016399-fx1.jpg"
             alt="" aria-describedby="alt11"></span>
  <div id="alt11" class="figure-description u-display-none">Lipoprotein A particle,
  illustration. Lipoprotein(a) is a low-density lipoprotein.</div>
</figure></div></body></html>
"""
#: The over-correction control: a NUMBERED Elsevier figure, served as `-gr2`,
#: on the same profile. Its number must survive.
ELSEVIER_NUMBERED_FIGURE = """
<html><body><div id="body"><p>Prose.</p>
<figure class="figure" id="f2">
  <span><img src="https://ars.els-cdn.com/content/image/1-s2.0-S0140673626016399-gr2.jpg"
             alt="" aria-describedby="alt3"></span>
  <div id="alt3">Kaplan-Meier curves for the primary endpoint.</div>
</figure></div></body></html>
"""


class TestUnnumberedDisplayItem:
    """A number nobody can see is worse than no number: a reader cites it.

    Measured 2026-09-11 on two Lancet Comments captured through ScienceDirect.
    Each has exactly ONE image -- the graphical abstract -- in
    `<figure id="f10">` with the asset `...-fx1.jpg`, and the manifest called it
    "Figure 10". The page prints no number anywhere.
    """

    def _figures(self, markup: str):
        return build_figure_manifest(
            markup,
            profile=profile_for_url(ELSEVIER_URL),
            base_url=ELSEVIER_URL,
        ).figures

    def test_the_unnumbered_item_keeps_the_word_and_drops_the_number(self):
        figures = self._figures(ELSEVIER_GRAPHICAL_ABSTRACT)
        assert [f.label for f in figures] == ["Figure"]
        # Still a full record, not dropped: an empty label would remove it.
        assert figures[0].figure_id == "f10"
        assert figures[0].asset_url.endswith("-fx1.jpg")

    def test_a_numbered_figure_on_the_same_profile_keeps_its_number(self):
        """The over-correction control."""

        assert [f.label for f in self._figures(ELSEVIER_NUMBERED_FIGURE)] == ["Figure 2"]

    def test_the_pattern_is_profile_data_not_a_rule_in_the_code(self):
        assert profile_for_url(ELSEVIER_URL)["unnumbered_asset_patterns"] == ["-fx"]
        # Another publisher's profile does not inherit Elsevier's convention.
        assert profile_for_url(NEJM_URL)["unnumbered_asset_patterns"] == []

    @pytest.mark.parametrize(
        ("url", "patterns", "expected"),
        [
            ("https://x/1-s2.0-S1-fx1.jpg", ("-fx",), True),
            ("https://x/1-s2.0-S1-fx1_lrg.jpg", ("-fx",), True),
            ("https://x/1-s2.0-S1-gr1.jpg", ("-fx",), False),
            ("https://x/1-s2.0-S1-fx1.jpg", (), False),
            ("", ("-fx",), False),
        ],
    )
    def test_the_path_test(self, url, patterns, expected):
        assert is_unnumbered_asset(url, patterns=patterns) is expected

    def test_numbered_false_never_empties_a_label(self):
        """An empty label drops the figure; the word alone must come back."""

        assert figure_label(element_id="f10", numbered=False) == "Figure"
        assert figure_label(element_id="t3", numbered=False) == "Table"
        assert figure_label(element_id="footer", numbered=False) == ""


class TestAriaDescribedBy:
    """The description a screen reader is given is the caption a reader wants.

    ScienceDirect hides it in a `u-display-none` div outside every caption
    selector, so a figure that HAS a description was recorded with none.
    """

    def test_the_description_becomes_the_caption(self):
        figures = build_figure_manifest(
            ELSEVIER_GRAPHICAL_ABSTRACT,
            profile=profile_for_url(ELSEVIER_URL),
            base_url=ELSEVIER_URL,
        ).figures
        assert "Lipoprotein A particle" in figures[0].caption

    def test_a_real_caption_wins_over_the_aria_target(self):
        markup = ELSEVIER_GRAPHICAL_ABSTRACT.replace(
            "</figure>", "<figcaption>The caption the page prints.</figcaption></figure>"
        )
        caption = build_figure_manifest(
            markup, profile=profile_for_url(ELSEVIER_URL), base_url=ELSEVIER_URL
        ).figures[0].caption
        assert caption == "The caption the page prints."

    def test_a_dangling_aria_reference_is_not_an_error(self):
        markup = ELSEVIER_GRAPHICAL_ABSTRACT.replace('id="alt11"', 'id="somewhere-else"')
        figures = build_figure_manifest(
            markup, profile=profile_for_url(ELSEVIER_URL), base_url=ELSEVIER_URL
        ).figures
        assert len(figures) == 1
        assert figures[0].caption == ""


def test_every_supported_profile_has_the_fixture_it_claims():
    """`supported` is a measurement; a missing fixture makes it a wish."""

    for profile in load_registry()["profiles"]:
        if profile["status"] != "supported":
            continue
        path = fixture_path(profile)
        assert path is not None and path.is_file(), profile["id"]
