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

#: Three unnumbered figures and one numbered, in document order, as ScienceDirect
#: renders a long guideline: `undfigN` ids, `-fx` assets, no caption, empty alt.
ELSEVIER_UNDFIG_ARTICLE = """
<html><body><div id="body"><p>Prose.</p>
<span class="display"><figure class="figure text-xs" id="undfig1"><span>
  <img src="https://ars.els-cdn.com/content/image/1-s2.0-S0272638619311370-fx1.jpg"
       height="417" alt=""></span></figure></span>
<figure class="figure" id="f1"><span>
  <img src="https://ars.els-cdn.com/content/image/1-s2.0-S0272638619311370-gr1.jpg"
       alt="" aria-describedby="alt1"></span>
  <div id="alt1">The ESKD Life-Plan.</div></figure>
<span class="display"><figure class="figure text-xs" id="undfig2"><span>
  <img src="https://ars.els-cdn.com/content/image/1-s2.0-S0272638619311370-fx2.jpg"
       height="528" alt=""></span></figure></span>
<span class="display"><figure class="figure text-xs" id="undfig3"><span>
  <img src="https://ars.els-cdn.com/content/image/1-s2.0-S0272638619311370-fx3.jpg"
       height="466" alt=""></span></figure></span>
</div></body></html>
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

    def test_unnumbered_figures_with_an_unreadable_id_are_kept_one_each(self):
        """ScienceDirect keys them `undfigN`: no number, no caption, empty alt.

        Measured 2026-09-19 on 10.1053/j.ajkd.2019.12.001 (KDOQI vascular
        access): three such figures -- the CKD heat map and two checklists --
        were filed as decoration, and the receiving capture was refused for
        dropping article images. Beside them, a numbered figure keeps its number.
        """

        figures = self._figures(ELSEVIER_UNDFIG_ARTICLE)
        assert [f.label for f in figures] == ["Figure", "Figure 1", "Figure", "Figure"]
        assert [f.figure_id for f in figures] == ["undfig1", "f1", "undfig2", "undfig3"]
        assert [f.asset_url.rsplit("-", 1)[-1] for f in figures] == [
            "fx1.jpg", "gr1.jpg", "fx2.jpg", "fx3.jpg"]

    def test_the_same_unnumbered_asset_rendered_twice_is_still_one_figure(self):
        twice = ELSEVIER_UNDFIG_ARTICLE.replace("-fx2.jpg", "-fx1.jpg")
        labels = [f.asset_url.rsplit("-", 1)[-1] for f in self._figures(twice)]
        assert labels == ["fx1.jpg", "gr1.jpg", "fx3.jpg"]

    def test_the_extension_selects_the_same_unnumbered_figures(self):
        """The serializer is the twin of this module; they may not disagree."""

        import json
        import shutil
        import subprocess

        root = Path(__file__).resolve().parents[1]
        node = shutil.which("node") or shutil.which("nodejs")
        if node is None or not (root / "node_modules" / "linkedom").is_dir():
            pytest.skip("node or linkedom is not installed")
        profile = json.dumps(profile_for_url(ELSEVIER_URL))
        for markup in (ELSEVIER_UNDFIG_ARTICLE, ELSEVIER_GRAPHICAL_ABSTRACT,
                       ELSEVIER_NUMBERED_FIGURE,
                       ELSEVIER_UNDFIG_ARTICLE.replace("-fx3.jpg", "-box3.jpg")):
            script = f"""
              import {{ parseHTML }} from "linkedom";
              import {{ serializePage }} from "{(root / "extension" / "serialize.js").as_posix()}";
              import {{ LIMITS }} from "{(root / "extension" / "limits.js").as_posix()}";
              const {{ document }} = parseHTML({json.dumps(markup)});
              globalThis.document = document;
              globalThis.location = {{ href: {json.dumps(ELSEVIER_URL)} }};
              const page = serializePage("0123456789abcdef", {profile}, LIMITS);
              console.log(JSON.stringify((page.figures || []).map((f) => [f.label, f.figure_id])));
            """
            result = subprocess.run([node, "--input-type=module", "--eval", script],
                                    capture_output=True, text=True, check=False, cwd=root)
            assert result.returncode == 0, result.stderr
            seen = [tuple(row) for row in json.loads(result.stdout.strip().splitlines()[-1])]
            assert seen == [(f.label, f.figure_id) for f in self._figures(markup)]

    def test_an_unlabelled_figure_with_an_ordinary_asset_stays_out(self):
        """The confusable negative: the boxed-text <figure> is not a display item."""

        boxed = ELSEVIER_UNDFIG_ARTICLE.replace("-fx3.jpg", "-box3.jpg")
        manifest = build_figure_manifest(
            boxed, profile=profile_for_url(ELSEVIER_URL), base_url=ELSEVIER_URL)
        assert [f.asset_url.rsplit("-", 1)[-1] for f in manifest.figures] == [
            "fx1.jpg", "gr1.jpg", "fx2.jpg"]
        assert [d["reason"] for d in manifest.decorative
                if d["asset_url"].endswith("-box3.jpg")] == ["unlabelled"]

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

    def test_an_embedded_figure_still_names_its_asset(self):
        """The stored artifact is what the corpus re-reads.

        Without the preserved URL a figure in an embedded capture reports
        asset_url="" -- so `unnumbered_asset_patterns` cannot fire and the
        Elsevier graphical abstract goes back to claiming "Figure 10".
        """

        markup = ELSEVIER_GRAPHICAL_ABSTRACT.replace(
            'src="https://ars.els-cdn.com/content/image/1-s2.0-S0140673626016399-fx1.jpg"',
            'src="data:image/jpeg;base64,AAAA"'
            ' data-capture-src="https://ars.els-cdn.com/content/image/'
            '1-s2.0-S0140673626016399-fx1.jpg"',
        )
        figures = build_figure_manifest(
            markup, profile=profile_for_url(ELSEVIER_URL), base_url=ELSEVIER_URL
        ).figures
        assert figures[0].asset_url.endswith("-fx1.jpg")
        assert figures[0].label == "Figure"

    def test_an_embedded_figure_with_nothing_preserved_says_so(self):
        """No URL is reported as no URL, never as the data: URI."""

        markup = ELSEVIER_GRAPHICAL_ABSTRACT.replace(
            'src="https://ars.els-cdn.com/content/image/1-s2.0-S0140673626016399-fx1.jpg"',
            'src="data:image/jpeg;base64,AAAA"',
        )
        figures = build_figure_manifest(
            markup, profile=profile_for_url(ELSEVIER_URL), base_url=ELSEVIER_URL
        ).figures
        assert figures[0].asset_url == ""

    def test_a_dangling_aria_reference_is_not_an_error(self):
        markup = ELSEVIER_GRAPHICAL_ABSTRACT.replace('id="alt11"', 'id="somewhere-else"')
        figures = build_figure_manifest(
            markup, profile=profile_for_url(ELSEVIER_URL), base_url=ELSEVIER_URL
        ).figures
        assert len(figures) == 1
        assert figures[0].caption == ""


SCIENCE_URL = "https://www.science.org/doi/10.1126/science.synthetic"
#: The shape Science gives a research article: the visual abstract is a
#: captioned `<figure id="Fa">` inside the structured abstract, and nothing on
#: it says "Fig."; the body figures are `F1`... with "Fig. 1 ." captions.
SCIENCE_VISUAL_ABSTRACT = """<html><body><article class="{article_class}">
<div id="abstracts"><section id="structured-abstract"><section id="abs-sec-4">
 <div class="figure-wrap"><figure id="Fa" class="graphic">
  <img src="https://www.science.org/cms/asset/a/science.synthetic-fa.jpg" alt="">
  <figcaption>{abstract_caption}</figcaption></figure></div>
