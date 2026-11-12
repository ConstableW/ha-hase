🇩🇪 [Deutsch](../../de/mcp/area-policy.md) | 🇬🇧 English

# MCP area policy and schema projection

Projection uses the same `ScopedAreaPolicy` the executor later enforces, but only to shape area arguments. It does not grant execution.

- **Case A — room-bound and restricted to that room:** the Bridge area is the only permitted area. The area capture is omitted from the JSON schema and injected internally.
- **Case B — area-less/master Bridge:** there is no default. The area argument is required.
- **Case C — room-bound with broader policy:** the area argument is optional. Omission uses the Bridge area; an explicit value may select another permitted area.

Action targets use Action AreaPolicy; query targets use Query AreaPolicy. A Combined Spell can touch both. `area_id` and display `area_name` remain distinct: policy membership uses IDs, while an `area_name` capture receives the current display name.

Entity captures have no automatic room entity default. Any explicit or injected target is resolved against current registries and checked again at execution. A schema that permits a value does not promise that the value exists, is exposed, or is authorized.
