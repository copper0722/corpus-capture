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
export function serializePage(nonce, profile) {
  profile = profile || {};
  const sel = (key, fallback) =>
    (Array.isArray(profile[key]) && profile[key].length ? profile[key] : fallback);
  const containerSelectors = sel("article_container_selectors", [
    '[itemprop="articleBody"]', "article", "main", '[role="main"]', "#bodyContent",
  ]);
  const figureSelectors = sel("figure_selectors", ["figure", ".figure", ".article-figure"]);
  const captionSelectors = sel("caption_selectors", ["figcaption", ".caption"]);
  const dropSelectors = sel("drop_selectors", []);

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
  const labelFor = (elementId, alt, caption) => {
    const byId = ID_LABEL.exec(String(elementId || "").trim());
    if (byId) return KIND[byId[1].toLowerCase()] + " " + parseInt(byId[2], 10);
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
  const claim = (url, kind) => {
    const absolute = new URL(url, document.baseURI).href;
    if (!/^https?:/i.test(absolute)) return null;
    const existing = assets.findIndex((a) => a.url === absolute && a.kind === kind);
    if (existing >= 0) return token(existing);
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
  bodyClone.appendChild(articleRoot.cloneNode(true));
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
  const live = [...articleRoot.querySelectorAll("img")];
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
      if (found) return (found.innerText || "").replace(/\s+/g, " ").trim().slice(0, 2000);
    }
    return "";
  };
  const claimFigure = (node, img, selector, rank) => {
    if (claimed.has(img)) return;
    const caption = captionOf(node);
    const alt = (img.getAttribute("alt") || "").trim();
    const elementId = (node.id || img.id || "").trim();
    const label = labelFor(elementId, alt, caption);
    if (!label) return;
    claimed.add(img);
    figureRows.push({
      figure_id: elementId || "fig" + (figureRows.length + 1),
      label, caption, alt, selector, rank,
      asset_url: img.currentSrc || img.src || "",
      order: live.indexOf(img),
    });
  };
  for (const selector of figureSelectors) {
    let nodes = [];
    try { nodes = [...articleRoot.querySelectorAll(selector)]; } catch (_) { nodes = []; }
    for (const node of nodes) {
      const img = node.querySelector("img");
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
  const bestByLabel = new Map();
  for (const row of figureRows) {
    const current = bestByLabel.get(row.label);
    if (!current || row.rank < current.rank
        || (row.rank === current.rank && row.caption.length > current.caption.length)) {
      bestByLabel.set(row.label, row);
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
    const source = live[position];
    const href = (source && (source.currentSrc || source.src)) || copy.getAttribute("src") || "";
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
      .trim().toLowerCase();
    const value = String(node.content || "").trim();
    if (!key || !value) continue;
    if (!declared.has(key)) declared.set(key, []);
    if (declared.get(key).length < 200) declared.get(key).push(value);
  }
  const metaValue = (...names) => {
    for (const name of names) {
      const values = declared.get(name.toLowerCase());
      if (values && values[0]) return values[0];
    }
    return "";
  };
  const metaList = (...names) => {
    for (const name of names) {
      const values = declared.get(name.toLowerCase());
      if (values && values.length) return values.slice(0, 100);
    }
    return [];
  };
  const canonical = document.querySelector('link[rel="canonical"]');

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

  return {
    html: `<!doctype html>\n${clone.outerHTML}`,
    assets,
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
    authors: metaList("citation_author", "dc.creator", "citation_authors"),
    meta: {
      doi: metaValue("citation_doi", "dc.identifier", "dc.identifier.doi", "prism.doi",
                     "bepress_citation_doi"),
      title: metaValue("citation_title", "og:title", "dc.title") || document.title || "",
      date_published: metaValue("citation_publication_date", "citation_date",
                                "article:published_time", "dc.date", "prism.publicationDate"),
    },
    // Everything the corpus records as producer evidence. Empty strings are kept
    // out so the row records "the page did not say" rather than "the page said
    // nothing", which are different facts about a publisher.
    publisher_meta: Object.fromEntries(Object.entries({
      title: metaValue("citation_title", "dc.title", "prism.title"),
      journal: metaValue("citation_journal_title", "prism.publicationname", "dc.source"),
      publisher: metaValue("citation_publisher", "dc.publisher", "prism.corporateentity"),
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
