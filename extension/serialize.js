"use strict";

// Phase 1 of the capture, and the only code that runs INSIDE the article page.
//
// It does not fetch anything. Every asset the page needs is enumerated here and
// fetched later from the extension, where `host_permissions` make a cross-origin
// figure readable at all: a `fetch()` issued from the page context inherits the
// page's origin, so a CDN that serves figures without CORS headers -- which is
// most of them -- would fail, silently, on exactly the images worth keeping.
//
// Every asset therefore leaves here as a placeholder token carrying a per-capture
// nonce. A nonce, rather than an index alone, because the page's own text is
// inside the string we are about to search and replace, and an article about web
// scraping is entirely capable of containing the literal word we chose.
export function serializePage(nonce, profile, limits) {
  profile = profile || {};
  // Chrome's page translation rewrites the text nodes in place, a screenful at
  // a time, and marks <html>. A capture of that DOM is a machine translation of
  // the top of the article and the original below it, filed as the source.
  // Refused, not repaired: the original text is no longer in the page.
  if (/\btranslated-(ltr|rtl)\b/.test(document.documentElement.className || "")
      || document.querySelector('font[style*="vertical-align: inherit"]')) {
    return { error: "page_translated" };
  }
  // Passed in rather than imported: this function is injected into the page as
  // a stringified function, so it has no module scope. extension/limits.js is
  // the one table; this is a copy of the defaults for the case where an older
  // caller does not pass it, and it must stay in step -- a test asserts it.
  const cap = Object.assign({
    maxAssets: 300, maxFigures: 200, maxDocumentChars: 16 * 1024 * 1024,
    maxMetaKeys: 200, maxMetaValues: 100, maxMetaValueChars: 2000,
    maxCaptionChars: 2000, maxAltChars: 500, maxAuthors: 100,
  }, limits || {});
  const sel = (key, fallback) =>
    (Array.isArray(profile[key]) && profile[key].length ? profile[key] : fallback);
  const containerSelectors = sel("article_container_selectors", [
    '[itemprop="articleBody"]', "article", "main", '[role="main"]', "#bodyContent",
  ]);
  const figureSelectors = sel("figure_selectors", ["figure", ".figure", ".article-figure"]);
  const captionSelectors = sel("caption_selectors", ["figcaption", ".caption"]);
  const dropSelectors = sel("drop_selectors", []);
  // An asset whose PATH says the publisher did not number it (Elsevier `-fx`):
  // profile data, the twin of figure_manifest.is_unnumbered_asset().
  const unnumberedPatterns = sel("unnumbered_asset_patterns", []);
  const isUnnumbered = (url) => {
    const lowered = String(url || "").toLowerCase();
    return unnumberedPatterns.some((pattern) => pattern && lowered.includes(String(pattern).toLowerCase()));
  };

  // The article container is the boundary that keeps the adverts out. Picking
  // images by SIZE instead returned five promos and zero figures on the page
  // this was measured against: a big image is a big image, and only its
  // position in the document says whose it is.
  let articleRoot = null;
  let containerSelector = "";
  for (const selector of containerSelectors) {
    let node = null;
    try { node = document.querySelector(selector); } catch (_) { node = null; }
    if (node && (node.innerText || "").length > 400) {
      articleRoot = node;
      containerSelector = selector;
      break;
    }
  }
  const scoped = articleRoot !== null;
  if (!articleRoot) articleRoot = document.body || document.documentElement;

  // `Figure1` has no space, and `^(Figure|Table)\b` does not match it: `e` and
  // `1` are both word characters, so there is no boundary between them. That
  // single case is the difference between three figures and none.
  const LABEL = /^\s*(supplementary\s+figure|efigure|etable|figure|fig\.?|table|panel|chart|scheme)\s*([0-9]+|[ivxlc]+|[a-z])?\b/i;
  const ID_LABEL = /^(figure|fig|f|table|tbl|tab|t)[-_]?(\d+)$/i;
  const KIND = { f: "Figure", fig: "Figure", figure: "Figure",
                 t: "Table", tab: "Table", tbl: "Table", table: "Table" };
  const BARE_KINDS = new Set(Object.values(KIND));
  // `numbered === false`: the id's digits are an internal counter, so the id may
  // say this IS a figure and may not say which one. A number nobody can see is
  // worse than no number, because a reader cites it.
  const labelFor = (elementId, alt, caption, numbered = true) => {
    const byId = ID_LABEL.exec(String(elementId || "").trim());
    if (byId) {
      const kind = KIND[byId[1].toLowerCase()];
      return numbered ? kind + " " + parseInt(byId[2], 10) : kind;
    }
    for (const text of [alt, caption]) {
      const found = LABEL.exec(String(text || "").trim());
      if (!found) continue;
      let word = found[1].trim().replace(/\.$/, "");
      word = word.charAt(0).toUpperCase() + word.slice(1).toLowerCase();
      word = { Fig: "Figure", Efigure: "eFigure", Etable: "eTable" }[word] || word;
      return found[2] ? word + " " + found[2] : word;
    }
    return "";
  };

  const token = (index) => `corpus-asset-${nonce}-${index}-end`;
  const assets = [];
  let assetsOverflow = 0;
  const claim = (url, kind) => {
    let absolute = "";
    try { absolute = new URL(url, document.baseURI).href; } catch (_) { return null; }
    if (!/^https?:/i.test(absolute)) return null;
    const existing = assets.findIndex((a) => a.url === absolute && a.kind === kind);
    if (existing >= 0) return token(existing);
    // The ceiling is not a policy about what is worth keeping. It is what stops
    // a page from choosing how many authenticated requests this makes.
    if (assets.length >= cap.maxAssets) {
      assetsOverflow += 1;
      return null;
    }
    assets.push({ index: assets.length, url: absolute, kind });
    return token(assets.length - 1);
  };

  // Serialize the ARTICLE plus <head> for its metadata. Everything else on the
  // page -- rails, modals, marketing, third-party frames -- is not the article
  // and has no business in an artifact that claims to be one.
  const clone = document.createElement("html");
  const headClone = document.head
    ? document.head.cloneNode(true) : document.createElement("head");
  const bodyClone = document.createElement("body");
  const roots = [];
  // A publisher can put its headline and lead image beside the article body.
  // Select only declared header blocks, never the containing page/marketing rail.
  for (const selector of sel("article_header_selectors", [".news-article__hero"])) {
    let header = null;
    try { header = document.querySelector(selector); } catch (_) { /* invalid selector */ }
    if (header && !header.contains(articleRoot) && !articleRoot.contains(header)
        && header.querySelector("h1") && !roots.some((root) => root.contains(header))) roots.push(header);
  }
  roots.push(articleRoot);
  const imageSelector = "img, svg[data-inject-url]";
  const live = roots.flatMap((root) => [...root.querySelectorAll(imageSelector)]);
  const liveByCopy = new Map();
  for (const root of roots) {
    const copy = root.cloneNode(true);
    const originals = [...root.querySelectorAll(imageSelector)];
    [...copy.querySelectorAll(imageSelector)].forEach((node, index) => liveByCopy.set(node, originals[index]));
    bodyClone.appendChild(copy);
  }
  // A publisher-injected SVG has an explicit image source. Preserve that
  // image in an <img>, where scripts cannot run; raw inline SVG remains
  // forbidden by the sanitizer. The ordinary asset policy still gates fetches.
  for (const svg of bodyClone.querySelectorAll("svg[data-inject-url]")) {
    const original = liveByCopy.get(svg);
    const img = document.createElement("img");
    img.setAttribute("src", svg.getAttribute("data-inject-url"));
    img.setAttribute("class", svg.getAttribute("class") || "");
    img.setAttribute("alt", "Figure — " + (svg.closest("figure")?.querySelector("h3")?.textContent || "Diagram"));
    img.setAttribute("data-capture-svg", "publisher-image");
    liveByCopy.set(img, original);
    svg.replaceWith(img);
  }
  clone.appendChild(headClone);
  clone.appendChild(bodyClone);

  // Scripts cannot travel: the artifact is read later by a reader that serves it
  // under `script-src 'none'`, and a stored script is a stored liability either
  // way. `<noscript>` goes with them because its content is markup the live page
  // deliberately did not render.
  clone.querySelectorAll(
    "script, noscript, iframe, frame, object, embed, template," +
    " link[rel~='preload'], link[rel~='prefetch'], link[rel~='dns-prefetch']," +
    " link[rel~='modulepreload'], link[rel~='preconnect']"
  ).forEach((node) => node.remove());
  for (const selector of dropSelectors) {
    try { clone.querySelectorAll(selector).forEach((node) => node.remove()); }
    catch (_) { /* a publisher selector may not be valid CSS in this context */ }
  }
  clone.querySelectorAll("*").forEach((node) => {
    for (const attribute of [...node.attributes]) {
      if (/^on/i.test(attribute.name)) node.removeAttribute(attribute.name);
    }
  });

  // Images are matched to their live counterparts by position, not by src: a
  // lazy-loaded figure's `src` attribute is a 1x1 placeholder until it renders,
  // and `currentSrc` is the only property that names the bytes the reader
  // actually saw -- including the srcset variant the viewport picked.
  const copies = [...bodyClone.querySelectorAll("img")];

  // The figure manifest is built HERE, before any src is rewritten: once an
  // image is a data: URI its asset URL is gone, and naming the asset each
  // figure came from is the manifest's whole job.
  const figureRows = [];
  const claimed = new Set();
  const captionOf = (node) => {
    for (const selector of captionSelectors) {
      let found = null;
      try { found = node.querySelector && node.querySelector(selector); } catch (_) { found = null; }
      if (found) {
        return (found.innerText || "").replace(/\s+/g, " ").trim().slice(0, cap.maxCaptionChars);
      }
    }
    return "";
  };
  // The page sets this node inside its abstract, within the article: Atypon's
  // `#abstracts` and `#structured-abstract`, `section.abstract`, DPUB-ARIA's
  // `doc-abstract`. The receiver's figure_manifest.in_abstract() is the twin.
  const inAbstract = (node) => {
    const parent = node.parentElement;
    const mark = parent && parent.closest('[role="doc-abstract"], [id*="abstract" i], [class*="abstract" i]');
    return Boolean(mark) && mark !== articleRoot && articleRoot.contains(mark);
  };
  const imageUrl = (img) => img?.getAttribute("data-inject-url") || img?.currentSrc || img?.src || "";
  const claimFigure = (node, img, selector, rank) => {
    if (claimed.has(img) || figureRows.length >= cap.maxFigures) return;
    const caption = captionOf(node);
    const alt = (img.getAttribute("alt") || "").trim().slice(0, cap.maxAltChars);
    let elementId = (node.id || img.id || "").trim();
    const unnumbered = isUnnumbered(imageUrl(img));
    let label = labelFor(elementId, alt, caption, !unnumbered);
    // Match the receiver: JAMA declares an inline graphic through a shared
    // XML key on the image/link and its figure anchor, not through image size.
    if (!label && rank === 0 && profile.id === "jama" && scoped) {
      const wrapper = img.closest("div.inline-graphic");
      const xmlKey = (img.getAttribute("path-from-xml") || "").trim();
      if (wrapper && wrapper !== node && node.contains(wrapper)
          && node.matches(".figure-table-wrapper.inline") && xmlKey
          && img.parentElement?.tagName.toLowerCase() === "a"
          && img.parentElement.getAttribute("path-from-xml") === xmlKey
          && [...wrapper.querySelectorAll("a.figure-anchor[id]")].some((a) => a.id === xmlKey)) {
        elementId = xmlKey;
        label = KIND.figure;
      }
    }
    // A publisher-marked figure served from the unnumbered series is a display
    // item whatever its id spells: ScienceDirect keys them `undfig1`, with no
    // caption and an empty alt. An unlabelled figure with an ordinary asset is
    // still the boxed-text case and stays out.
    if (!label && rank === 0 && unnumbered) label = KIND.figure;
    // The visual abstract: a figure element the page captions inside its
    // abstract, with no "Fig." anywhere (Science's `<figure id="Fa">`).
    if (!label && rank === 0 && caption && inAbstract(node)) label = "Graphical abstract";
    if (!label && rank === 0 && caption && img.tagName.toLowerCase() === "svg") label = "Figure";
    if (!label) return;
    claimed.add(img);
    figureRows.push({
      figure_id: elementId || "fig" + (figureRows.length + 1),
      label, caption, alt, selector, rank,
      asset_url: imageUrl(img),
      order: live.indexOf(img),
    });
  };
  for (const selector of figureSelectors) {
    let nodes = [];
    try { nodes = [...articleRoot.querySelectorAll(selector)]; } catch (_) { nodes = []; }
    for (const node of nodes) {
      const img = node.querySelector(imageSelector);
      if (img) claimFigure(node, img, selector, 0);
    }
  }
  for (const img of live) {
    if (claimed.has(img)) continue;
    if (labelFor(img.id, img.getAttribute("alt") || "", "")) {
      claimFigure(img.parentElement || img, img, "img[alt]", 1);
    }
  }
  // One label, one figure. A table rendered twice -- in the body and as a rail
  // thumbnail -- carries the same alt on both, and listing it twice makes the
  // manifest disagree with the article.
  // A label without a number names a kind, not an item: three unnumbered
  // figures are all "Figure". For those the asset is the identity: the URL
  // when the page still names one, otherwise the publisher's own element key
  // so two declared JAMA anchors with no URL never collapse into one. For a
  // data: URI the identity is the normalized payload, not the full URI
  // string, so equivalent headers over identical bytes compare equal while
  // invalid or empty payloads fall back to the publisher key. Synchronous,
  // no dependency and no hash: the payload itself is the comparison.
  // Decode a percent-encoded payload to a binary string, matching Python
  // unquote_to_bytes: valid %XX becomes one byte, anything else stays
  // literal. Synchronous, no dependency.
  const decodePercentToBinary = (payload) => {
    let out = "";
    for (let i = 0; i < payload.length; i++) {
      const ch = payload[i];
      if (ch === "%" && /^[0-9A-Fa-f]{2}$/.test(payload.slice(i + 1, i + 3))) {
        out += String.fromCharCode(parseInt(payload.slice(i + 1, i + 3), 16));
        i += 2;
      } else {
        if (payload.charCodeAt(i) > 255) return "";
        out += ch;
      }
    }
    return out;
  };
  // Identity from actual decoded bytes, so base64 and percent-encoding of
  // the same bytes compare equal. atob is synchronous platform capability,
  // not a new dependency; failure stays honestly unmeasured.
  const dataUriIdentity = (url) => {
    const comma = url.indexOf(",");
    if (comma < 0) return "";
    const header = url.slice(0, comma);
    const payload = url.slice(comma + 1);
    let binary = "";
    if (/;base64/i.test(header)) {
      const cleaned = payload.replace(/\s+/g, "");
      if (!cleaned) return "";
      if (!/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=|[A-Za-z0-9+/]{4})$/.test(cleaned)) {
        return "";
      }
      try {
        binary = (typeof atob === "function") ? atob(cleaned) : "";
      } catch (_) {
        return "";
      }
      if (!binary) return "";
    } else {
      if (!payload) return "";
      binary = decodePercentToBinary(payload);
      if (!binary) return "";
    }
    return "data-bytes:" + binary;
  };
  const bareIdentity = (row) => {
    if (!row.asset_url) {
      if (row.figure_id) return "id:" + row.figure_id;
      return "";
    }
    if (/^data:/i.test(row.asset_url)) {
      const identity = dataUriIdentity(row.asset_url);
      if (identity) return identity;
      if (row.figure_id) return "id:" + row.figure_id;
      return "";
    }
    return row.asset_url;
  };
  const bestByLabel = new Map();
  for (const row of figureRows) {
    const key = BARE_KINDS.has(row.label) ? row.label + "\u0000" + bareIdentity(row) : row.label;
    const current = bestByLabel.get(key);
    if (!current || row.rank < current.rank
        || (row.rank === current.rank && row.caption.length > current.caption.length)) {
      bestByLabel.set(key, row);
    }
  }
  const figureManifest = [...bestByLabel.values()]
    .sort((a, b) => a.order - b.order)
    .map((row, position) => ({
      figure_id: row.figure_id, label: row.label, caption: row.caption,
      alt: row.alt, selector: row.selector, asset_url: row.asset_url, position,
    }));
  const figureAssets = new Set(figureManifest.map((row) => row.asset_url).filter(Boolean));
  copies.forEach((copy, position) => {
    const source = liveByCopy.get(copy);
    const href = imageUrl(source) || copy.getAttribute("src") || "";
    copy.removeAttribute("srcset");
    copy.removeAttribute("sizes");
    copy.removeAttribute("loading");
    copy.removeAttribute("decoding");
    if (!href) {
      copy.removeAttribute("src");
      return;
    }
    const placeholder = claim(href, "image");
    if (placeholder) copy.setAttribute("src", placeholder);
    else copy.removeAttribute("src");
  });

  // A stylesheet becomes an EMPTY <style> carrying its token in a CSS comment.
  // The comment is what gets replaced, so the substitution needs no knowledge of
  // how the serializer chose to spell the element's attributes.
  clone.querySelectorAll("link[rel~='stylesheet']").forEach((node) => {
    const href = node.getAttribute("href") || "";
    const placeholder = href ? claim(href, "style") : null;
    if (!placeholder) {
      node.remove();
      return;
    }
    const style = document.createElement("style");
    style.textContent = `/*${placeholder}*/`;
    node.replaceWith(style);
  });

  // One pass over every <meta> the page publishes, keyed by name OR property.
  // Collected as a LIST per key because citation_author repeats once per author
  // and taking only the first would silently drop the other nineteen.
  const declared = new Map();
  for (const node of document.querySelectorAll("meta[name], meta[property]")) {
    const key = String(node.getAttribute("name") || node.getAttribute("property") || "")
      .trim().toLowerCase().slice(0, 200);
    const value = String(node.content || "").trim().slice(0, cap.maxMetaValueChars);
    if (!key || !value) continue;
    if (!declared.has(key) && declared.size >= cap.maxMetaKeys) continue;
    if (!declared.has(key)) declared.set(key, []);
    if (declared.get(key).length < cap.maxMetaValues) declared.get(key).push(value);
  }
  const metaValue = (...names) => {
    for (const name of names) {
      const values = declared.get(name.toLowerCase());
      if (values && values[0]) return values[0];
    }
    return "";
  };
  // The first declared value that IS a DOI, not the first value of the first
  // key that has one: a Science research page declares `dc.Identifier` as the
  // publisher's own id ("aec6129") and the DOI under `publication_doi`, a later
  // key. The preview picks the same way.
  const metaDoi = (...names) => {
    for (const name of names) {
      for (const value of declared.get(name.toLowerCase()) || []) {
        if (/^(?:https?:\/\/(?:dx\.)?doi\.org\/|doi:\s*|info:doi\/)?10\.\d{4,9}\/\S+$/i.test(value)) return value;
      }
    }
    return "";
  };
  const metaList = (...names) => {
    for (const name of names) {
      const values = declared.get(name.toLowerCase());
      if (values && values.length) return values.slice(0, cap.maxAuthors);
    }
    return [];
  };
  const canonical = document.querySelector('link[rel="canonical"]');
  // A news page names its byline, day and publisher in JSON-LD and in no
  // <meta>. Asked only where the <meta> said nothing; never for a DOI. The
  // preview reads the same way (identity-probe.js).
  let ldArticle = null;
  for (const node of [...document.querySelectorAll('script[type="application/ld+json"]')].slice(0, 20)) {
    let data = null;
    try { data = JSON.parse(node.textContent || ""); } catch (_) { continue; }
    ldArticle = [].concat(data || []).flatMap((item) => (item && item["@graph"]) || item)
      .find((item) => item && typeof item === "object"
        && [].concat(item["@type"] || []).some((type) => /Article$|^BlogPosting$/.test(String(type))));
    if (ldArticle) break;
  }
  const ldNamed = (value) => [].concat(value || [])
    .map((item) => String((item && typeof item === "object" ? item.name : item) || "").trim()
      .slice(0, cap.maxMetaValueChars))
    .filter(Boolean).slice(0, cap.maxAuthors);
  const metaAuthors = metaList("citation_author", "dc.creator", "citation_authors");

  // The reader's own session decided what this page showed. `login_required` is
  // an OBSERVATION about access, never a statement about the licence: the
  // corpus rights lane is the only thing that may grant publication.
  const paywallMarkers = [
    ".paywall", "#paywall", "[data-paywall]", ".subscribe-prompt",
    ".article-access-options", "#access-options", ".loginBarrier",
  ];
  const bodyText = (document.body && document.body.innerText) || "";
  const access =
    paywallMarkers.some((s) => document.querySelector(s)) ||
    /sign in to (?:read|continue|view)|subscribe to (?:read|continue)|購買|訂閱後閱讀/i.test(
      bodyText.slice(0, 4000)
    )
      ? "login_required"
      : metaValue("citation_fulltext_world_readable") !== "" ||
        /creativecommons\.org\/licenses/i.test(document.documentElement.innerHTML.slice(0, 200000))
        ? "open"
        : "unknown";

  // The hash covers the declarations only, so a later reader can tell whether
  // the publisher changed what it says about the article without diffing the
  // whole page. Sorted, so a reordered head is not a changed head.
  const metaBlock = [...declared.entries()]
    .map(([key, values]) => `${key}=${values.join("\u001f")}`)
    .sort()
    .join("\u001e");

  const html = `<!doctype html>\n${clone.outerHTML}`;
  // Refused, not truncated: cutting a document in half produces markup that
  // parses into something nobody wrote, and storing that as the article is
  // worse than saying the page was too large.
  if (html.length > cap.maxDocumentChars) {
    return { error: "page_too_large", bytes: html.length, limit: cap.maxDocumentChars };
  }

  return {
    html,
    assets,
    assets_overflow: assetsOverflow,
    url: location.href,
    profile_id: String(profile.id || "generic"),
    container_selector: containerSelector,
    scoped_to_article: scoped,
    figures: figureManifest,
    // Every other image is still embedded, for page fidelity, and flagged so
    // nothing downstream -- packet builder, figure tagging, image registry --
    // takes a masthead for a result.
    decorative: assets
      .filter((asset) => asset.kind === "image" && !figureAssets.has(asset.url))
      .map((asset) => ({ asset_url: asset.url, reason: "not_declared_by_the_article" })),
    canonical_url: (canonical && canonical.href) || "",
    access,
    meta_block: metaBlock,
    authors: metaAuthors.length ? metaAuthors : ldNamed(ldArticle && ldArticle.author),
    meta: {
      // `publication_doi`: an Atypon page that is not a journal article (a
      // Science news story) declares its DOI there and nowhere else.
      doi: metaDoi("citation_doi", "dc.identifier", "dc.identifier.doi", "prism.doi",
                   "bepress_citation_doi", "publication_doi"),
      title: metaValue("citation_title", "og:title", "dc.title") || document.title || "",
      date_published: metaValue("citation_publication_date", "citation_date",
                                "article:published_time", "dc.date", "prism.publicationDate")
        || String((ldArticle && ldArticle.datePublished) || "").trim(),
    },
    // Everything the corpus records as producer evidence. Empty strings are kept
    // out so the row records "the page did not say" rather than "the page said
    // nothing", which are different facts about a publisher.
    publisher_meta: Object.fromEntries(Object.entries({
      title: metaValue("citation_title", "dc.title", "prism.title"),
      journal: metaValue("citation_journal_title", "prism.publicationname", "dc.source"),
      publisher: metaValue("citation_publisher", "dc.publisher", "prism.corporateentity")
        || ldNamed(ldArticle && ldArticle.publisher)[0] || "",
      publication_date: metaValue("citation_publication_date", "citation_date",
                                  "prism.publicationdate", "dc.date"),
      volume: metaValue("citation_volume", "prism.volume"),
      issue: metaValue("citation_issue", "prism.number"),
      first_page: metaValue("citation_firstpage", "prism.startingpage"),
      last_page: metaValue("citation_lastpage", "prism.endingpage"),
      issn: metaValue("citation_issn", "prism.issn", "dc.identifier.issn"),
      isbn: metaValue("citation_isbn"),
      item_type: metaValue("dc.type", "og:type", "citation_article_type"),
      series: metaValue("citation_series_title", "prism.seriestitle"),
      site_name: metaValue("og:site_name"),
      canonical_url: (canonical && canonical.href) || metaValue("og:url"),
      language: metaValue("citation_language", "dc.language"),
    }).filter(([, value]) => value !== "")),
  };
}
