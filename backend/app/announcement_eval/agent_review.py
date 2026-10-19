"""Natural-conversation review. Measurements inform judgment; they do not replace it.

Historical assessment functions and saved grades remain unchanged. No inference,
speech, preference or acknowledgment side effects are permitted here.
"""
from copy import deepcopy
from hashlib import sha256
import json

RUBRIC = """Review the whole interaction as a natural personal assistant.
Judge request/preference memory, execution and truth, relevance, timing, yielding
and recovery, durable communication memory, and usefulness versus the reference.
Overlap alone is not failure. Distinguish simultaneous starts, assistant-first,
caller-first, backchannels, yielding, apology/recovery and persistent disruption.
Transcript receipt/generation time is not client playback time. Establish order
from synchronized audio and playback with uncertainty; do not invent audio access.
Ten seconds is a contextual target, not an absolute limit. Explain sensible delays
and unexplained/missed requests. Harmless spelling or taxonomy differences are not
product failures. Do not waive wrong facts, task identity, unplayed delivery claims
or missing evidence. Uncertain communication is neither delivered nor a mandate to
repeat. Cite source evidence and report actual effects, severity and uncertainty.
Judge overall usefulness, not flawless performance. Obtain independent review for
borderline judgments, proposed blocking failures and the adoption recommendation.
"""
IDENTITY = sha256(RUBRIC.encode()).hexdigest()
SUBJECTIVE_PREFIXES = ("substantive_overlap:", "outcome_interrupts_caller:",
    "notification_deadline_missed:", "irrelevant_task_outcome:",
    "duplicate_unsolicited_outcome:")


def observations(assessment):
    """Project an old measurement report without rewriting its original findings."""
    result = deepcopy(assessment)
    result["schema"] = "jarvis-natural-observations.v1"
    result["agent_rubric_sha256"] = IDENTITY
    result["conversational_observations"] = []
    for name, dimension in result["dimensions"].items():
        retained = []
        for finding in dimension["failures"]:
            if finding.startswith(SUBJECTIVE_PREFIXES):
                result["conversational_observations"].append({"dimension": name, "measurement": finding})
            else:
                retained.append(finding)
        dimension["failures"] = retained
        dimension.pop("status", None)
    # Even no automatic failures does not establish good conversational behavior.
    for key in ("selection_status", "interaction_status", "conclusion"):
        result.pop(key, None)
    result["review_status"] = "requires_agent_judgment"
    return result


def validate_review(review, *, adoption=False):
    if review.get("rubric_sha256") != IDENTITY:
        raise ValueError("Review rubric mismatch")
    if review.get("verdict") not in {"acceptable", "material_problem", "inconclusive"}:
        raise ValueError("Explicit review verdict required")
    if not review.get("reviewer") or not review.get("evidence") or not review.get("reason"):
        raise ValueError("Evidence and reasoning required")
    independent = review.get("independent_review")
    if adoption or review.get("borderline") or review["verdict"] == "material_problem":
        if not independent or not independent.get("reviewer") or independent.get("reviewer") == review.get("reviewer"):
            raise ValueError("Independent review required")
        if not independent.get("reason") or independent.get("verdict") != review["verdict"]:
            raise ValueError("Unresolved reviewer disagreement")
    return review["verdict"] == "acceptable"


def identity(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
