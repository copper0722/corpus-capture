"""The registry validates against its published schema, and the schema bites.

Two validators describe the same registry: `profiles/schema.json`, which a
contributor's editor and CI can both read, and `validate_registry()`, which is
what actually runs at load time. They agree here or they are a trap: a
contributor fixes the file until the schema is happy and the loader still
refuses it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from corpus_capture.profiles import REGISTRY_PATH, ProfileRegistryError, validate_registry

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "profiles" / "schema.json"
jsonschema = pytest.importorskip("jsonschema")


@pytest.fixture(scope="module")
def validator():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    return jsonschema.Draft202012Validator(schema)


@pytest.fixture
def registry():
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


def test_the_shipped_registry_validates(validator, registry):
    errors = sorted(validator.iter_errors(registry), key=lambda error: error.path)
    assert not errors, [f"{list(error.path)}: {error.message}" for error in errors]


@pytest.mark.parametrize(
    ("mutate", "why"),
    [
        (lambda r: r["profiles"][0].update(figure_selector=["figure"]), "an unknown key"),
        (lambda r: r["profiles"][0].update(status="proven"), "a status outside the vocabulary"),
        (lambda r: r["profiles"][0].update(figure_selectors=["figure", ""]), "an empty selector"),
        (lambda r: r["profiles"][0].pop("host_patterns"), "a profile claiming no hosts"),
        (lambda r: r.update(schema_version="capture-profiles-v99"), "a version nobody implements"),
    ],
)
def test_both_validators_refuse_the_same_defects(validator, registry, mutate, why):
    mutate(registry)
    assert list(validator.iter_errors(registry)), f"the schema accepted {why}"
    with pytest.raises(ProfileRegistryError):
        validate_registry(registry)


def test_the_schema_refuses_a_claim_of_support_without_a_fixture(validator, registry):
    """The rule that keeps `status` a measurement rather than an intention."""

    supported = next(p for p in registry["profiles"] if p["status"] == "supported")
    supported["fixture"] = None
    assert list(validator.iter_errors(registry))
    with pytest.raises(ProfileRegistryError, match="fixture"):
        validate_registry(registry)
