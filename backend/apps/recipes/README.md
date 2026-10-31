# Recipes

A built-in app: the owner's saved recipes. Jarvis adds them with the
`recipe_from_screen` workflow ("find me a recipe for this dish") and revises
them on request ("use grams instead"). The owner reads, searches and deletes them
here.

## What it does

- **List:** every recipe, newest first, with its servings and timing. A search
  box filters by title and ingredients as you type.
- **Recipe:** the title, any uncertainty (highlighted), servings and timing, the
  ingredients as a list, the instructions as numbered steps, and the sources as
  links that open outside Cockpit.
- **Opening a recipe:** a notification, a link or Jarvis can open the app on one
  recipe (`cockpit.ready` gives its `target`, and `cockpit.onTarget` reports later
  ones). Opening a recipe calls `cockpit.viewing`, so Jarvis knows which recipe
  "this recipe" is.
- **Delete:** removes a recipe after a confirmation. A deleted recipe that Jarvis
  made is gone; asking Jarvis to change it starts a new one.

## Package

- `cockpit-app.json`: one result collection, `recipe`, which workflows may
  publish into, and the `recipe_from_screen` workflow.
- `schemas/recipe.json`: the record schema. Workflow results and app writes are
  both validated against it.
- `ui/index.html`: the whole UI, with no build step or dependencies. Records are
  read with `cockpit.data.query` and `cockpit.data.get`.
- `tests/ui.json`: the UI tests the platform check runs before install.
- `workflows/recipe_from_screen/`: the workflow definition, its instructions and
  its fixture cases. Its `output_schema` is `collection:recipe`.

A change ships as a new `version` in `cockpit-app.json`. On start, Cockpit
installs this package only when its version is newer than every installed one,
so a version Jarvis built is never overwritten.
