"""A featured story's header and injected diagram remain self-contained images."""
import json
from test_limits import _run, SERIALIZE_JS
from pathlib import Path


def test_feature_header_and_publisher_svg_survive_without_inline_active_svg():
    markup = '''<html><head><title>Story</title></head><body>
    <div class="news-article__hero"><h1>Feature headline</h1>
      <p>Deck and byline</p><img src="https://publisher.test/lead.jpg"></div>
    <article><p>''' + 'body ' * 150 + '''</p><figure><figcaption>Igniting itch</figcaption>
      <svg data-inject-url="https://publisher.test/diagram.svg" onload="evil()"><script>evil()</script></svg>
    </figure><svg onload="evil()"><text>untrusted inline</text></svg></article>
    <aside><h1>Unrelated</h1><img src="https://publisher.test/promo.jpg"></aside>
    </body></html>'''
    sanitizer = (Path(SERIALIZE_JS).parent / 'sanitize.js').as_posix()
    out = _run(f'''
      import {{ parseHTML }} from 'linkedom';
      import {{ serializePage }} from '{SERIALIZE_JS}';
      import {{ sanitizeDocument }} from '{sanitizer}';
      const {{document}} = parseHTML({json.dumps(markup)});
      globalThis.document=document;
      globalThis.location={{href:'https://publisher.test/story'}};
      const page=serializePage('0123456789abcdef',{{}},{{}});
      const saved=parseHTML(page.html).document;
      sanitizeDocument(saved);
      console.log(JSON.stringify({{html:saved.documentElement.outerHTML,assets:page.assets,figures:page.figures}}));
    ''', need_dom=True)
    assert 'Feature headline' in out['html'] and 'Deck and byline' in out['html']
    assert 'Unrelated' not in out['html'] and 'promo.jpg' not in out['html']
    assert '<svg' not in out['html'] and 'evil()' not in out['html']
    assert {a['url'] for a in out['assets']} == {'https://publisher.test/lead.jpg','https://publisher.test/diagram.svg'}
    assert len(out['figures']) == 1
    assert out['figures'][0]['asset_url'] == 'https://publisher.test/diagram.svg'
