"""The store record moves with the manifest, or the build says so."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_every_requested_permission_is_justified():
    manifest = json.loads((ROOT / "extension" / "manifest.json").read_text(encoding="utf-8"))
    record = (ROOT / "CHROMEWEBSTORE.md").read_text(encoding="utf-8")
    requested = list(manifest.get("permissions", [])) + list(manifest.get("host_permissions", []))
    if manifest.get("commands"):
        requested.append("commands")
    missing = [name for name in requested if f"| `{name}`" not in record]
    assert not missing, f"CHROMEWEBSTORE.md has no justification for {missing}"


def test_the_listing_summary_is_the_manifest_description():
    manifest = json.loads((ROOT / "extension" / "manifest.json").read_text(encoding="utf-8"))
    record = (ROOT / "CHROMEWEBSTORE.md").read_text(encoding="utf-8")
    assert f"Summary: {manifest['description']}" in record
