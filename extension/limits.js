"use strict";

// Every number that stops a page from deciding how much work this does.
//
// A capture is driven entirely by a document the reader does not control. The
// asset list, the figure list, the metadata keys, the size of any single
// response and the size of the finished artifact are all page-supplied, and
// without ceilings each of them is an invitation: a thousand `<img>` elements
// is a thousand sequential authenticated requests, and one asset with no
// `content-length` can be a stream that never ends.
//
// They live in one module because two of the consumers are in different
// worlds: the serializer runs inside the page (injected as a function, so it
// cannot import anything and receives this object as an argument), and the
// inliner runs in the extension. One table, passed across, rather than two that
// drift.
export const LIMITS = {
  //: Assets claimed from one page. Beyond this the capture is still made; the
  //: extra references are dropped and counted, because a page with 400 images
  //: is a page, and a page with 40,000 is an attack on the fetch loop.
  maxAssets: 300,
  //: Figures in the manifest. A real article has tens.
  maxFigures: 200,
  //: One asset. Checked against `content-length` BEFORE the body is read, and
  //: again while streaming, because a declared length is a claim.
  maxAssetBytes: 8 * 1024 * 1024,
  //: Everything inlined, together. The receiver refuses a payload over 32 MiB;
  //: stopping below that produces a large capture rather than a rejected one.
  maxInlineBytes: 24 * 1024 * 1024,
  //: The serialized document before anything is inlined. A page past this is
  //: refused with a reason rather than silently truncated into broken markup.
  maxDocumentChars: 16 * 1024 * 1024,
  //: The artifact that gets posted, as UTF-8 bytes.
  maxPayloadBytes: 32 * 1024 * 1024,
  //: Distinct <meta> keys, values per key, and the length of one value.
  maxMetaKeys: 200,
  maxMetaValues: 100,
  maxMetaValueChars: 2000,
  //: One caption, one alt, one title.
  maxCaptionChars: 2000,
  maxAltChars: 500,
  maxTitleChars: 500,
  //: Authors named by the page.
  maxAuthors: 100,
  //: How long one asset may take before the capture moves on without it.
  assetTimeoutMs: 20000,
};
