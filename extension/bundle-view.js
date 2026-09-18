"use strict";

// The side panel on the receiver's own reading page.
//
// A receiver that also serves a reading UI (the Corpus Reader is one) declares
// the work on screen the way a publisher's article page declares its article:
// Highwire `citation_*` <meta> in <head>, one set for the work shown and none
// when no work is shown. There is nothing to capture on such a tab -- the work
// is already in the corpus -- so the panel shows those declarations read-only,
// and the receiver's record for a declared DOI fills in what the page leaves
// out (a reading page rarely lists every author).
//
// `awaitBundleDeclaration` is injected into the page and sees only its
// arguments; everything else is pure and runs unchanged under node.

import { normalizeDoi } from "./capture.js";
import { assignedEnumeration, cleanAuthors, cleanMetadata, isoDay, mergeProposal } from "./review.js";

//: What the reading page may declare, first name wins. Standard Highwire and
//: Dublin Core names only: a receiver needs no vocabulary of ours to be read.
export const BUNDLE_DECLARATION_KEYS = {
  title: ["citation_title", "dc.title"],
  authors: ["citation_author", "dc.creator"],
  journal: ["citation_journal_title", "citation_inbook_title", "citation_conference_title"],
  published: ["citation_publication_date", "citation_date", "dc.date"],
  volume: ["citation_volume"],
  issue: ["citation_issue"],
  first_page: ["citation_firstpage"],
  last_page: ["citation_lastpage"],
  doi: ["citation_doi"],
  pmid: ["citation_pmid"],
  issn: ["citation_issn"],
  publisher: ["citation_publisher", "dc.publisher"],
  keywords: ["citation_keywords", "dc.subject"],
  type: ["dc.type"],
};

//: How long one wait for a change lasts before the panel asks again, and how
//: long the page must be quiet before a change counts: a page that clears its
//: declarations and writes the next set a moment later is one change, not two.
export const BUNDLE_WAIT_MS = 30000;
export const BUNDLE_SETTLE_MS = 120;
//: Names shown before the list is folded, and tags shown at all.
export const AUTHORS_SHOWN = 3;
export const MAX_TAGS = 30;

/** Whether `url` is a page of the configured receiver, which is where its reader lives. */
export function isReceiverPage(url, apiBase) {
  if (!apiBase) return false;
  try {
    const page = new URL(url);
    return /^https?:$/.test(page.protocol) && page.origin === new URL(apiBase).origin;
  } catch (_) {
    return false;
  }
}

/**
 * Injected into the reading page: its declarations now, or -- when they are
 * still what the panel last saw (`seen`) -- as soon as they change or `waitMs`
 * has passed. The answer is `{field: [values]}` for the fields declared.
 */
export function awaitBundleDeclaration(keys, cap, seen, waitMs, settleMs) {
  const read = () => {
    const declared = new Map();
    for (const node of document.querySelectorAll("meta[name]")) {
      const name = String(node.getAttribute("name") || "").trim().toLowerCase().slice(0, 200);
      const value = String(node.getAttribute("content") || "").trim().slice(0, cap.maxMetaValueChars);
      if (!name || !value) continue;
      if (!declared.has(name)) {
        if (declared.size >= cap.maxMetaKeys) continue;
        declared.set(name, []);
      }
      if (declared.get(name).length < cap.maxMetaValues) declared.get(name).push(value);
    }
    const out = {};
    for (const [field, names] of Object.entries(keys)) {
      for (const name of names) {
        const values = declared.get(name);
        if (values && values.length) {
          out[field] = values;
          break;
        }
      }
    }
    return out;
  };
  const now = read();
  if (seen === null || JSON.stringify(now) !== seen || !(waitMs > 0)) return now;
  return new Promise((resolve) => {
    let settle = 0;
    let deadline = 0;
    const finish = (value) => {
      observer.disconnect();
      clearTimeout(settle);
      clearTimeout(deadline);
      resolve(value);
    };
    const observer = new MutationObserver(() => {
      clearTimeout(settle);
      settle = setTimeout(() => {
        const next = read();
        if (JSON.stringify(next) !== seen) finish(next);
      }, settleMs);
    });
    observer.observe(document.head || document.documentElement, {
      childList: true, subtree: true, attributes: true, attributeFilter: ["name", "content"],
    });
    deadline = setTimeout(() => finish(read()), waitMs);
  });
}

