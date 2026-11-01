# Meal planner

The reference package for another app's data: it plans the week's dinners
from the Recipes app.

- **Grant:** `"grants": {"read": ["recipes.recipe"]}` lets the UI and functions
  read Recipes' records as `recipes.recipe`. It cannot change them.
- **Own data:** `meal` records, one per weekday (the key is the day), naming a
  recipe by its key and keeping a copy of its title.
- **Event function** `follow_recipe` runs when a recipe changes in Recipes. It
  copies a new title into the meals that use it, or marks them `missing` when
  the recipe is deleted.
- Because this app reads `recipes.recipe`, Recipes may now only add optional
  fields to it.
