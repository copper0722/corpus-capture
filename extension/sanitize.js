"use strict";

// What a stored capture is allowed to contain.
//
// The artifact outlives the browser that made it. Somebody opens it months
// later -- from a receiver, from a download directory, from a reader UI that
// may or may not have got its Content-Security-Policy right -- and whatever is
// in the file is what runs. Removing `<script>` and `on*` is not enough for
// that: `<base>` rewrites every relative URL, `<meta http-equiv="refresh">`
// navigates, a `<form>` posts, `<source srcset>` and `<link rel=stylesheet>`
// fetch, and SVG carries its own script surface.
//
// So this is an ALLOWLIST. An element nobody listed is not kept because nobody
// thought of it; it is unwrapped, and its text survives. The handful that carry
// their own payload are removed outright, children and all.
//
// This runs in the extension, over the DOM parsed from what the page handed
// back. The in-page serializer does a coarse strip first, but only for size:
// this pass is the one that decides what the artifact contains.

//: Removed with their subtree: an element whose content IS the payload.
export const DROP_ELEMENTS = new Set([
  "script", "noscript", "iframe", "frame", "frameset", "object", "embed",
  "applet", "template", "base", "link", "svg", "math", "canvas", "audio",
  "video", "source", "track", "form", "input", "button", "select", "option",
  "optgroup", "textarea", "fieldset", "legend", "dialog", "portal", "slot",
]);

//: Kept. Structure, text, tables, figures. Anything absent is unwrapped, so an
//: unknown custom element loses its tag and keeps its words.
export const ALLOWED_ELEMENTS = new Set([
  "html", "head", "title", "style", "body",
  "article", "section", "aside", "nav", "header", "footer", "main", "div", "span",
  "h1", "h2", "h3", "h4", "h5", "h6", "p", "br", "hr", "pre", "blockquote",
  "ul", "ol", "li", "dl", "dt", "dd",
  "table", "caption", "colgroup", "col", "thead", "tbody", "tfoot", "tr", "th", "td",
  "figure", "figcaption", "img", "picture",
  "a", "em", "strong", "b", "i", "u", "s", "small", "sub", "sup", "mark", "q",
  "cite", "code", "kbd", "samp", "var", "abbr", "time", "data", "del", "ins",
  "ruby", "rt", "rp", "wbr", "bdi", "bdo", "details", "summary", "address",
]);

//: Attributes with no behaviour: identity, presentation and table geometry.
export const GLOBAL_ATTRIBUTES = new Set([
  "id", "class", "lang", "dir", "title", "translate", "style",
]);
export const ELEMENT_ATTRIBUTES = {
  a: ["href", "rel", "target"],
  img: ["src", "alt", "width", "height"],
  td: ["colspan", "rowspan", "headers"],
  th: ["colspan", "rowspan", "headers", "scope", "abbr"],
  col: ["span"], colgroup: ["span"],
  ol: ["start", "reversed", "type"], li: ["value"],
  time: ["datetime"], data: ["value"], del: ["datetime"], ins: ["datetime"],
  details: ["open"], bdo: ["dir"],
  // A `<meta>` survives only for what it DECLARES; see keepMeta below.
  meta: ["name", "property", "content", "charset"],
};
//: `aria-*` and `data-*` are inert and carry the page's own semantics.
const ATTRIBUTE_PREFIXES = ["aria-", "data-"];
//: Attributes that hold a URL, checked rather than trusted.
const URL_ATTRIBUTES = new Set(["href", "src"]);
//: Schemes a stored artifact may still point at. `data:` is how an embedded
//: figure exists at all; the rest are ordinary links a reader may follow.
const SAFE_SCHEMES = new Set(["https:", "http:", "mailto:", "data:"]);

//: The asset placeholder the in-page serializer emits. It is not a URL yet, so
//: the URL check has to know about it or every figure would be stripped before
//: its bytes arrived.
const ASSET_TOKEN = /^corpus-asset-[0-9a-f]{8,32}-\d+-end$/;

export function isSafeUrlValue(value) {
  const text = String(value || "").trim();
  if (!text) return false;
  if (ASSET_TOKEN.test(text)) return true;
  if (text.startsWith("#")) return true;
  // A relative URL cannot be resolved once `<base>` is gone, but it is also
  // inert: nothing here turns it into a request. It is kept as written.
  if (/^[a-z][a-z0-9+.-]*:/i.test(text)) {
    try {
      return SAFE_SCHEMES.has(new URL(text).protocol);
    } catch (_) {
      return false;
    }
  }
  return !/^\s*(javascript|vbscript|data)\s*:/i.test(text);
}

