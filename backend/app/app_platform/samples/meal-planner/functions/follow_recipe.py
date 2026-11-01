def handle(input, cockpit):
    """A recipe changed in the Recipes app: update the title of every meal planned with it, or mark it missing."""
    updated = 0
    for meal in cockpit.data.query("meal", where={"recipe": input["key"]}, limit=50):
        value = dict(meal["value"])
        if input["change"] == "delete":
            value["missing"] = True
        else:
            value["title"] = input["value"]["title"]
            value.pop("missing", None)
        if value != meal["value"]:
            cockpit.data.put("meal", meal["key"], value)
            updated += 1
    return {"updated": updated}
