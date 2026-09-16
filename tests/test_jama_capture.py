"""Synthetic multi-widget JAMA DOM; no publisher text or live assets."""
from corpus_capture.figure_manifest import build_figure_manifest, find_article_container, parse_markup
from corpus_capture.profiles import fixture_path, profile_for_url


def test_jama_body_and_display_items():
    profile = profile_for_url('https://jamanetwork.com/journals/jama/fullarticle/synthetic')
    html = fixture_path(profile).read_text()
    soup = parse_markup(html)
    container, selector = find_article_container(soup, profile['article_container_selectors'])
    assert selector == '.article-body'
    assert len(container.select('.article-full-text')) == 3
    assert 'Synthetic reference entry' in container.get_text()
    assert soup.select_one('meta[name="citation_doi"]')['content'] == '10.1000/synthetic-jama'
    manifest = build_figure_manifest(html, profile=profile, base_url='https://example.test/')
    assert manifest.labels == ('Figure 1', 'Table 1', 'Figure 2', 'Table 2', 'Figure 3')
    assert all(f.asset_url.startswith('https://example.test/synthetic/') for f in manifest.figures)
    assert len(manifest.decorative) == 1
    assert manifest.decorative[0]['reason'] == 'outside_article_container'
    # Actual lazy markup often has no src at all.
    for img in soup.select('img'):
        del img['src']
    assert build_figure_manifest(str(soup), profile=profile, base_url='https://example.test/').labels == manifest.labels


def test_abstract_is_not_a_full_article_container():
    profile = profile_for_url('https://jamanetwork.com/a')
    soup = parse_markup('<div class="article-full-text">' + 'Abstract. ' * 200 + '</div>')
    assert find_article_container(soup, profile['article_container_selectors'])[1] == ''
