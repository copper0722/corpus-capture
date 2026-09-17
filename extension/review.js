"use strict";

// The reader's review of a capture's identity, before the receiver publishes it.
//
// A producer hands over bytes and observations, never an identity (see
// docs/protocol.md). What the reader confirms or corrects here is therefore one
// more observation -- `reader_review` in the sidecar -- that a receiver weighs
// when it decides which work this is. Nothing in this module talks to Chrome or
// the network, so it runs unchanged under node.

import { normalizeDoi } from "./capture.js";

//: The fields the review shows, in the order it shows them. `authors` is a list;
//: every other value is one line of text.
export const METADATA_FIELDS = [
  "title", "authors", "journal", "published", "volume", "issue", "pages", "issn", "publisher",
];

export const FIELD_LABELS = {
  doi: "DOI",
  title: "標題",
  authors: "作者（一行一位）",
  journal: "期刊",
  published: "出版日期",
  volume: "卷",
  issue: "期",
  pages: "頁碼",
  issn: "ISSN",
  publisher: "出版者",
};

export const DOI_SOURCE_LABELS = {
  page_meta: "頁面宣告（citation_doi 等）",
  canonical_url: "由 canonical 網址推得",
  url: "由頁面網址推得",
  none: "頁面沒有宣告 DOI",
};

//: Bounds that keep a review small enough for the finalize body (keepalive
//: requests are limited to 64 KB) and for a sidecar a person can read.
export const REVIEW_LIMITS = { text: 1000, authors: 100, author: 300 };

function clip(value, max = REVIEW_LIMITS.text) {
  return String(value ?? "").replace(/\s+/g, " ").trim().slice(0, max);
}

export function cleanAuthors(value) {
  const list = Array.isArray(value) ? value : String(value ?? "").split(/\r?\n/);
  return list
    .map((name) => clip(name, REVIEW_LIMITS.author))
    .filter(Boolean)
    .slice(0, REVIEW_LIMITS.authors);
}

/** Normalize one metadata record to exactly METADATA_FIELDS. */
export function cleanMetadata(record) {
  const source = record && typeof record === "object" ? record : {};
  const out = {};
  for (const field of METADATA_FIELDS) {
    out[field] = field === "authors" ? cleanAuthors(source[field]) : clip(source[field]);
  }
  return out;
}

/** What the page itself declared, in review form. */
export function pageMetadata(capture) {
  const meta = (capture && capture.publisher_meta) || {};
  const first = clip(meta.first_page);
  const last = clip(meta.last_page);
  return cleanMetadata({
    title: meta.title || (capture && capture.title),
    authors: (capture && capture.authors) || [],
    journal: meta.journal,
    published: meta.publication_date || (capture && capture.date_published),
    volume: meta.volume,
    issue: meta.issue,
    pages: first && last && first !== last ? `${first}-${last}` : first,
    issn: meta.issn,
    publisher: meta.publisher,
  });
}

function same(a, b) {
  return JSON.stringify(a) === JSON.stringify(b);
}

/**
 * Combine the page's declarations with what the receiver resolved.
 *
 * The receiver's value wins where it has one: it comes from a registration
 * record or the corpus itself, while the page is only what the publisher chose
 * to print. The page's value is kept as the alternative whenever the two
 * disagree, so the reader sees the disagreement instead of a silent overwrite.
 */
export function mergeProposal(page, receiver) {
  const fromPage = cleanMetadata(page);
  const resolved = receiver && receiver.metadata ? cleanMetadata(receiver.metadata) : null;
  const sourceName = (receiver && receiver.metadata_source) || "receiver";
  const values = {};
  const sources = {};
  const alternatives = {};
  for (const field of METADATA_FIELDS) {
    const pageValue = fromPage[field];
    const receiverValue = resolved ? resolved[field] : (field === "authors" ? [] : "");
    const hasReceiver = field === "authors" ? receiverValue.length > 0 : Boolean(receiverValue);
    const hasPage = field === "authors" ? pageValue.length > 0 : Boolean(pageValue);
    if (hasReceiver) {
      values[field] = receiverValue;
      sources[field] = sourceName;
      if (hasPage && !same(pageValue, receiverValue)) {
        alternatives[field] = { source: "page", value: pageValue };
      }
    } else {
      values[field] = pageValue;
      sources[field] = hasPage ? "page" : "";
    }
  }
  return { values, sources, alternatives };
}

/** The fields whose final value differs from what the review first proposed. */
export function changedFields(proposed, final) {
  const changed = [];
  if ((normalizeDoi(proposed.doi) || null) !== (normalizeDoi(final.doi) || null)) changed.push("doi");
  const before = cleanMetadata(proposed.metadata);
  const after = cleanMetadata(final.metadata);
  for (const field of METADATA_FIELDS) {
    if (!same(before[field], after[field])) changed.push(field);
  }
  return changed;
}

/**
 * The observation the finalize call carries.
 *
 * `detected_doi` is what the capture found on the page; `doi` is what the
 * reader left in the field. A receiver can tell a confirmation from a
 * correction without trusting the `decision` label alone.
 */
export function buildReaderReview({ detectedDoi, proposed, final, reviewedAt = new Date() }) {
  const doiText = String(final.doi ?? "").trim();
  const doi = doiText ? normalizeDoi(doiText) : null;
  if (doiText && !doi) throw new Error("doi_invalid");
  const changed = changedFields(proposed, { doi, metadata: final.metadata });
  return {
    decision: changed.length ? "corrected" : "confirmed",
    doi,
    detected_doi: normalizeDoi(detectedDoi) || null,
    metadata: cleanMetadata(final.metadata),
    changed,
    reviewed_at: reviewedAt.toISOString(),
  };
}