function first(declared, field) {
  const values = declared && Array.isArray(declared[field]) ? declared[field] : [];
  return String(values[0] ?? "").replace(/\s+/g, " ").trim();
}

/** Whether the page declares a work at all. */
export function declaresWork(declared) {
  return Boolean(first(declared, "title") || first(declared, "doi"));
}

/** The page's declarations in the review's nine fields, so the receiver's record merges in. */
export function declaredMetadata(declared) {
  const firstPage = first(declared, "first_page");
  const lastPage = first(declared, "last_page");
  const { volume, issue } = assignedEnumeration(first(declared, "volume"), first(declared, "issue"));
  return cleanMetadata({
    title: first(declared, "title"),
    authors: (declared && declared.authors) || [],
    journal: first(declared, "journal"),
    published: isoDay(first(declared, "published")),
    volume,
    issue,
    pages: firstPage && lastPage && firstPage !== lastPage ? `${firstPage}-${lastPage}` : firstPage,
    issn: first(declared, "issn"),
    publisher: first(declared, "publisher"),
  });
}

/** Authors as a line that fits: every name up to four, else the first three and the last. */
export function authorLine(authors) {
  const names = cleanAuthors(authors);
  if (names.length <= AUTHORS_SHOWN + 1) return { short: names.join(", "), total: names.length, folded: false };
  return {
    short: `${names.slice(0, AUTHORS_SHOWN).join(", ")} … ${names[names.length - 1]}`,
    total: names.length,
    folded: true,
  };
}

/**
 * Where the work appeared, in citation order: `Nature · 2026-09-02;657(8130):47-58`.
 * An article with no volume yet keeps its pages after a colon (`2026:1-27`), as a
 * citation of an ahead-of-print article does. The publisher stands in for a
 * container only when there is none (a book).
 */
export function sourceParts(values) {
  const container = values.journal || values.publisher || "";
  let where = values.volume ? `;${values.volume}${values.issue ? `(${values.issue})` : ""}` : "";
  if (values.pages) where += `:${values.pages}`;
  const when = `${values.published || ""}${where}`.replace(/^[;:]/, "");
  return { container, when };
}

export function sourceLine(values) {
  const { container, when } = sourceParts(values);
  return [container, when].filter(Boolean).join(" · ");
}

/**
 * What the panel shows for the declared work. `identity` is the receiver's
 * answer for the declared DOI (`GET /api/v1/capture/identity`), or null.
 */
export function bundleCard(declared, identity = null) {
  const merged = mergeProposal(declaredMetadata(declared), identity && identity.metadata ? identity : null).values;
  const pmid = first(declared, "pmid");
  const seen = new Set();
  const tags = [];
  for (const raw of (declared && declared.keywords) || []) {
    const tag = String(raw ?? "").replace(/\s+/g, " ").trim().slice(0, 80);
    if (!tag || seen.has(tag.toLowerCase())) continue;
    seen.add(tag.toLowerCase());
    tags.push(tag);
    if (tags.length >= MAX_TAGS) break;
  }
  const { container, when } = sourceParts(merged);
  return {
    title: merged.title,
    authors: merged.authors,
    source: sourceLine(merged),
    container,
    when,
    doi: normalizeDoi(first(declared, "doi")) || "",
    pmid: /^\d{1,9}$/.test(pmid) ? pmid : "",
    type: first(declared, "type").slice(0, 60),
    tags,
  };
}
