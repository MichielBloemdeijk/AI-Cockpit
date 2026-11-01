from clips import describe, record_key


def handle(input, cockpit):
    """A clip file changed: keep its record in step (event trigger, safe to run twice)."""
    name = input["name"]
    if input["change"] == "delete":
        cockpit.data.delete("clip", record_key(name))
        return {"removed": name}
    text = cockpit.files.read_text(name)
    if text is None:  # deleted again before this ran
        return {"skipped": name}
    record = describe(name, text)
    cockpit.data.put("clip", record_key(name), record)
    return record
