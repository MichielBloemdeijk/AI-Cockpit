"""The `cockpit-app.v1` manifest: everything the platform knows about an app.

A package is a folder with `cockpit-app.json` at its root. Nothing about an app
is inferred from its code; the manifest is validated completely at install time
and stored with the installed version.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any, Literal

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

MANIFEST_FILE = "cockpit-app.json"
SLUG_PATTERN = r"^[a-z][a-z0-9-]{1,47}$"
NAME_PATTERN = r"^[a-z][a-z0-9_]{0,47}$"
FIELD_PATTERN = r"^[a-z_][a-z0-9_]{0,63}$"
# Manifest sections the design reserves for later build steps. An app that
# declares them must not install as if they worked.
UNSUPPORTED_SECTIONS = ("functions", "files", "triggers")
WORKFLOW_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"


class ManifestError(ValueError):
    """The package or its manifest is not installable."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UiSpec(_Strict):
    entry: str = Field(min_length=1, max_length=255)


class CollectionSpec(_Strict):
    schema_path: str = Field(alias="schema", min_length=1, max_length=255)
    indexes: list[str] = Field(default_factory=list, max_length=16)
    # Workflows may publish their results as records of this collection.
    result_type: bool = False

    @field_validator("indexes")
    @classmethod
    def index_fields_are_plain_names(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)) or not all(re.fullmatch(FIELD_PATTERN, value) for value in values):
            raise ValueError("Indexes must be unique top-level field names")
        return values


class GrantSpec(_Strict):
    read: list[str] = Field(default_factory=list)
    write: list[str] = Field(default_factory=list)


class JarvisSpec(_Strict):
    summary: str = Field(default="", max_length=500)
    tools: list[str] = Field(default_factory=list)


class AppManifest(_Strict):
    schema_id: Literal["cockpit-app.v1"] = Field(alias="schema")
    slug: str = Field(pattern=SLUG_PATTERN)
    title: str = Field(min_length=1, max_length=120)
    version: int = Field(ge=1)
    description: str = Field(default="", max_length=1000)
    ui: UiSpec
    collections: dict[str, CollectionSpec] = Field(default_factory=dict)
    grants: GrantSpec = Field(default_factory=GrantSpec)
    jarvis: JarvisSpec = Field(default_factory=JarvisSpec)
    # Ids of the workflows in workflows/<id>/, which publish into result collections.
    workflows: list[str] = Field(default_factory=list, max_length=16)

    @field_validator("workflows")
    @classmethod
    def workflow_ids_are_plain(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)) or not all(re.fullmatch(WORKFLOW_PATTERN, value) for value in values):
            raise ValueError("Workflows are unique lowercase identifiers")
        return values

    @field_validator("collections")
    @classmethod
    def collection_names_are_plain(cls, values: dict[str, CollectionSpec]) -> dict[str, CollectionSpec]:
        if not all(re.fullmatch(NAME_PATTERN, name) for name in values):
            raise ValueError("Collection names must be lowercase identifiers")
        return values


def _inside(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root.resolve()):
        raise ManifestError(f"Path escapes the package: {relative}")
    return candidate


def load_package(root: Path) -> tuple[AppManifest, dict[str, dict[str, Any]]]:
    """Validate a package folder; returns the manifest and each collection's JSON Schema.

    Its workflows are validated too; `package_workflows` returns their identities.
    """
    manifest_path = root / MANIFEST_FILE
    if not manifest_path.is_file():
        raise ManifestError(f"Package has no {MANIFEST_FILE}")
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"{MANIFEST_FILE} is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ManifestError(f"{MANIFEST_FILE} must be an object")
    unsupported = [section for section in UNSUPPORTED_SECTIONS if section in raw]
    if unsupported:
        raise ManifestError(f"Not supported by this platform version yet: {', '.join(unsupported)}")
    try:
        manifest = AppManifest.model_validate(raw)
    except ValidationError as exc:
        raise ManifestError(f"Invalid manifest: {exc.errors(include_url=False)}") from exc

    entry = _inside(root, manifest.ui.entry)
    if not entry.is_file() or not entry.is_relative_to((root / "ui").resolve()):
        raise ManifestError("ui.entry must be an existing file inside ui/")

    schemas: dict[str, dict[str, Any]] = {}
    for name, spec in manifest.collections.items():
        schema_file = _inside(root, spec.schema_path)
        try:
            schema = json.loads(schema_file.read_text(encoding="utf-8"))
            Draft202012Validator.check_schema(schema)
        except (OSError, json.JSONDecodeError, SchemaError) as exc:
            raise ManifestError(f"Collection {name} has an invalid schema: {exc}") from exc
        if schema.get("type") != "object":
            raise ManifestError(f"Collection {name} schema must describe an object")
        properties = schema.get("properties") or {}
        missing = [field for field in spec.indexes if field not in properties]
        if missing:
            raise ManifestError(f"Collection {name} indexes unknown fields: {', '.join(missing)}")
        if spec.result_type and ((properties.get("title") or {}).get("type") != "string"
                                 or "title" not in (schema.get("required") or [])):
            raise ManifestError(f"Result collection {name} must require a string title")
        schemas[name] = schema
    package_workflows(root, manifest)
    return manifest, schemas


def package_workflows(root: Path, manifest: AppManifest) -> dict[str, dict[str, Any]]:
    """Validate the package's workflows; returns {id: {"version", "digest"}}."""
    from app.services.task_workflows import load_package_workflow

    results = tuple(name for name, spec in manifest.collections.items() if spec.result_type)
    definitions: dict[str, dict[str, Any]] = {}
    for workflow_id in manifest.workflows:
        try:
            loaded = load_package_workflow(_inside(root, f"workflows/{workflow_id}"), workflow_id,
                                           app=manifest.slug, result_collections=results)
        except (ValueError, OSError) as exc:
            raise ManifestError(f"Workflow {workflow_id} is invalid: {exc}") from exc
        definitions[workflow_id] = {"version": loaded.version, "digest": loaded.digest}
    return definitions


def compatibility_problems(old: dict[str, dict[str, Any]], new: dict[str, dict[str, Any]]) -> list[str]:
    """Changes that would invalidate records already stored under the old schemas.

    A collection may gain optional fields. It may not disappear, lose a field,
    change a field's type, or require a field the old schema did not require.
    """
    problems: list[str] = []
    for name, old_schema in old.items():
        new_schema = new.get(name)
        if new_schema is None:
            problems.append(f"collection {name} was removed")
            continue
        old_props = old_schema.get("properties") or {}
        new_props = new_schema.get("properties") or {}
        for field, definition in old_props.items():
            if field not in new_props:
                problems.append(f"{name}.{field} was removed")
            elif (definition or {}).get("type") != (new_props[field] or {}).get("type"):
                problems.append(f"{name}.{field} changed type")
        added_required = set(new_schema.get("required") or []) - set(old_schema.get("required") or [])
        problems.extend(f"{name}.{field} became required" for field in sorted(added_required))
    return problems