/**
 * CSS that cannot fetch, cannot navigate and cannot run.
 *
 * Applies to a fetched stylesheet, to a `<style>` element's text, and to a
 * `style` attribute -- the same rule in all three places, because the reader
 * that opens the artifact does not care which of them a payload arrived in.
 */
export function sanitizeCss(css, baseUrl) {
  let text = String(css || "");
  // An @import is a network request the reader never asked for, and it is the
  // one CSS construct that can pull in a whole second stylesheet.
  text = text.replace(/@import[^;{]*(;|(?=\{))/gi, "");
  text = text.replace(/expression\s*\(/gi, "void(");
  text = text.replace(/(behavior|-moz-binding)\s*:[^;}]*/gi, "");
  // Serializing a <style> element does NOT escape its text: the HTML parser
  // ends the element at the first `</style`, and no escape exists in CSS to
  // stop it. Building the DOM removes every other substitution hazard, but this
  // one survives serialization, so the sequence is neutralized here -- in every
  // case, which is what the old lowercase-only guard got wrong.
  text = text.replace(/<\/(?=\s*(style|script)\b)/gi, "<\\/");
  text = text.replace(/url\(\s*(['"]?)([^'")]*)\1\s*\)/gi, (whole, quote, href) => {
    const value = String(href || "").trim();
    if (/^\s*(javascript|vbscript)\s*:/i.test(value)) return "url(about:blank)";
    if (/^data:/i.test(value) || value.startsWith("#")) return whole;
    if (!baseUrl) return whole;
    try {
      // Absolutized so the stored bytes say where the reference pointed rather
      // than resolving, later, against whatever origin serves the artifact.
      return `url(${quote}${new URL(value, baseUrl).href}${quote})`;
    } catch (_) {
      return whole;
    }
  });
  return text;
}

function keepMeta(node) {
  // `http-equiv` is the navigating, policy-setting half of `<meta>`; refresh
  // lives there. What the corpus reads out of a stored capture is the
  // declaring half: citation_*, dc.*, prism.*, og:*, and the charset.
  if (node.hasAttribute("http-equiv")) return false;
  return node.hasAttribute("charset") || node.hasAttribute("name") || node.hasAttribute("property");
}

function allowedAttribute(tag, name) {
  if (ATTRIBUTE_PREFIXES.some((prefix) => name.startsWith(prefix))) return true;
  if (GLOBAL_ATTRIBUTES.has(name)) return true;
  return (ELEMENT_ATTRIBUTES[tag] || []).includes(name);
}

/**
 * Rewrite `doc` in place so that what it contains is inert. Returns a count per
 * action, because "the sanitizer ran and removed nothing" and "the sanitizer
 * did not run" must not look the same in a test.
 */
export function sanitizeDocument(doc, { baseUrl = "" } = {}) {
  const removed = { elements: 0, attributes: 0, unwrapped: 0 };
  const root = doc.documentElement || doc.body;
  if (!root) return removed;

  for (const node of [...root.querySelectorAll("*")]) {
    if (!node.isConnected) continue;
    const tag = node.tagName.toLowerCase();
    if (tag === "meta") {
      // Handled by what it declares rather than by its name: see keepMeta.
      if (!keepMeta(node)) { node.remove(); removed.elements += 1; continue; }
    } else if (DROP_ELEMENTS.has(tag)) {
      node.remove();
      removed.elements += 1;
      continue;
    } else if (!ALLOWED_ELEMENTS.has(tag)) {
      // Unwrap: an unknown tag is not evidence of anything, but the text inside
      // it is the article.
      node.replaceWith(...node.childNodes);
      removed.unwrapped += 1;
      continue;
    }
    for (const attribute of [...node.attributes]) {
      const name = attribute.name.toLowerCase();
      if (!allowedAttribute(tag, name)) {
        node.removeAttribute(attribute.name);
        removed.attributes += 1;
        continue;
      }
      if (URL_ATTRIBUTES.has(name) && !isSafeUrlValue(attribute.value)) {
        node.removeAttribute(attribute.name);
        removed.attributes += 1;
        continue;
      }
      if (name === "style") {
        node.setAttribute("style", sanitizeCss(attribute.value, baseUrl));
      }
      if (name === "target") node.setAttribute("rel", "noreferrer noopener");
    }
  }
  for (const style of [...root.querySelectorAll("style")]) {
    style.textContent = sanitizeCss(style.textContent, baseUrl);
  }
  return removed;
}

/** The serialized artifact, doctype included. */
export function serializeDocument(doc) {
  return `<!doctype html>\n${doc.documentElement.outerHTML}`;
}
