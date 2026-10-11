"""Trusted result registrations shared by admission, MCP and publication."""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, ValidationError


class _Content(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    title: StrictStr = Field(min_length=1, max_length=200)
    source_event_sequences: list[StrictInt] = Field(max_length=64)


class TextReport(_Content):
    body: StrictStr = Field(min_length=1, max_length=32_000)


class DocumentSource(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    url: StrictStr = Field(min_length=1, max_length=2000)
    label: StrictStr = Field(min_length=1, max_length=200)
    source_event_sequence: StrictInt | None = Field(default=None, ge=1)


class DocumentAttachment(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    artifact_id: StrictStr = Field(min_length=1, max_length=64)
    label: StrictStr = Field(min_length=1, max_length=200)


class Document(TextReport):
    summary: StrictStr | None = Field(default=None, max_length=500)
    attachments: list[DocumentAttachment] = Field(default_factory=list, max_length=20)
    sources: list[DocumentSource] = Field(default_factory=list, max_length=20)


class RecipeSource(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source_event_sequence: StrictInt = Field(ge=1)
    url: StrictStr = Field(min_length=1, max_length=2000)
    title: StrictStr = Field(min_length=1, max_length=200)


class Recipe(_Content):
    ingredients: list[StrictStr] = Field(min_length=1, max_length=100)
    instructions: list[StrictStr] = Field(min_length=1, max_length=100)
    sources: list[RecipeSource] = Field(min_length=1, max_length=10)
    uncertainty: StrictStr | None = Field(default=None, max_length=1000)
    servings: StrictStr | None = Field(default=None, max_length=200)
    timing: StrictStr | None = Field(default=None, max_length=500)


@dataclass(frozen=True)
class ResultType:
    model: type[_Content]
    app_slug: str | None
    label: str
    requires_sources: bool = False

    @property
    def actions(self) -> tuple[str, ...]:
        return ("set_saved",) if self.app_slug is not None else ()

    @property
    def namespaces(self) -> tuple[str, ...]:
        return ("saved",) if self.app_slug is not None else ()

    def validate(self, content: Any) -> dict[str, Any]:
        try:
            parsed = self.model.model_validate(content)
        except ValidationError as exc:
            raise ValueError(f"Workflow result does not match {self.model.__name__}: {exc}") from exc
        # Omitted additive fields stay omitted: old operation fingerprints and
        # pinned workflow replays remain valid after a schema extension.
        result = parsed.model_dump(exclude_none=True, exclude_unset=True)
        if re.search(r"(?:turn\d+(?:search|view|fetch)\d+|\ue200cite|\ue202)", str(result)):
            raise ValueError("Replace unresolved provider citation markers with usable source URL links")
        for field in ("title", "body", "summary", "uncertainty", "servings", "timing"):
            if field in result:
                result[field] = result[field].strip()
        if not result["title"] or ("body" in result and not result["body"]):
            raise ValueError("Result fields are invalid")
        sequences = result["source_event_sequences"]
        if any(value < 1 for value in sequences) or len(sequences) != len(set(sequences)):
            raise ValueError("Result source sequences are invalid")
        for field in ("ingredients", "instructions"):
            if field in result:
                if any(not value.strip() or len(value) > 2000 for value in result[field]):
                    raise ValueError("Recipe items must be nonempty bounded text")
                result[field] = [value.strip() for value in result[field]]
        for source in result.get("sources", []):
            if source["url"] != source["url"].strip() or any(ord(char) < 32 for char in source["url"]):
                raise ValueError("Source URL contains unsafe whitespace")
            url = urlsplit(source["url"])
            if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
                raise ValueError("Source URL must be public HTTP(S) without credentials")
        return result


RESULT_TYPES = {
    "text-report.v1": ResultType(TextReport, None, "Report"),
    "document.v1": ResultType(Document, "documents", "Document"),
    "recipe.v1": ResultType(Recipe, "recipes", "Recipe", requires_sources=True),
}


def result_type(schema: str) -> ResultType:
    try:
        return RESULT_TYPES[schema]
    except KeyError:
        raise ValueError("Workflow result schema is not registered") from None
