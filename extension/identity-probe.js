"use strict";

// The side panel's preview: what the open page declares about its identity,
// read without capturing anything. Only <meta>, <link rel="canonical"> and the
// address are touched -- no serialization, no asset fetch -- so it can run on
// every tab the reader switches to.
//
// The key lists are the serializer's own (serialize.js); a test pins them, so
// the preview never shows an identity the saved capture would not carry.

export const DECLARATION_KEYS = {
  doi: ["citation_doi", "dc.identifier", "dc.identifier.doi", "prism.doi", "bepress_citation_doi"],
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
  const canonical = document.querySelector('link[rel="canonical"]');
  const publisher = {};
  for (const [field, names] of Object.entries(keys.publisher_meta)) {
    const value = first(names);
    if (value) publisher[field] = value;
  }
  return {
    url: location.href,
    canonical_url: (canonical && canonical.href) || "",
    doi: first(keys.doi),
    title: (first(keys.title) || document.title || "").slice(0, cap.maxTitleChars),
    date_published: first(keys.date_published).slice(0, 32),
    authors: list(keys.authors),
    publisher_meta: publisher,
  };
}
