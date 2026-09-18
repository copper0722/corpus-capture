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
  authors: "作者",
  journal: "期刊",
  published: "日期",
  volume: "卷",
  issue: "期",
  pages: "頁",
  issn: "ISSN",
  publisher: "出版者",
};

//: Said only when the DOI is NOT the page's own declaration: that is the normal
//: case, and a line repeating it on every article is a line nobody reads.
export const DOI_SOURCE_LABELS = {
  page_meta: "",
  canonical_url: "DOI 由 canonical 網址推得",
  url: "DOI 由網址推得",
  none: "頁面無 DOI",
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

//: What a page prints for volume and issue before the article is in an issue:
//: Human Kinetics declares "-1" and "aop", Taylor & Francis "0" and "0". The
//: receiver applies the same rule (corpus-ops `assigned_enumeration`).
export const UNASSIGNED_ENUMERATION = new Set([
  "aop", "ahead of print", "ahead-of-print", "online ahead of print", "epub ahead of print",
  "online first", "online-first", "onlinefirst", "in press", "inpress", "articles in press",
  "early view", "earlyview", "early access", "just accepted", "forthcoming", "latest articles",
  "n/a", "na", "none", "null", "nil", "undefined", "nan", "tbd", "tba", "-", "--", "?",
]);

/**
 * The volume and issue a citation can print; "" for a placeholder.
 *
 * A volume of 0 is a placeholder. An issue of 0 is one only beside a
 * placeholder volume: registries record real supplement issues as 0.
 */
export function assignedEnumeration(volume, issue) {
  const unassigned = (text) => UNASSIGNED_ENUMERATION.has(text.toLowerCase()) || /^-\s*\d+$/.test(text);
  const vol = clip(volume);
  const iss = clip(issue);
  const assignedVolume = !vol || unassigned(vol) || /^0+$/.test(vol) ? "" : vol;
  const assignedIssue = !iss || unassigned(iss) || (/^0+$/.test(iss) && !assignedVolume) ? "" : iss;
  return { volume: assignedVolume, issue: assignedIssue };
}

/**
 * A day written the way Google Scholar asks pages to write it (2026/09/15),
 * as an ISO date. Anything that is not a whole, real day is returned as given.
 */
export function isoDay(value) {
  const text = clip(value);
  const match = /^(\d{4})[/-](\d{1,2})[/-](\d{1,2})$/.exec(text);
  if (!match) return text;
  const [year, month, day] = match.slice(1).map(Number);
  const date = new Date(Date.UTC(year, month - 1, day));
  if (date.getUTCFullYear() !== year || date.getUTCMonth() !== month - 1 || date.getUTCDate() !== day) {
    return text;
  }
  return `${match[1]}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
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
  const { volume, issue } = assignedEnumeration(meta.volume, meta.issue);
  return cleanMetadata({
    title: meta.title || (capture && capture.title),
    authors: (capture && capture.authors) || [],
    journal: meta.journal,
    published: isoDay(meta.publication_date || (capture && capture.date_published)),
    volume,
    issue,
    pages: first && last && first !== last ? `${first}-${last}` : first,
    issn: meta.issn,
    publisher: meta.publisher,
  });
}

function same(a, b) {
  return JSON.stringify(a) === JSON.stringify(b);
}

// "2026-09-15" says everything "2026" says, and more.
function refines(precise, coarse) {
  return precise.length > coarse.length && precise.startsWith(coarse)
    && /^[-/]/.test(precise.slice(coarse.length));
}

/**
 * Combine the page's declarations with what the receiver resolved.
 *
 * The receiver's value wins where it has one: it comes from a registration
 * record or the corpus itself, while the page is only what the publisher chose
 * to print. The page's value is kept as the alternative whenever the two
 * disagree, so the reader sees the disagreement instead of a silent overwrite.
 * A date is the one exception: a registry that knows only the year does not
 * disagree with the page's day in that year, it knows less.
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
    if (field === "published" && hasReceiver && hasPage && refines(pageValue, receiverValue)) {
      values[field] = pageValue;
      sources[field] = "page";
    } else if (hasReceiver) {
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
