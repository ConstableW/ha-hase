🇩🇪 [Deutsch](../de/assist.md) | 🇬🇧 English

# Assist and Conversation

A standalone HASE-Agent is a Home Assistant `ConversationEntity`. It matches the selected Spellbook, builds the declared plan and executes it through the shared security path. Configure it in an Assist pipeline like any other conversation agent.

Action and query area policies are separate. Old entries without explicit query keys mirror the action policy; newly configured entries require an explicit query scope or unrestricted setting. A configured but broken book fails closed.

All five implemented execution forms reach the same router: actions, single-operation queries, multi-source queries, Combined Spells, and text-only Spells. For a text-only Spell, `assist_redirect: false` returns the rendered `output`; `assist_redirect: true` sends that rendered text to Home Assistant's configured conversation agent for interpretation and possible execution. It is not inferred from wording.

Matching prefers more specific triggers and supports aliases. If two near-equal candidates would produce opposite outcomes, HASE asks for clarification rather than guessing. Conversation and clarification state is in memory and does not survive a restart.

On firmware 1.6.0+, Codie's own model hands your sentences to a HASE Spellbooker (with Assist as fallback) through the `home_command` tool. See [CODE27](providers/code27.md).
