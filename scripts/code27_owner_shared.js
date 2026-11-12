// Shared, registry-based owner resolution for the CODE27 dashboard cards.
// A device is owned by the integration that created its entities. HASE's
// external binding never changes that owner or creates another device.
const OWNERS = new Set(["hase", "code27"]);

export function code27Devices(hass) {
  const entities = hass?.entities || {};
  const devices = hass?.devices || {};
  const found = [];
  for (const [entityId, entity] of Object.entries(entities)) {
    if (entity.translation_key !== "active_character" || !entity.device_id) continue;
    const device = devices[entity.device_id];
    if (!device) continue;
    const owners = (device.identifiers || []).map(([domain]) => domain).filter((domain) => OWNERS.has(domain));
    if (owners.length !== 1) continue;
    const entryIds = device.config_entries || [];
    const entryId = entity.config_entry_id || (entryIds.length === 1 ? entryIds[0] : null);
    if (!entryId || !entryIds.includes(entryId)) continue;
    const prefix = entityId.replace(/^sensor\./, "").replace(/(_[^_]+){2}$/, "");
    found.push({
      prefix, device_id: entity.device_id, config_entry_id: entryId,
      domain: owners[0], name: device.name_by_user || device.name || prefix,
    });
  }
  return found;
}

export function code27Owner(hass, selector = {}) {
  const matches = code27Devices(hass).filter((device) =>
    (selector.device_id && device.device_id === selector.device_id) ||
    (!selector.device_id && selector.config_entry_id && device.config_entry_id === selector.config_entry_id) ||
    (!selector.device_id && !selector.config_entry_id && selector.device_prefix && device.prefix === selector.device_prefix)
  );
  return matches.length === 1 ? matches[0] : null;
}

// Music runtimes expose their own targets. Probe both namespaces and route only
// to the runtime that advertises the selected entry; no HASE proxy is created.
export async function code27MusicTargets(hass, transports) {
  const results = await Promise.all(Object.entries(transports).map(async ([provider, transport]) => {
    try {
      const snapshot = await hass.callWS({ type: transport.command, operation: "snapshot", data: {} });
      return (snapshot.targets || []).map((target) => ({ ...target, provider }));
    } catch (_) {
      return [];
    }
  }));
  return results.flat();
}

export async function code27MusicOwner(hass, target, transports) {
  if (target) {
    const owner = code27Devices(hass).find((device) => device.config_entry_id === target);
    if (owner) {
      const provider = owner.domain === "hase" ? "code27" : "code27_standalone";
      if (transports[provider]) return { id: target, provider };
    }
  }
  const targets = await code27MusicTargets(hass, transports);
  const matches = target ? targets.filter((item) => item.id === target) : targets;
  if (matches.length !== 1) {
    throw new Error(matches.length ? "Select a CODE27 target" : "CODE27 music target unavailable");
  }
  return matches[0];
}
