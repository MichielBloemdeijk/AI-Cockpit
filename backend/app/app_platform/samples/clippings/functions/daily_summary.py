def handle(input, cockpit):
    """Every morning: count the clips and their words into the summary record "latest"."""
    clips = cockpit.data.query("clip", limit=200)
    summary = {"clips": len(clips), "words": sum(clip["value"]["words"] for clip in clips),
               "counted_for": str((input or {}).get("scheduled_for") or "")[:40]}
    cockpit.data.put("summary", "latest", summary)
    return summary
