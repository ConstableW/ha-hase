/* Small, additive HASE entry point in the native integration/Assist configuration. */
(() => {
  const observers = new Map();
  let scheduled = false;
  function schedule() {
    if (scheduled) return;
    scheduled = true;
    // Also work when HA renders a configuration page in a background tab.
    queueMicrotask(() => { scheduled = false; scan(); });
  }
  function enhance(page) {
    const root = page.shadowRoot;
    if (!root || page.domain !== "hase") {
      root?.querySelector("[data-hase-studio-link]")?.remove();
      return;
    }
    const actions = root.querySelector(".actions");
    if (!actions || root.querySelector("[data-hase-studio-link]")) return;
    const link = document.createElement("a");
    link.dataset.haseStudioLink = "";
    link.href = "/hase";
    link.setAttribute("aria-label", "HASE Spellbook Studio");
    link.style.cssText = "display:inline-flex;align-items:center;gap:6px;min-height:44px;padding:0 10px;border:1px solid #376075;border-radius:8px;background:#0c1722;color:#79dfee;text-decoration:none;font:inherit;font-size:13px;white-space:normal";
    const logo = document.createElement("img");
    logo.src = "/hase-studio-brand/icon.png";
    logo.alt = "";
    logo.width = 26;
    logo.height = 26;
    link.append(logo, document.createTextNode("Spellbook Studio"));
    actions.append(link);
  }
  function scanRoot(root) {
    if (!observers.has(root)) {
      const observer = new MutationObserver(schedule);
      observer.observe(root, {childList: true, subtree: true});
      observers.set(root, observer);
    }
    for (const node of root.querySelectorAll("*")) {
      if (node.localName === "ha-config-integration-page") enhance(node);
      if (node.localName === "ha-config-entry-row") node.shadowRoot?.querySelector("[data-hase-studio-link]")?.remove();
      if (node.shadowRoot) scanRoot(node.shadowRoot);
    }
  }
  function scan() {
    if (!/^\/config\/(integrations|voice-assistants)(\/|$)/.test(window.location.pathname)) {
      for (const observer of observers.values()) observer.disconnect();
      observers.clear();
      return;
    }
    for (const [root, observer] of observers) {
      if (root.host && !root.host.isConnected) { observer.disconnect(); observers.delete(root); }
    }
    scanRoot(document);
  }
  window.addEventListener("location-changed", schedule);
  customElements.whenDefined("ha-config-integration-page").then(schedule);
  schedule();
})();
