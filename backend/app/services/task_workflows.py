"""Source-controlled, explicitly enabled workflow definitions for task runs."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any

import yaml

from app.config import settings
from app.services.task_capabilities import INPUT_CONTEXTS, validate_capabilities
from app.services.task_result_types import RESULT_TYPES, result_type


_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_CAPABILITIES = {"task.context.read@1", "task.result.publish@1"}
_SCREEN_CAPABILITIES = _CAPABILITIES | {"task.context.images@2"}
_OUTPUT_SCHEMAS = set(RESULT_TYPES)


class _UniqueSafeLoader(yaml.SafeLoader):
    pass


def _unique_mapping(loader: _UniqueSafeLoader, node: yaml.MappingNode) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if not isinstance(key, str) or key in result:
            raise ValueError(f"Duplicate or non-string workflow YAML key: {key!r}")
        result[key] = loader.construct_object(value_node)
    return result


_UniqueSafeLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


def _safe_definition_file(folder: Path, name: Any) -> Path:
    if not isinstance(name, str) or not name or Path(name).is_absolute() or ".." in Path(name).parts:
        raise ValueError("Workflow file path must stay inside its version directory")
    if not folder.is_dir() or folder.is_symlink() or folder.parent.is_symlink():
        raise ValueError("Workflow version directory is unavailable or linked")
    target = folder / name
    try:
        resolved = target.resolve(strict=True)
    except OSError as exc:
        raise ValueError("Workflow definition file is unavailable") from exc
    if target.is_symlink() or not resolved.is_relative_to(folder.resolve(strict=True)):
        raise ValueError("Workflow file path escapes its version directory")
    if not target.is_file():
        raise ValueError("Workflow definition file is not a regular file")
    return target


@dataclass(frozen=True, slots=True)
class WorkflowDefinition:
    id: str
    version: int
    digest: str
    instructions: str
    output_schema: str
    capabilities: tuple[str, ...]
    input_schema: str = "text-request.v1"
    required_initial_context: tuple[str, ...] = ()
    purpose: str = ""
    routing_examples: tuple[str, ...] = ()
    manifest_schema: str = "workflow-definition.v2"
    tools: dict | None = None

    def pinned(self) -> dict[str, Any]:
        return {"schema": "workflow-pinned.v2", "id": self.id,
            "version": self.version, "digest": self.digest, "instructions": self.instructions,
            "output_schema": self.output_schema, "capabilities": list(self.capabilities),
            "input_schema": self.input_schema, "required_initial_context": list(self.required_initial_context),
            "purpose": self.purpose, "routing_examples": list(self.routing_examples),
            "tools": self.tools or {"add": [], "remove": []}}


def load_workflow(definition: str) -> WorkflowDefinition:
    match = re.fullmatch(r"([a-z][a-z0-9_]{0,63})@([1-9][0-9]*)", definition)
    if match is None:
        raise ValueError("Workflow definition must be id@positive_version")
    workflow_id, version_text = match.groups()
    folder = settings.backend_root / "workflows" / workflow_id / version_text
    manifest_path = _safe_definition_file(folder, "workflow.yaml")
    manifest_bytes = manifest_path.read_bytes()
    if len(manifest_bytes) > 32_000:
        raise ValueError("Workflow manifest is too large")
    manifest = yaml.load(manifest_bytes.decode("utf-8"), Loader=_UniqueSafeLoader)
    required_fields = {
        "schema", "id", "version", "instructions", "input_schema", "output_schema",
        "required_initial_context", "capabilities", "defaults", "fixtures", "purpose", "routing_examples",
    }
    if not isinstance(manifest, dict) or set(manifest) - {"tools"} != required_fields:
        raise ValueError("Workflow manifest has missing or unsupported fields")
    if (manifest["schema"] != "workflow-definition.v2" or manifest["id"] != workflow_id
            or type(manifest["version"]) is not int or manifest["version"] != int(version_text)):
        raise ValueError("Workflow manifest identity does not match its directory")
    profile = (manifest["input_schema"], manifest["output_schema"])
    if profile[0] not in INPUT_CONTEXTS or profile[1] not in RESULT_TYPES:
        raise ValueError("Workflow input or output schema is not registered")
    required_context = list(INPUT_CONTEXTS[profile[0]])
    if manifest["required_initial_context"] != required_context or manifest["defaults"] != {"execution_profile": "fast"}:
        raise ValueError("Workflow context or execution profile is not supported")
    capabilities = manifest["capabilities"]
    validate_capabilities(capabilities, tuple(required_context))
    from app.services.worker_tool_policy import normalize_overrides
    tools = normalize_overrides(manifest.get("tools"))
    if result_type(profile[1]).requires_sources and "research.native@1" not in capabilities:
        raise ValueError("Source-backed output requires the research capability")
    purpose = manifest.get("purpose", workflow_id.replace("_", " "))
    examples = manifest.get("routing_examples", [])
    if (not isinstance(purpose, str) or not purpose.strip() or len(purpose) > 300
            or not isinstance(examples, list) or len(examples) > 5
            or any(not isinstance(item, str) or not item.strip() or len(item) > 300 for item in examples)):
        raise ValueError("Workflow catalog metadata is invalid")
    instructions_bytes = _safe_definition_file(folder, manifest["instructions"]).read_bytes()
    fixture_bytes = _safe_definition_file(folder, manifest["fixtures"]).read_bytes()
    if len(instructions_bytes) > 32_000 or len(fixture_bytes) > 64_000:
        raise ValueError("Workflow instructions or fixture index is too large")
    instructions = instructions_bytes.decode("utf-8").strip()
    fixtures = yaml.load(fixture_bytes.decode("utf-8"), Loader=_UniqueSafeLoader)
    if not instructions or not isinstance(fixtures, dict) or not isinstance(fixtures.get("cases"), list):
        raise ValueError("Workflow instructions or fixture index is invalid")
    identities = set()
    if set(fixtures) != {"cases"} or not 1 <= len(fixtures["cases"]) <= 32:
        raise ValueError("Workflow fixture index is invalid")
    for case in fixtures["cases"]:
        if (not isinstance(case, dict) or set(case) != {"id", "request", "expected"}
                or not isinstance(case["id"], str) or not _IDENTIFIER.fullmatch(case["id"])
                or case["id"] in identities
                or any(not isinstance(case[field], str) or not case[field].strip() or len(case[field]) > 2000
                       for field in ("request", "expected"))):
            raise ValueError("Workflow fixture case is invalid or duplicated")
        identities.add(case["id"])
    digest = sha256(json.dumps({
        "manifest": manifest_bytes.decode("utf-8"),
        "instructions": instructions_bytes.decode("utf-8"),
        "fixtures": fixture_bytes.decode("utf-8"),
    }, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    return WorkflowDefinition(workflow_id, int(version_text), digest, instructions,
                              manifest["output_schema"], tuple(capabilities), profile[0],
                              tuple(required_context), purpose, tuple(examples), manifest["schema"], tools)


def resolve_workflow_config(config: dict[str, Any] | None, *, inherited: dict | None = None) -> dict[str, Any]:
    request = dict(config or {})
    if set(request) - {"definition", "model", "reasoning_effort", "override_reason", "tools"}:
        raise ValueError("Workflow execution config contains unsupported fields")
    definition = request.get("definition")
    if not isinstance(definition, str):
        raise ValueError("Workflow definition is required")
    loaded = load_workflow(definition)
    allowed = {item.strip() for item in settings.task_workflow_allowlist.split(",") if item.strip()}
    if f"{definition}:{loaded.digest}" not in allowed:
        raise ValueError("Workflow version and digest are not enabled")
    if "research.native@1" in loaded.capabilities and not settings.task_research_enabled:
        raise ValueError("Workflow research capability is not enabled")
    model = request.get("model") or settings.codex_task_model
    effort = request.get("reasoning_effort") or settings.codex_task_reasoning_effort
    if not isinstance(model, str) or not model.strip() or effort not in {"low", "medium", "high"}:
        raise ValueError("Workflow model or reasoning effort is invalid")
    from app.services.task_execution_preferences import normalize_agent_preferences
    preferences = normalize_agent_preferences({key: value for key, value in request.items() if key != "definition"}, inherited={key: value for key, value in (inherited or {}).items() if key in {"model", "reasoning_effort", "override_reason", "tools"}})
    return {
        "definition": definition, "model": model.strip(), "reasoning_effort": effort,
        "workflow": loaded.pinned(), **preferences,
    }


def validate_pinned_workflow(config: dict[str, Any]) -> dict[str, Any]:
    pinned = config.get("workflow")
    if not isinstance(pinned, dict) or pinned.get("schema") != "workflow-pinned.v2":
        raise ValueError("Workflow run has no pinned definition")
    contexts = tuple(INPUT_CONTEXTS.get(pinned.get("input_schema"), ()))
    validate_capabilities(pinned.get("capabilities"), contexts)
    if (not isinstance(pinned.get("id"), str) or not _IDENTIFIER.fullmatch(pinned["id"])
            or type(pinned.get("version")) is not int or pinned["version"] < 1
            or not isinstance(pinned.get("digest"), str) or not re.fullmatch(r"[a-f0-9]{64}", pinned["digest"])
            or not isinstance(pinned.get("instructions"), str) or not pinned["instructions"].strip()
            or pinned.get("output_schema") not in _OUTPUT_SCHEMAS):
        raise ValueError("Pinned workflow definition is invalid")
    if (pinned.get("input_schema") not in INPUT_CONTEXTS
                       or pinned.get("required_initial_context") != list(contexts)):
        raise ValueError("Pinned workflow context is invalid")
    from app.services.worker_tool_policy import normalize_overrides
    normalize_overrides(pinned.get("tools"))
    if result_type(pinned["output_schema"]).requires_sources and "research.native@1" not in pinned["capabilities"]:
        raise ValueError("Source-backed output requires the research capability")
    return pinned


def validate_workflow_result(schema: str, content: Any) -> dict[str, Any]:
    return result_type(schema).validate(content)


def enabled_workflows() -> list[WorkflowDefinition]:
    definitions: list[WorkflowDefinition] = []
    for entry in settings.task_workflow_allowlist.split(","):
        identity, separator, digest = entry.strip().partition(":")
        if not separator:
            continue
        try:
            definition = load_workflow(identity)
        except (ValueError, OSError, yaml.YAMLError):
            continue
        if definition.digest == digest and ("research.native@1" not in definition.capabilities or settings.task_research_enabled):
            definitions.append(definition)
    return definitions