</section></section></div>
<section id="bodymatter"><section id="sec-1">
 <p>{filler}</p>
 <div class="figure-wrap"><figure id="F1" class="graphic">
  <img src="https://www.science.org/cms/asset/b/science.synthetic-f1.jpg" alt="">
  <figcaption>Fig. 1 . A numbered figure in the body.</figcaption></figure></div>
 <div class="figure-wrap"><figure id="Fb" class="graphic">
  <img src="https://www.science.org/cms/asset/c/science.synthetic-fb.jpg" alt="">
  <figcaption>An unnumbered, captioned illustration in the body.</figcaption></figure></div>
</section></section>
</article></body></html>"""


def _science(abstract_caption="Illustration of the model and its deployment.",
             article_class="core"):
    return SCIENCE_VISUAL_ABSTRACT.format(
        abstract_caption=abstract_caption, article_class=article_class,
        filler="Body text. " * 80,
    )


class TestTheVisualAbstract:
    """A figure the page captions inside its abstract is a figure (operator, 2026-09-19).

    Measured on a Science research article: `<figure id="Fa">` in
    `#structured-abstract`, a full caption, no "Fig." anywhere. Every label
    source was silent, so the visual abstract was filed as decoration while
    Fig. 1-4 were kept. Where it sits is the evidence.
    """

    def _labels(self, markup: str):
        manifest = build_figure_manifest(
            markup, profile=profile_for_url(SCIENCE_URL), base_url=SCIENCE_URL
        )
        return [(figure.label, figure.figure_id) for figure in manifest.figures]

    def test_it_is_selected_and_named_for_what_it_is(self):
        assert self._labels(_science()) == [("Graphical abstract", "Fa"), ("Figure 1", "F1")]

    def test_the_same_figure_outside_the_abstract_is_still_not_guessed_at(self):
        """The control: `Fb` has a caption and no label, in the body. Unchanged."""

        assert ("Graphical abstract", "Fb") not in self._labels(_science())
        assert all(figure_id != "Fb" for _, figure_id in self._labels(_science()))

    def test_without_a_caption_it_is_not_a_figure(self):
        assert self._labels(_science(abstract_caption="")) == [("Figure 1", "F1")]

    def test_a_wrapper_that_merely_says_abstract_does_not_make_every_figure_one(self):
        """The article container itself is not an abstract mark."""

        labels = self._labels(_science(article_class="abstract-view"))
        assert labels == [("Graphical abstract", "Fa"), ("Figure 1", "F1")]

    def test_the_extension_selects_the_same_figures(self):
        """The serializer is the twin of this module; they may not disagree."""

        import json
        import shutil
        import subprocess

        root = Path(__file__).resolve().parents[1]
        node = shutil.which("node") or shutil.which("nodejs")
        if node is None or not (root / "node_modules" / "linkedom").is_dir():
            pytest.skip("node or linkedom is not installed")
        registry = load_registry()
        profile = json.dumps(profile_for_url(SCIENCE_URL))
        cases = (("core", "Illustration of the model."), ("abstract-view", ""))
        for article_class, caption in cases:
            markup = _science(abstract_caption=caption, article_class=article_class)
            script = f"""
              import {{ parseHTML }} from "linkedom";
              import {{ serializePage }} from "{(root / "extension" / "serialize.js").as_posix()}";
              import {{ LIMITS }} from "{(root / "extension" / "limits.js").as_posix()}";
              const {{ document }} = parseHTML({json.dumps(markup)});
              globalThis.document = document;
              globalThis.location = {{ href: {json.dumps(SCIENCE_URL)} }};
              const page = serializePage("0123456789abcdef", {profile}, LIMITS);
              console.log(JSON.stringify((page.figures || []).map((f) => [f.label, f.figure_id])));
            """
            result = subprocess.run([node, "--input-type=module", "--eval", script],
                                    capture_output=True, text=True, check=False, cwd=root)
            assert result.returncode == 0, result.stderr
            seen = [tuple(row) for row in json.loads(result.stdout.strip().splitlines()[-1])]
            assert seen == self._labels(markup), (article_class, caption)
        assert registry  # the profile came from the shipped registry


def test_every_supported_profile_has_the_fixture_it_claims():
    """`supported` is a measurement; a missing fixture makes it a wish."""

    for profile in load_registry()["profiles"]:
        if profile["status"] != "supported":
            continue
        path = fixture_path(profile)
        assert path is not None and path.is_file(), profile["id"]


JAMA_INLINE_URL = 'https://jamanetwork.com/journals/jama/fullarticle/9999999'
JAMA_INLINE = '''<html><body><div class="article-body"><p>ARTICLE_PROSE</p>
<div class="figure-table-wrapper inline"><div class="inline-graphic">
<a class="figure-anchor" id="articlegraphic-a"></a>
<a path-from-xml="articlegraphic-a" href="https://cdn.jamanetwork.com/graphic.png">
<img class="content-img" path-from-xml="articlegraphic-a" alt="Image description not available."
src="https://cdn.jamanetwork.com/graphic.png"></a></div></div></div>
<img src="https://cdn.jamanetwork.com/logo.png" alt="Journal logo"></body></html>'''.replace('ARTICLE_PROSE', 'Synthetic article paragraph with independently scoped body. ' * 10)


def _jama_inline_cases():
    return [
        (JAMA_INLINE, 'jama', [('Figure', 'articlegraphic-a')]),
        (JAMA_INLINE.replace('id="articlegraphic-a"', 'id="unrelated"'), 'jama', []),
        (JAMA_INLINE.replace('class="inline-graphic"', 'class="other"'), 'jama', []),
        (JAMA_INLINE.replace('class="figure-table-wrapper inline"', 'class="figure-table-wrapper"'), 'jama', []),
        (JAMA_INLINE.replace('path-from-xml="articlegraphic-a"', ''), 'jama', []),
        (JAMA_INLINE.replace('class="article-body"', 'class="outside"').replace(
            '</body>', '<div class="article-body"><p>' + 'Independent actual article body. ' * 20 + '</p></div></body>'), 'jama', []),
        (JAMA_INLINE, 'different-publisher', []),
        (JAMA_INLINE.replace('class="article-body"', 'class="unknown-scope"'), 'jama', []),
    ]


@pytest.mark.parametrize('markup,profile_id,expected', _jama_inline_cases())
def test_jama_declared_inline_graphic_preserves_xml_identity(markup, profile_id, expected):
    profile = {**profile_for_url(JAMA_INLINE_URL), 'id': profile_id}
    result = build_figure_manifest(markup, profile=profile, base_url=JAMA_INLINE_URL)
    assert [(f.label, f.figure_id) for f in result.figures] == expected


def test_jama_inline_graphic_extension_receiver_parity():
    import json
    import shutil
    import subprocess
    root = Path(__file__).resolve().parents[1]
    node = shutil.which('node') or shutil.which('nodejs')
    assert node and (root / 'node_modules/linkedom').is_dir(), 'actual serializer test prerequisites required'
    for markup, profile_id, expected in _jama_inline_cases():
        profile = {**profile_for_url(JAMA_INLINE_URL), 'id': profile_id}
        script = f'''
          import {{ parseHTML }} from 'linkedom';
          import {{ serializePage }} from '{(root / 'extension/serialize.js').as_posix()}';
          import {{ LIMITS }} from '{(root / 'extension/limits.js').as_posix()}';
          const {{ document }} = parseHTML({json.dumps(markup)});
          globalThis.document = document;
          globalThis.location = {{ href: {json.dumps(JAMA_INLINE_URL)} }};
          const page = serializePage('0123456789abcdef', {json.dumps(profile)}, LIMITS);
          console.log(JSON.stringify((page.figures || []).map(f => [f.label, f.figure_id])));
        '''
        result = subprocess.run([node, '--input-type=module', '--eval', script],
                                capture_output=True, text=True, check=True, cwd=root)
        assert [tuple(row) for row in json.loads(result.stdout)] == expected


JAMA_TWO_URL = 'https://jamanetwork.com/journals/jama/fullarticle/9999999'
SYNTHETIC_FILLER = 'Synthetic filler sentence for scoping. ' * 20


def _synthetic_png_bytes(r: int, g: int, b: int) -> bytes:
    """Deterministic 1x1 RGB PNG, generated in-test so the commit stays text."""

    import struct
    import zlib

    sig = bytes.fromhex('89504e470d0a1a0a')

    def chunk(typ: bytes, data: bytes) -> bytes:
        body = struct.pack('>I', len(data)) + typ + data
        return body + struct.pack('>I', zlib.crc32(typ + data) & 0xFFFFFFFF)

    ihdr = struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(bytes([0, r, g, b]))
    return sig + chunk(b'IHDR', ihdr) + chunk(b'IDAT', idat) + chunk(b'IEND', b'')


def _synthetic_png(name: str) -> bytes:
    mapping = {'graphic-a.png': (255, 0, 0), 'graphic-b.png': (0, 0, 255)}
    return _synthetic_png_bytes(*mapping[name])


def _synthetic_b64(name: str) -> str:
    import base64

    return base64.b64encode(_synthetic_png(name)).decode()


def _jama_two_markup(red_name='graphic-a.png', blue_name='graphic-b.png', *, extra_outside='',
                      second_patch=None, container_class='article-body') -> str:
    red = _synthetic_b64(red_name)
    blue = _synthetic_b64(blue_name)
    if second_patch == 'missing-xml':
        second_img_attrs = 'class="content-img"'
        second_link_open = '<a path-from-xml="articlegraphic-b" href="#">'
    elif second_patch == 'mismatched-link':
        second_img_attrs = 'class="content-img" path-from-xml="articlegraphic-b"'
        second_link_open = '<a path-from-xml="unrelated-key" href="#">'
    else:
        second_img_attrs = 'class="content-img" path-from-xml="articlegraphic-b"'
        second_link_open = '<a path-from-xml="articlegraphic-b" href="#">'
    second_anchor = '<a class="figure-anchor" id="articlegraphic-b"></a>'
    if second_patch == 'mismatched-anchor':
        second_anchor = '<a class="figure-anchor" id="unrelated"></a>'
    first_link_open = '<a path-from-xml="articlegraphic-a" href="#">'
    first_img_attrs = 'class="content-img" path-from-xml="articlegraphic-a"'
    return (
        f'<html><body><div class="{container_class}"><p>{SYNTHETIC_FILLER}</p>'
        '<div class="figure-table-wrapper inline"><div class="inline-graphic">'
        '<a class="figure-anchor" id="articlegraphic-a"></a>'
        f'{first_link_open}<img {first_img_attrs} '
        f'alt="Image description not available." src="data:image/png;base64,{red}"></a>'
        '</div></div>'
        '<div class="figure-table-wrapper inline"><div class="inline-graphic">'
        f'{second_anchor}'
        f'{second_link_open}<img {second_img_attrs} '
        f'alt="Image description not available." src="data:image/png;base64,{blue}"></a>'
        '</div></div>'
        f'</div>{extra_outside}</body></html>'
    )


class TestJamaTwoEmbeddedGraphics:
    """Two unnumbered JAMA graphics with distinct anchors and distinct bytes.

    The receiver intentionally reports asset_url="" once a captured data: URI
    loses its original URL. Dedup keyed only on (label, asset_url) collapsed
    two such graphics into one; the serializer (which still sees distinct
    data: URIs) kept two. Both must keep two, and the same bytes twice must
    still collapse per the documented same-asset contract. No numbers are
    invented and no size test is used anywhere.
    """

    def test_generated_pngs_are_valid_and_distinct(self):
        import hashlib

        assert len(SYNTHETIC_FILLER) > 400
        for name in ('graphic-a.png', 'graphic-b.png'):
            raw = _synthetic_png(name)
            assert raw[:8] == bytes.fromhex('89504e470d0a1a0a')
            assert len(raw) < 5000
        digests = {
            hashlib.sha256(_synthetic_png(n)).hexdigest()
            for n in ('graphic-a.png', 'graphic-b.png')
        }
        assert len(digests) == 2

    def test_two_distinct_embedded_graphics_survive(self):
        markup = _jama_two_markup()
        profile = profile_for_url(JAMA_TWO_URL)
        result = build_figure_manifest(markup, profile=profile, base_url=JAMA_TWO_URL)
        assert [(f.label, f.figure_id) for f in result.figures] == [
            ('Figure', 'articlegraphic-a'), ('Figure', 'articlegraphic-b')]
        # No number is invented for unnumbered graphics.
        assert all(f.label == 'Figure' for f in result.figures)
        # The captured data: URI lost its URL by design; the bytes are identity.
        assert [f.asset_url for f in result.figures] == ['', '']
        assert [f.embedded for f in result.figures] == [True, True]
        assert result.figures[0].asset_sha256 != result.figures[1].asset_sha256
        assert all(len(f.asset_sha256) == 64 for f in result.figures)

    def test_same_embedded_bytes_still_collapse(self):
        """The documented dedup contract: the same asset twice is one figure."""

        markup = _jama_two_markup(red_name='graphic-a.png', blue_name='graphic-a.png')
        profile = profile_for_url(JAMA_TWO_URL)
        result = build_figure_manifest(markup, profile=profile, base_url=JAMA_TWO_URL)
        assert [(f.label, f.figure_id) for f in result.figures] == [
            ('Figure', 'articlegraphic-a')]

    def test_outside_duplicate_anchor_does_not_steal_identity(self):
        outside = (
            '<div class="figure-table-wrapper inline"><div class="inline-graphic">'
            '<a class="figure-anchor" id="articlegraphic-a"></a>'
            '<a path-from-xml="articlegraphic-a" href="#">'
            '<img class="content-img" path-from-xml="articlegraphic-a" '
            'alt="Image description not available." '
            f'src="data:image/png;base64,{_synthetic_b64("graphic-a.png")}">'
            '</a></div></div>'
        )
        markup = _jama_two_markup(extra_outside=outside)
        profile = profile_for_url(JAMA_TWO_URL)
        result = build_figure_manifest(markup, profile=profile, base_url=JAMA_TWO_URL)
        assert [(f.label, f.figure_id) for f in result.figures] == [
            ('Figure', 'articlegraphic-a'), ('Figure', 'articlegraphic-b')]

    def test_missing_xml_link_on_second_keeps_only_first(self):
        markup = _jama_two_markup(second_patch='missing-xml')
        profile = profile_for_url(JAMA_TWO_URL)
        result = build_figure_manifest(markup, profile=profile, base_url=JAMA_TWO_URL)
        assert [(f.label, f.figure_id) for f in result.figures] == [
            ('Figure', 'articlegraphic-a')]

    def test_mismatched_anchor_on_second_keeps_only_first(self):
        markup = _jama_two_markup(second_patch='mismatched-anchor')
        profile = profile_for_url(JAMA_TWO_URL)
        result = build_figure_manifest(markup, profile=profile, base_url=JAMA_TWO_URL)
        assert [(f.label, f.figure_id) for f in result.figures] == [
            ('Figure', 'articlegraphic-a')]

    def test_mismatched_link_on_second_keeps_only_first(self):
        markup = _jama_two_markup(second_patch='mismatched-link')
        profile = profile_for_url(JAMA_TWO_URL)
        result = build_figure_manifest(markup, profile=profile, base_url=JAMA_TWO_URL)
        assert [(f.label, f.figure_id) for f in result.figures] == [
            ('Figure', 'articlegraphic-a')]

    def test_unknown_scope_keeps_nothing(self):
        markup = _jama_two_markup(container_class='unknown-scope')
        profile = profile_for_url(JAMA_TWO_URL)
        result = build_figure_manifest(markup, profile=profile, base_url=JAMA_TWO_URL)
        assert result.figures == ()

    def test_foreign_publisher_with_similar_css_keeps_nothing(self):
        markup = _jama_two_markup()
        profile = {**profile_for_url(JAMA_TWO_URL), 'id': 'different-publisher'}
        result = build_figure_manifest(markup, profile=profile, base_url=JAMA_TWO_URL)
        assert result.figures == ()

    def test_ordinary_unlabelled_decoration_stays_out(self):
        markup = (
            f'<html><body><div class="article-body"><p>{SYNTHETIC_FILLER}</p>'
            f'<img src="data:image/png;base64,{_synthetic_b64("graphic-a.png")}" '
            'alt="Image description not available."></div></body></html>'
        )
        profile = profile_for_url(JAMA_TWO_URL)
        result = build_figure_manifest(markup, profile=profile, base_url=JAMA_TWO_URL)
        assert result.figures == ()
        assert any(d['reason'] == 'unlabelled' for d in result.decorative)

    def test_extension_and_receiver_agree_on_two_graphic_cases(self):
        import json
        import shutil
        import subprocess

        root = Path(__file__).resolve().parents[1]
        node = shutil.which('node') or shutil.which('nodejs')
        assert node and (root / 'node_modules/linkedom').is_dir(), \
            'actual serializer test prerequisites required'
        cases = [
            (_jama_two_markup(), 'jama',
             [('Figure', 'articlegraphic-a'), ('Figure', 'articlegraphic-b')]),
            (_jama_two_markup(red_name='graphic-a.png', blue_name='graphic-a.png'),
             'jama', [('Figure', 'articlegraphic-a')]),
            (_jama_two_markup(second_patch='missing-xml'), 'jama',
             [('Figure', 'articlegraphic-a')]),
            (_jama_two_markup(second_patch='mismatched-anchor'), 'jama',
             [('Figure', 'articlegraphic-a')]),
            (_jama_two_markup(second_patch='mismatched-link'), 'jama',
             [('Figure', 'articlegraphic-a')]),
            (_jama_two_markup(container_class='unknown-scope'), 'jama', []),
            (_jama_two_markup(), 'different-publisher', []),
        ]
        for markup, profile_id, expected in cases:
            profile = {**profile_for_url(JAMA_TWO_URL), 'id': profile_id}
            script = f'''
              import {{ parseHTML }} from 'linkedom';
              import {{ serializePage }} from '{(root / 'extension/serialize.js').as_posix()}';
              import {{ LIMITS }} from '{(root / 'extension/limits.js').as_posix()}';
              const {{ document }} = parseHTML({json.dumps(markup)});
              globalThis.document = document;
              globalThis.location = {{ href: {json.dumps(JAMA_TWO_URL)} }};
              const page = serializePage('0123456789abcdef', {json.dumps(profile)}, LIMITS);
              console.log(JSON.stringify((page.figures || []).map(f => [f.label, f.figure_id])));
            '''
            proc = subprocess.run([node, '--input-type=module', '--eval', script],
                                  capture_output=True, text=True, check=True, cwd=root)
            seen = [tuple(row) for row in json.loads(proc.stdout.strip().splitlines()[-1])]
            assert seen == expected
            receiver = build_figure_manifest(markup, profile=profile, base_url=JAMA_TWO_URL)
            assert [(f.label, f.figure_id) for f in receiver.figures] == expected


def _jama_two_srcs_markup(src1: str, src2: str) -> str:
    """Two declared JAMA graphics with explicit full src strings."""

    return (
        f'<html><body><div class="article-body"><p>{SYNTHETIC_FILLER}</p>'
        '<div class="figure-table-wrapper inline"><div class="inline-graphic">'
        '<a class="figure-anchor" id="articlegraphic-a"></a>'
        '<a path-from-xml="articlegraphic-a" href="#">'
        '<img class="content-img" path-from-xml="articlegraphic-a" '
        f'alt="Image description not available." src="{src1}"></a>'
        '</div></div>'
        '<div class="figure-table-wrapper inline"><div class="inline-graphic">'
        '<a class="figure-anchor" id="articlegraphic-b"></a>'
        '<a path-from-xml="articlegraphic-b" href="#">'
        '<img class="content-img" path-from-xml="articlegraphic-b" '
        f'alt="Image description not available." src="{src2}"></a>'
        '</div></div>'
        '</div></body></html>'
    )


class TestJamaReviewFixes:
    """Parent acceptance remediation: no URI hash as image bytes (R1) and
    equivalent data-URI headers share one identity on both sides (R2)."""

    def test_invalid_base64_stays_unmeasured_and_preserves_identity(self):
        """`data:image/png;base64,A` must not store a URI hash as bytes."""

        src = 'data:image/png;base64,A'
        markup = _jama_two_srcs_markup(src, src)
        profile = profile_for_url(JAMA_TWO_URL)
        result = build_figure_manifest(markup, profile=profile, base_url=JAMA_TWO_URL)
        assert [(f.label, f.figure_id) for f in result.figures] == [
            ('Figure', 'articlegraphic-a'), ('Figure', 'articlegraphic-b')]
        assert [f.asset_url for f in result.figures] == ['', '']
        assert [f.asset_sha256 for f in result.figures] == ['', '']
        assert '237933886de30535eba8a37ad1db05f138ae5b3b4ddbee94dbd25a0b07f8c251' not in [
            f.asset_sha256 for f in result.figures]

    @staticmethod
    def _invalid_srcs():
        return [
            'data:image/png;base64,A',
            'data:image/png;base64,!!!',
            'data:image/png;base64,AAAAA',
            'data:image/png;base64,',
            'data:image/png;base64',
            'data:image/png;base64,   ',
        ]

    def test_each_malformed_payload_preserves_declared_identity(self):
        for src in self._invalid_srcs():
            markup = _jama_two_srcs_markup(src, src)
            profile = profile_for_url(JAMA_TWO_URL)
            result = build_figure_manifest(
                markup, profile=profile, base_url=JAMA_TWO_URL)
            assert [(f.label, f.figure_id) for f in result.figures] == [
                ('Figure', 'articlegraphic-a'),
                ('Figure', 'articlegraphic-b')], src
            assert all(f.asset_sha256 == '' for f in result.figures), src
            assert all(f.asset_url == '' for f in result.figures), src

    def test_equivalent_headers_and_distinct_bytes_agree_both_sides(self):
        import json
        import shutil
        import subprocess

        root = Path(__file__).resolve().parents[1]
        node = shutil.which('node') or shutil.which('nodejs')
        assert node and (root / 'node_modules/linkedom').is_dir(), \
            'actual serializer test prerequisites required'
        from urllib.parse import quote_from_bytes

        red = _synthetic_b64('graphic-a.png')
        blue = _synthetic_b64('graphic-b.png')
        wrapped = '\n'.join(red[i:i + 20] for i in range(0, len(red), 20))
        percent_red = quote_from_bytes(_synthetic_png('graphic-a.png'), safe='')
        percent_blue = quote_from_bytes(_synthetic_png('graphic-b.png'), safe='')
        cases = [
            # Same decoded bytes, different headers: one figure on both sides.
            (f'data:image/png;base64,{red}',
             f'data:image/png;charset=binary;base64,{red}',
             [('Figure', 'articlegraphic-a')]),
            # Same bytes, whitespace-wrapped payload: still one figure.
            (f'data:image/png;base64,{red}',
             f'data:image/png;base64,{wrapped}',
             [('Figure', 'articlegraphic-a')]),
            # Same bytes, base64 vs percent-encoding: one figure on both
            # sides. Identity comes from decoded bytes, not payload text.
            (f'data:image/png;base64,{red}',
             f'data:image/png,{percent_red}',
             [('Figure', 'articlegraphic-a')]),
            # Distinct bytes, different headers: still two figures.
            (f'data:image/png;base64,{red}',
             f'data:image/png;charset=binary;base64,{blue}',
             [('Figure', 'articlegraphic-a'), ('Figure', 'articlegraphic-b')]),
            # Distinct bytes, base64 vs percent-encoding: still two figures.
            (f'data:image/png;base64,{red}',
             f'data:image/png,{percent_blue}',
             [('Figure', 'articlegraphic-a'), ('Figure', 'articlegraphic-b')]),
            # Distinct bytes, same header: still two figures.
            (f'data:image/png;base64,{red}',
             f'data:image/png;base64,{blue}',
             [('Figure', 'articlegraphic-a'), ('Figure', 'articlegraphic-b')]),
            # Same invalid payload, distinct declared keys: two figures,
            # honestly unmeasured, on both sides.
            ('data:image/png;base64,A', 'data:image/png;base64,A',
             [('Figure', 'articlegraphic-a'), ('Figure', 'articlegraphic-b')]),
        ]
        for src1, src2, expected in cases:
            markup = _jama_two_srcs_markup(src1, src2)
            profile = profile_for_url(JAMA_TWO_URL)
            receiver = build_figure_manifest(
                markup, profile=profile, base_url=JAMA_TWO_URL)
            assert [(f.label, f.figure_id) for f in receiver.figures] == expected, src2[:60]
            script = f'''
              import {{ parseHTML }} from 'linkedom';
              import {{ serializePage }} from '{(root / 'extension/serialize.js').as_posix()}';
              import {{ LIMITS }} from '{(root / 'extension/limits.js').as_posix()}';
              const {{ document }} = parseHTML({json.dumps(markup)});
              globalThis.document = document;
              globalThis.location = {{ href: {json.dumps(JAMA_TWO_URL)} }};
              const page = serializePage('0123456789abcdef', {json.dumps(profile)}, LIMITS);
              console.log(JSON.stringify((page.figures || []).map(f => [f.label, f.figure_id])));
            '''
            proc = subprocess.run([node, '--input-type=module', '--eval', script],
                                  capture_output=True, text=True, check=True, cwd=root)
            seen = [tuple(row) for row in json.loads(proc.stdout.strip().splitlines()[-1])]
            assert seen == expected, src2[:60]
