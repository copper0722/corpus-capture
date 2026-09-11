"""The publisher capture registry: one set of selectors, three consumers.

Purpose
-------
The browser extension, the acquisition script and the intake sidecar mapping all
need to know where a publisher puts its article body, its figures and its
identity. Written three times they drift, and the one that drifts silently is the
one nobody runs by hand. So the selectors are DATA -- ``capture_profiles.json``
-- and this module is the only thing that reads them.

`status` is a measurement, not a plan. A profile is `supported` when a committed
fixture exists and a test asserts the container, the identity and the figure
manifest against it; `generic` when only the fallback selectors are proven; and
`unsupported` only with a `reason` naming what was observed. Nothing here may be
promoted by intending to test it later.

Inputs
------
``profiles/capture_profiles.json``, and a URL to resolve.

Outputs
-------
Validated profile records, the merged effective profile for a URL, and the
public projection the extension fetches.

State changes
-------------
None.

Failure behavior
----------------
A registry that violates the schema raises :class:`ProfileRegistryError` at load.
A URL that matches nothing resolves to the generic profile, never to an error:
capture must still work on a site nobody has profiled.

Public entrypoints
------------------
``load_registry()``, ``profile_for_url()``, ``public_registry()``,
``fixture_path()``.

Related tests
-------------
``tests/test_capture_profiles.py``.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


def _registry_path() -> Path:
    """Where the registry is, whether this is a checkout or an installed wheel.

    The registry is `profiles/capture_profiles.json` at the repository root --
    one file, versioned beside the extension that consumes it. The wheel carries
    the same bytes next to this module, because an installed package cannot see
    the checkout it came from. Neither copy is edited: the build maps one onto
    the other.
    """

    override = os.environ.get("CORPUS_CAPTURE_PROFILES")
    if override:
        return Path(override)
    packaged = Path(__file__).with_name("capture_profiles.json")
    if packaged.is_file():
        return packaged
    return Path(__file__).resolve().parents[2] / "profiles" / "capture_profiles.json"


REGISTRY_PATH = _registry_path()
SCHEMA_VERSION = "capture-profiles-v1"
STATUSES = ("supported", "generic", "unsupported")

#: Every key a profile may carry. Unknown keys fail closed: a typo in a selector
#: name is otherwise a profile that silently does nothing.
PROFILE_KEYS = {
    "id", "display_name", "host_patterns", "article_container_selectors",
    "figure_selectors", "caption_selectors", "meta_sources", "doi_source",
    "series_source", "access_markers", "status", "fixture", "notes", "reason",
    "drop_selectors", "drop_asset_hosts", "drop_asset_patterns",
    "decorative_asset_patterns", "unnumbered_asset_patterns", "asset_origins",
}
REQUIRED_KEYS = {
    "id", "display_name", "host_patterns", "article_container_selectors",
    "figure_selectors", "status",
}
#: Inherited from the generic profile when a publisher profile omits them, so a
#: profile only has to state what it does DIFFERENTLY.
INHERITED_KEYS = (
    "article_container_selectors", "figure_selectors", "caption_selectors",
    "meta_sources", "doi_source", "series_source", "access_markers",
    "drop_selectors", "decorative_asset_patterns", "unnumbered_asset_patterns",
    "asset_origins",
)


class ProfileRegistryError(ValueError):
    """The registry does not satisfy its own schema."""


def _require_str_list(record: dict[str, Any], key: str, *, where: str) -> None:
    value = record.get(key)
    if value is None:
        return
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ProfileRegistryError(f"{where}.{key} must be a list of non-empty strings")


def validate_registry(registry: dict[str, Any]) -> dict[str, Any]:
    """Check the registry end to end, or raise. Returns it unchanged."""

    if registry.get("schema_version") != SCHEMA_VERSION:
        raise ProfileRegistryError("schema_version is not the supported one")
    generic = registry.get("generic")
    if not isinstance(generic, dict) or generic.get("id") != "generic":
        raise ProfileRegistryError("the generic fallback profile is missing")
    profiles = registry.get("profiles")
    if not isinstance(profiles, list) or not profiles:
        raise ProfileRegistryError("profiles must be a non-empty list")

    seen: set[str] = set()
    for profile in profiles:
        if not isinstance(profile, dict):
            raise ProfileRegistryError("every profile must be an object")
        identifier = str(profile.get("id") or "")
        where = f"profile[{identifier or '?'}]"
        missing = REQUIRED_KEYS - set(profile)
        if missing:
            raise ProfileRegistryError(f"{where} is missing {sorted(missing)}")
        unknown = set(profile) - PROFILE_KEYS
        if unknown:
            raise ProfileRegistryError(f"{where} has unknown keys {sorted(unknown)}")
        if identifier in seen or identifier == "generic":
            raise ProfileRegistryError(f"{where} duplicates an id")
        seen.add(identifier)
        if profile["status"] not in STATUSES:
            raise ProfileRegistryError(f"{where}.status is not one of {STATUSES}")
        for key in (
            "host_patterns", "article_container_selectors", "figure_selectors",
            "caption_selectors", "meta_sources", "doi_source", "series_source",
            "access_markers", "drop_selectors", "drop_asset_hosts",
            "drop_asset_patterns", "decorative_asset_patterns",
            "unnumbered_asset_patterns", "asset_origins",
        ):
            _require_str_list(profile, key, where=where)
        # The rule that keeps `status` honest. A claim of support with no fixture
        # is an intention, and an intention is what this field exists not to be.
        if profile["status"] == "supported" and not str(profile.get("fixture") or "").strip():
            raise ProfileRegistryError(f"{where} claims support without a fixture")
        if profile["status"] == "unsupported" and not str(profile.get("reason") or "").strip():
            raise ProfileRegistryError(f"{where} is unsupported without a measured reason")
    return registry


@lru_cache(maxsize=1)
def load_registry(path: str | None = None) -> dict[str, Any]:
    """Read and validate the registry once per process."""

    source = Path(path) if path else REGISTRY_PATH
    try:
        registry = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProfileRegistryError(f"registry unreadable: {type(exc).__name__}") from exc
    return validate_registry(registry)


def fixture_path(profile: dict[str, Any]) -> Path | None:
    """Where the fixture that proves ``supported`` lives, or None.

    Paths in the registry are relative to the REGISTRY's own repository root,
    never to the caller's. This project vendors into other repositories as a
    subtree, and a path resolved against the consumer's root points at nothing
    there -- which reads as "the fixture is missing" and turns a passing
    measurement into a failing one for the wrong reason.
    """

    relative = (profile or {}).get("fixture")
    if not relative:
        return None
    return REGISTRY_PATH.resolve().parents[1] / str(relative)


def _host_matches(host: str, pattern: str) -> bool:
    host, pattern = host.lower().lstrip("."), pattern.lower().lstrip(".")
    if pattern.startswith("*."):
        suffix = pattern[2:]
        return host == suffix or host.endswith("." + suffix)
    return host == pattern or host.endswith("." + pattern)


def profile_for_url(url: str, *, registry: dict[str, Any] | None = None) -> dict[str, Any]:
    """The effective profile for a URL, generic values filled in.

    A miss is not an error. Capture on an unprofiled site still works through the
    fallback selectors, and the sidecar records `profile=generic` so nobody later
    mistakes a fallback capture for a tested one.
    """

    registry = registry or load_registry()
    generic = dict(registry["generic"])
    host = (urlsplit(str(url or "")).hostname or "").lower()
    for profile in registry["profiles"]:
        if any(_host_matches(host, pattern) for pattern in profile.get("host_patterns", [])):
            merged = {**generic, **{k: v for k, v in profile.items() if v is not None}}
            for key in INHERITED_KEYS:
                if key not in profile or profile.get(key) is None:
                    merged[key] = generic.get(key, [])
            merged["matched"] = True
            return merged
    return {**generic, "status": "generic", "matched": False, "fixture": None}


def public_registry(registry: dict[str, Any] | None = None) -> dict[str, Any]:
    """The projection the extension fetches. Data only, no local paths.

    `fixture` is deliberately dropped: it is a repository path, and this
    projection is served to a browser extension that will be published.
    """

    registry = registry or load_registry()
    return {
        "schema_version": registry["schema_version"],
        "generic": {k: v for k, v in registry["generic"].items() if k != "fixture"},
        "profiles": [
            {k: v for k, v in profile.items() if k != "fixture"}
            for profile in registry["profiles"]
        ],
    }


def profile_status_table(registry: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """One row per profile: what it is, whether it is proven, and why not."""

    registry = registry or load_registry()
    return [
        {
            "id": profile["id"],
            "display_name": profile["display_name"],
            "status": profile["status"],
            "hosts": list(profile.get("host_patterns") or []),
            "fixture": profile.get("fixture"),
            "reason": profile.get("reason"),
        }
        for profile in registry["profiles"]
    ]
