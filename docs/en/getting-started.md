🇩🇪 [Deutsch](../de/getting-started.md) | 🇬🇧 English

# Getting started

This path needs only Home Assistant and HASE. CODE27 hardware and an MCP client are optional.

## 1. Install HASE

Copy `custom_components/hase` into `<config>/custom_components/hase`, then fully restart Home Assistant. In **Settings → Devices & services → Add integration**, search for **HASE**.

## 2. Create a standalone HASE-Agent

Choose **Create Spellbook voice assistant**. Select the bundled `demo` Spellbook, choose at least one allowed area for actions and queries (or deliberately enable the corresponding unrestricted option), and finish setup.

HASE creates a `conversation.*` entity. It does not create a CODE27 device.

## 3. Add it to Assist

Open **Settings → Voice assistants**, create or edit a pipeline, and select the new HASE conversation agent. Use the language supported by the selected book.

## 4. Run one Spell

The bundled demo book includes phrases such as:

```text
turn on the light in living room
```

Use the exact Home Assistant area name. The action succeeds only if the target entity is exposed to Assist and the area is allowed by the agent's action policy.

## 5. Create your own Spell

Open **Spellbook Studio** at `/hase`, create or copy a book, and select **Turn on light** (`light.turn_on`) from the action catalog. Saving a bundled book creates a user override; it does not modify the bundled original. Assign the new book in the HASE-Agent's options and retry the phrase.

Next: [Assist](assist.md), [Spellbook schema](spellbooks/schema.md), [Studio](studio.md), [MCP](mcp/overview.md), or [CODE27](providers/code27.md).
