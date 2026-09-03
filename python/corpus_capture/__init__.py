"""corpus-capture: publisher profiles, figure selection, and the intake contract.

The browser extension in ``extension/`` is the producer. This package is what a
RECEIVER needs: the same publisher registry the extension fetches, the same
structural figure selection, and the sidecar contract both sides emit. Sharing
the code is the point -- a receiver that reimplements the selectors drifts from
the extension, and the copy that drifts silently is the one nobody runs by hand.
"""

from corpus_capture.figure_manifest import (
    FigureManifest,
    FigureRecord,
    build_figure_manifest,
    figure_label,
    find_article_container,
    is_decorative_asset,
)
from corpus_capture.profiles import (
    REGISTRY_PATH,
    SCHEMA_VERSION,
    ProfileRegistryError,
    fixture_path,
    load_registry,
    profile_for_url,
    profile_status_table,
    public_registry,
    validate_registry,
)
from corpus_capture.sidecar import (
    ACCESS_CLASSES,
    PRODUCER_KEY,
    SIDECAR_SCHEMA,
    SIDECAR_SCHEMAS_ACCEPTED,
    SidecarError,
    capture_slug,
    download_basename,
    normalize_doi,
    validate_sidecar,
)
from corpus_capture.submission import (
    ALLOWED_FIELDS,
    FORBIDDEN_FIELDS,
    MAX_BODY_BYTES,
    MAX_HTML_BYTES,
    MAX_METADATA_BYTES,
    ReceiptStore,
    SubmissionError,
    enforce_body_size,
    validate_submission,
)

__version__ = "0.1.2"

__all__ = [
    "ACCESS_CLASSES",
    "ALLOWED_FIELDS",
    "FORBIDDEN_FIELDS",
    "MAX_BODY_BYTES",
    "MAX_HTML_BYTES",
    "MAX_METADATA_BYTES",
    "PRODUCER_KEY",
    "REGISTRY_PATH",
    "SCHEMA_VERSION",
    "SIDECAR_SCHEMA",
    "SIDECAR_SCHEMAS_ACCEPTED",
    "FigureManifest",
    "FigureRecord",
    "ProfileRegistryError",
    "ReceiptStore",
    "SidecarError",
    "SubmissionError",
    "__version__",
    "build_figure_manifest",
    "capture_slug",
    "download_basename",
    "enforce_body_size",
    "figure_label",
    "find_article_container",
    "fixture_path",
    "is_decorative_asset",
    "load_registry",
    "normalize_doi",
    "profile_for_url",
    "profile_status_table",
    "public_registry",
    "validate_registry",
    "validate_sidecar",
    "validate_submission",
]
