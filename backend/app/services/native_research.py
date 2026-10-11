"""Validate provider-backed provenance without fabricating local fetch proof."""
from sqlalchemy import select
from app.db.tables import TaskEvent


def is_research_event(event) -> bool:
    item = (event.payload_json or {}).get("item") or {}
    return event.event_type in {"agent.native.item", "agent.native.subagent.item"} and item.get("type") == "webSearch"


async def validate_research_evidence(session, task_id: str, pinned: dict, content: dict) -> None:
    sequences = content["source_event_sequences"]
    events = list((await session.execute(select(TaskEvent).where(
        TaskEvent.task_id == task_id, TaskEvent.sequence.in_(sequences)))).scalars().all()) if sequences else []
    research_sequences = {event.sequence for event in events if is_research_event(event)}
    if "research.native@1" in pinned.get("capabilities", []):
        if not research_sequences:
            raise ValueError("Research result requires a cited completed native research event")
        if not content.get("sources"):
            raise ValueError("Research result requires usable source URLs")
    for source in content.get("sources", []):
        sequence = source.get("source_event_sequence")
        if sequence is not None and sequence not in research_sequences:
            raise ValueError("Source must reference completed native research from this task")
        if "research.native@1" in pinned.get("capabilities", []) and sequence is None:
            raise ValueError("Each research source requires a native research event reference")
