"""Trusted result registrations shared by admission, MCP and publication.

A workflow publishes either a core result (a report, an app build) or a record
in an installed app's result collection, named `collection:<app>.<collection>`.
Collection results are validated against the app's own JSON Schema.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, ValidationError

COLLECTION_PREFIX = "collection:"
_COLLECTION_OUTPUT = re.compile(r"^collection:([a-z][a-z0-9-]{1,47})\.([a-z][a-z0-9_]{0,47})$")
# What a general agent publishes when asked for a maintained result.
DOCUMENT_OUTPUT = "collection:documents.document"
# Publication metadata the core keeps on the task, never in the app's record.
SOURCE_EVENTS_FIELD = "source_event_sequences"
_SOURCE_EVENTS_SCHEMA = {"type": "array", "items": {"type": "integer", "minimum": 1}, "maxItems": 64,
    "description": "Sequences of the task events this result is based on, from task_history."}


class _Content(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    title: StrictStr = Field(min_length=1, max_length=200)
    source_event_sequences: list[StrictInt] = Field(max_length=64)


class TextReport(_Content):
    body: StrictStr = Field(min_length=1, max_length=32_000)


class AppBuild(TextReport):
    """What a build_app run built or changed; publishing it installs the package."""
    slug: StrictStr = Field(min_length=2, max_length=48)
    version: StrictInt = Field(ge=1)


# Filled in from the installed package rather than by the builder.
INSTALL_FIELDS = ("slug", "version")


@dataclass(frozen=True)
class ResultType:
    model: type[_Content]
    label: str


RESULT_TYPES = {
    "text-report.v1": ResultType(TextReport, "Report"),
    "app-build.v1": ResultType(AppBuild, "App build"),
}


def collection_output(schema: str) -> tuple[str, str] | None:
    """(app slug, collection) for a collection result, None for a core result."""
    match = _COLLECTION_OUTPUT.fullmatch(schema) if isinstance(schema, str) else None
    return (match.group(1), match.group(2)) if match else None


def is_output_schema(schema: object) -> bool:
    return isinstance(schema, str) and (schema in RESULT_TYPES or collection_output(schema) is not None)


async def _collection_schema(schema: str) -> dict[str, Any]:
    from app import app_platform

    slug, collection = collection_output(schema)  # type: ignore[misc]
    try:
        app = await app_platform.get_active(slug)
    except app_platform.AppNotFound:
        raise ValueError(f"The {slug} app is not installed") from None
    spec = app.collections.get(collection)
    if spec is None or not spec.get("result_type"):
        raise ValueError(f"{slug} has no result collection {collection}")
    return spec["schema_json"]


async def publish_schema(schema: str) -> dict[str, Any]:
    """The JSON Schema a worker's result_publish arguments follow."""
    if collection_output(schema) is None:
        model_schema = RESULT_TYPES[schema].model.model_json_schema()
        if schema == "app-build.v1":
            model_schema["properties"] = {key: value for key, value in model_schema["properties"].items()
                                          if key not in INSTALL_FIELDS}
            model_schema["required"] = [key for key in model_schema["required"] if key not in INSTALL_FIELDS]
        return model_schema
    record_schema = await _collection_schema(schema)
    return {**record_schema, "properties": {**record_schema.get("properties", {}),
                                            SOURCE_EVENTS_FIELD: _SOURCE_EVENTS_SCHEMA}}


def result_label(schema: str) -> str:
    output = collection_output(schema)
    return output[1].replace("_", " ").capitalize() if output else RESULT_TYPES[schema].label


async def validate_result(schema: str, content: Any) -> dict[str, Any]:
    """Validate published content; collection results also satisfy the app's record schema."""
    if not isinstance(content, dict):
        raise ValueError("Workflow result must be an object")
    if collection_output(schema) is None:
        registered = RESULT_TYPES.get(schema)
        if registered is None:
            raise ValueError("Workflow result schema is not registered")
        try:
            parsed = registered.model.model_validate(content)
        except ValidationError as exc:
            raise ValueError(f"Workflow result does not match {registered.model.__name__}: {exc}") from exc
        result = parsed.model_dump(exclude_none=True, exclude_unset=True)
    else:
        from jsonschema import Draft202012Validator

        result = dict(content)
        sequences = result.setdefault(SOURCE_EVENTS_FIELD, [])
        if (not isinstance(sequences, list) or len(sequences) > 64
                or any(type(value) is not int for value in sequences)):
            raise ValueError("source_event_sequences must be a list of task event sequences")
        record = {key: value for key, value in result.items() if key != SOURCE_EVENTS_FIELD}
        error = next(iter(sorted(Draft202012Validator(await _collection_schema(schema)).iter_errors(record),
                                 key=lambda item: list(item.path))), None)
        if error is not None:
            location = "/".join(str(part) for part in error.path) or "(record)"
            raise ValueError(f"Workflow result is invalid at {location}: {error.message}")
    return _common_checks(result)


def _common_checks(result: dict[str, Any]) -> dict[str, Any]:
    if re.search(r"(?:turn\d+(?:search|view|fetch)\d+|cite|)", str(result)):
        raise ValueError("Replace unresolved provider citation markers with usable source URL links")
    for field in ("title", "body", "summary"):
        if isinstance(result.get(field), str):
            result[field] = result[field].strip()
    if not isinstance(result.get("title"), str) or not result["title"] or ("body" in result and not result["body"]):
        raise ValueError("Result fields are invalid")
    sequences = result[SOURCE_EVENTS_FIELD]
    if any(value < 1 for value in sequences) or len(sequences) != len(set(sequences)):
        raise ValueError("Result source sequences are invalid")
    for source in result.get("sources") or []:
        url_text = source.get("url") if isinstance(source, dict) else None
        if not isinstance(url_text, str):
            continue
        if url_text != url_text.strip() or any(ord(char) < 32 for char in url_text):
            raise ValueError("Source URL contains unsafe whitespace")
        url = urlsplit(url_text)
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
            raise ValueError("Source URL must be public HTTP(S) without credentials")
    return result


def record_value(result: dict[str, Any]) -> dict[str, Any]:
    """The app record for validated collection-result content."""
    return {key: value for key, value in result.items() if key != SOURCE_EVENTS_FIELD}
