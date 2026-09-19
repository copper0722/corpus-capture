"use strict";

// The side panel's preview: what the open page declares about its identity,
// read without capturing anything. Only <meta>, <link rel="canonical">, the
// page's JSON-LD and the address are touched -- no serialization, no asset
// fetch -- so it can run on every tab the reader switches to.
//
// The key lists are the serializer's own (serialize.js); a test pins them, so
// the preview never shows an identity the saved capture would not carry.

export const DECLARATION_KEYS = {
  // `publication_doi` is what an Atypon page that is not a journal article
  // declares: a Science news story carries it and no citation_* at all.
  doi: ["citation_doi", "dc.identifier", "dc.identifier.doi", "prism.doi", "bepress_citation_doi",
        "publication_doi"],
  title: ["citation_title", "og:title", "dc.title"],
  date_published: ["citation_publication_date", "citation_date", "article:published_time",
                   "dc.date", "prism.publicationDate"],
  authors: ["citation_author", "dc.creator", "citation_authors"],
  publisher_meta: {
    title: ["citation_title", "dc.title", "prism.title"],
    journal: ["citation_journal_title", "prism.publicationname", "dc.source"],
    publisher: ["citation_publisher", "dc.publisher", "prism.corporateentity"],
    publication_date: ["citation_publication_date", "citation_date", "prism.publicationdate", "dc.date"],
    volume: ["citation_volume", "prism.volume"],
    issue: ["citation_issue", "prism.number"],
    first_page: ["citation_firstpage", "prism.startingpage"],
    last_page: ["citation_lastpage", "prism.endingpage"],
    issn: ["citation_issn", "prism.issn", "dc.identifier.issn"],
  },
};

/**
 * Injected into the article tab, so it is self-contained: it sees only its
 * arguments and the page.
 */
export function probePageIdentity(keys, cap) {
  const declared = new Map();
  for (const node of document.querySelectorAll("meta[name], meta[property]")) {
    const key = String(node.getAttribute("name") || node.getAttribute("property") || "")
      .trim().toLowerCase().slice(0, 200);
    const value = String(node.content || node.getAttribute("content") || "").trim()
      .slice(0, cap.maxMetaValueChars);
    if (!key || !value) continue;
    if (!declared.has(key) && declared.size >= cap.maxMetaKeys) continue;
    if (!declared.has(key)) declared.set(key, []);
    if (declared.get(key).length < cap.maxMetaValues) declared.get(key).push(value);
  }
  const first = (names) => {
    for (const name of names) {
      const values = declared.get(name.toLowerCase());
      if (values && values[0]) return values[0];
    }
    return "";
  };
  const list = (names) => {
    for (const name of names) {
      const values = declared.get(name.toLowerCase());
      if (values && values.length) return values.slice(0, cap.maxAuthors);
    }
    return [];
  };
  // The first declared value that IS a DOI, not the first value of the first
  // key: Science declares `dc.Identifier` twice -- the publisher's own id
  // ("aec6129") and then the DOI -- and taking the first one left the DOI to be
  // guessed from the address.
  const declaredDoi = (names) => {
    for (const name of names) {
      for (const value of declared.get(name.toLowerCase()) || []) {
        if (/^(?:https?:\/\/(?:dx\.)?doi\.org\/|doi:\s*|info:doi\/)?10\.\d{4,9}\/\S+$/i.test(value)) return value;
      }
    }
    return "";
  };
  const canonical = document.querySelector('link[rel="canonical"]');
  const publisher = {};
  for (const [field, names] of Object.entries(keys.publisher_meta)) {
    const value = first(names);
    if (value) publisher[field] = value;
  }
  // A news page names its byline, day and publisher in JSON-LD and in no
  // <meta>. Asked only where the <meta> said nothing; never for a DOI.
  let article = null;
  for (const node of [...document.querySelectorAll('script[type="application/ld+json"]')].slice(0, 20)) {
    let data = null;
    try { data = JSON.parse(node.textContent || ""); } catch (_) { continue; }
    article = [].concat(data || []).flatMap((item) => (item && item["@graph"]) || item)
      .find((item) => item && typeof item === "object"
        && [].concat(item["@type"] || []).some((type) => /Article$|^BlogPosting$/.test(String(type))));
    if (article) break;
  }
  const named = (value) => [].concat(value || [])
    .map((item) => String((item && typeof item === "object" ? item.name : item) || "").trim()
      .slice(0, cap.maxMetaValueChars))
    .filter(Boolean).slice(0, cap.maxAuthors);
  const authors = list(keys.authors);
  const ldPublisher = named(article && article.publisher)[0];
  if (!publisher.publisher && ldPublisher) publisher.publisher = ldPublisher;
  return {
    url: location.href,
    canonical_url: (canonical && canonical.href) || "",
    // The serializer refuses a page the browser is translating; same test.
    translated: /\btranslated-(ltr|rtl)\b/.test(document.documentElement.className || "")
      || Boolean(document.querySelector('font[style*="vertical-align: inherit"]')),
    doi: declaredDoi(keys.doi),
    title: (first(keys.title) || document.title || "").slice(0, cap.maxTitleChars),
    date_published: (first(keys.date_published) || String((article && article.datePublished) || "").trim())
      .slice(0, 32),
    authors: authors.length ? authors : named(article && article.author),
    publisher_meta: publisher,
  };
}
