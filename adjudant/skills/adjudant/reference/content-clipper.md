> **`adjudant` vault?** Clipped notes land as `type: source` (or `type: note`). The shape is the kind's template in `templates/`, parsed into `FIELD_SCHEMA`; see `adjudant:vault-standards (reference/vault-standards.md)` §1. A `source` requires `type:`, `created:`, `updated:`, `verified:` and `verified_by:` (`tested | read | docs`), and may add `source:`. A `note` requires `type:`, `created:` and `updated:`, and may add `session:`. Write no other key: `tags:` and `title:` are fields no template declares and `clean` strips them. Project membership is the folder path, not a frontmatter field: save the clipped note into the project's folder rather than writing `project:`.

# Obsidian Web Clipper Template Creator

This skill helps you create importable JSON templates for the Obsidian Web Clipper.

## When to Use
- You need to create or refine an importable Obsidian Web Clipper template.
- You want to map a site's real DOM, schema data, and selectors into a valid clipping template.
- You need selector verification and template logic guidance before handing the JSON to the user.

## Workflow

1. **Identify User Intent:** specific site (YouTube), specific type (Recipe), or general clipping?
2. **Check Existing Bases:** The user likely has a "Base" schema defined in `Bases/`.
    - **Action:** Read `Bases/*.base` to find a matching category (e.g., `Recipes.base`).
    - **Action:** Use the properties defined in the Base to structure the Clipper template properties.
    - See [content-bases.md](content-bases.md) for the Bases side of the mapping.
3. **Fetch & Analyze Reference URL:** Validate variables against a real page.
    - **Action:** Ask the user for a sample URL of the content they want to clip (if not provided).
    - **Action (REQUIRED):** Use **WebFetch** to retrieve page content; if WebFetch is not available, use a browser DOM snapshot.
    - **Action:** Analyze the HTML for Schema.org JSON, Meta tags, and CSS selectors.
    - **Action (REQUIRED):** Verify each selector against the fetched content. Do not guess selectors.
    - Validate against the live page: fetch it, inspect the DOM/schema.org data, and confirm each selector resolves.
4. **Draft the JSON:** Create a valid JSON object following the schema.
    - See the official [Templates docs](https://help.obsidian.md/web-clipper/templates) for the JSON structure.
5. **Consider template logic:** Use conditionals for optional blocks (e.g. show nutrition only if present), loops for list data, variable assignment to avoid repeating expressions, and fallbacks for missing variables. Use logic only when it improves the template; keep simple templates simple. See the official [Logic docs](https://help.obsidian.md/web-clipper/logic).
6. **Verify Variables:** Ensure the chosen variables (Preset, Schema, Selector) exist in your analysis.
    - **Action (REQUIRED):** If a selector cannot be verified from the fetched content, state that explicitly and ask for another URL.
    - See the official [Variables docs](https://help.obsidian.md/web-clipper/variables).

## Selector Verification Rules

- **Always verify selectors** against live page content before responding.
- **Never guess selectors.** If the DOM cannot be accessed or the element is missing, ask for another URL or a screenshot.
- **Prefer stable selectors** (data attributes, semantic roles, unique IDs) over fragile class chains.
- **Document the target element** in your reasoning (e.g., "About sidebar paragraph") to reduce mismatch.

## Output Format

**ALWAYS** output the final result as a JSON code block that the user can copy and import.

The Clipper template editor validates template syntax.
If you use template logic (conditionals, loops, variable assignment), ensure it follows the official [Logic](https://help.obsidian.md/web-clipper/logic) docs so the template passes validation.

```json
{
  "schemaVersion": "0.1.0",
  "name": "My Template",
  ...
}
```

## Resources

- [Variables (official docs)](https://help.obsidian.md/web-clipper/variables) - Available data variables.
- [Filters (official docs)](https://help.obsidian.md/web-clipper/filters) - Formatting filters.
- [Templates (official docs)](https://help.obsidian.md/web-clipper/templates) - Template JSON structure.
- [Logic (official docs)](https://help.obsidian.md/web-clipper/logic) - Template logic.
- [content-bases.md](content-bases.md) - The Bases side of the Bases→Template mapping.

### Official Documentation

- [Variables](https://help.obsidian.md/web-clipper/variables)
- [Filters](https://help.obsidian.md/web-clipper/filters)
- [Logic](https://help.obsidian.md/web-clipper/logic)
- [Templates](https://help.obsidian.md/web-clipper/templates)

## Examples

## Limitations
- Use this skill only when the task clearly matches the scope described above.
- Do not treat the output as a substitute for environment-specific validation, testing, or expert review.
- Stop and ask for clarification if required inputs, permissions, safety boundaries, or success criteria are missing.
