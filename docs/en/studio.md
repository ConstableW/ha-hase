🇩🇪 [Deutsch](../de/studio.md) | 🇬🇧 English

# Spellbook Studio

Spellbook Studio is HASE's authoring and management UI at `/hase`. It is not an authorization boundary for MCP or Assist execution.

Studio edits one stable book at a time, validates through the same schema used at runtime, supports multilingual fields, import/export, dry runs, optional confirmed live execution, and local per-book Expert enablement. Editing a bundled book creates a complete user copy in `<config>/hase_spellbooks/`; the bundled original remains unchanged. Reset removes the user override and reveals the bundled book again.

The UI follows the Home Assistant user's language (German and English are included). Book language is independent. Runtime fallback is requested language → English → declared default → the only available language → technical fallback.

There is now only **one** Wizard: a spell has 0-12 steps (0 = plain text response, 1 = a single action/query, 2-12 = several actions and queries combined). The old distinction between a "single-action wizard" and a "Combined wizard" is purely historical - both are the same editor today, differing only in step count. The Wizard covers the common, catalogued user actions; any individual step can additionally be switched to "Advanced (Expert)" to enter a free operation, a free target (including Jinja/template) and free arguments - without forcing the rest of the spell into Expert.

**Which editor opens:** Whether a spell opens in the Wizard or in Expert is not a stored property of the spell - it is derived from the spell's actual content every time it is opened. If the spell contains at least one Expert-only structure (e.g. a Jinja target, an uncatalogued operation/arguments, or a separate `expert:` block), it opens in Expert; otherwise in the Wizard. "Switch to Expert" (always available) and "Switch to Wizard" (only available when the whole spell is losslessly representable in the Wizard - otherwise a concrete, step-by-step error is shown) let you convert deliberately.

**Every step validates itself:** Clicking "Next" validates the step you are leaving - as far as it is possible to check at that point - and blocks navigation with concrete, field-level errors if something is wrong; "Back" is always free. This applies identically in Wizard and Expert mode (Expert means freely editable, not unvalidated) and to a single action/query's own "Apply" button. Later steps see more: capture ↔ trigger consistency can only be checked once the Trigger step exists, and response variables only once the Actions/Queries and Trigger steps exist - so a problem is always caught at the earliest step that can actually detect it, never held back until the end.

**Final step – Check & test:** The final wizard step still runs a complete, whole-document check (including cross-spell safety) as a last safety net - by the time you reach it, anything it finds should already be familiar from an earlier step. "Check spell" runs automatically when you arrive there (via "Next" or the breadcrumb); the result is shown immediately in the validation summary ("X errors · Y warnings").

**Exposure warning on save:** If the spell targets an entity that is not exposed to Assist (conversation domain), saving shows a confirmation dialog listing the unexposed entities. You can still save and expose the entity later, or cancel and expose it first.

The MCP capability check reports two separate facts: structural projectability and known Bridge requirements. Reasons such as `duplicate_spell_id`, `conflicting_capture_definition:*`, `unknown_operation:*` and `unresolvable_text` are authoring/projection problems, not permission denials. A real call is authorized again at execution time.

Studio access defaults to Home Assistant administrators and can be configured for all users or selected users/groups. Deleted users/groups never become implicit access grants.

## Wizard: Todo spells

The wizard entity picker lists **all** todo entities, including those that are not yet exposed to Assist. An unexposed entity is marked with an inline warning (⚠ Not exposed to Assist · Expose now). Clicking "Expose now" opens the Home Assistant Settings page where you can grant access. Filtering unexposed todo entities out of the picker would silently hide the most common setup mistake, so all lists are always shown.

**`{item}` capture:** For operations that act on a single item (`todo.add_item`, `todo.update_item`, `todo.remove_item`), the trigger phrase should include `{item}` as a placeholder. At runtime, whatever the user says in place of `{item}` is passed verbatim as the `item` argument. For `todo.update_item` and `todo.remove_item`, the item name must match exactly — capitalisation and punctuation included. Example trigger: `complete task {item}`.

**Exposure check in the final step:** When auto-validation runs on reaching the final Wizard step, the backend checks whether the targeted entity is exposed. If it is not, a warning appears in the validation summary and is highlighted on the target field: "Target `todo.inbox` is not exposed to Assist and cannot be resolved." The wizard still allows you to save; the save confirmation dialog then asks whether to proceed.

For full operation details, `status` filtering and multi-step patterns, see [Actions → Todo lists](spellbooks/actions.md#todo-lists) and [Combined Spells → Todo lists](spellbooks/combined-spells.md#todo-lists).
