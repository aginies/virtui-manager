/* VirtUI Manager web dashboard (P1: read-only monitoring).
 *
 * - Reads the API token from the URL hash (#token=...) or localStorage.
 * - Fetches /api/servers and /api/vms, renders one card per VM.
 * - Subscribes to /ws/stats for live sparkline updates (same scaling
 *   rules as the TUI, see sparkline.js).
 * - Refreshes the inventory periodically to pick up new/removed VMs.
 */
"use strict";

(() => {
  const TOKEN_KEY = "virtui_token";
  const SERVERS_KEY = "virtui_servers";
  const INVENTORY_REFRESH_MS = 30000;
  const WS_RETRY_MS = 4000;

  const STATUS_COLORS = {
    Running: "running",
    Blocked: "blocked",
    Paused: "paused",
    "Guest Suspended": "paused",
    Stopped: "stopped",
    Unknown: "unknown",
  };

  // Quick action bar, mirroring the TUI (icons from QBarIcons.EMOJI and
  // visibility rules from VMCard._update_fast_buttons).
  const ACTIONS = {
    start:     { icon: "\u25b6", label: "Start VM",            states: ["Stopped"], confirm: false },
    shutdown:  { icon: "\u23fb", label: "Shutdown",            states: ["Running", "Blocked"], confirm: true },
    force_off: { icon: "\u26a1", label: "Force Off",           states: ["Running", "Paused", "Guest Suspended", "Blocked"], confirm: true, danger: true },
    pause:     { icon: "\u23f8", label: "Pause",               states: ["Running"], confirm: false },
    resume:    { icon: "\u23ef", label: "Resume",              states: ["Paused", "Guest Suspended"], confirm: false },
    hibernate: { icon: "\ud83d\udca4", label: "Hibernate (save state)", states: ["Running", "Blocked"], confirm: true },
    // ui: true -> opens the detail modal on a tab instead of calling the API
    snapshots: { icon: "\ud83d\udcf7", label: "Snapshots", ui: true,
                 states: ["Running", "Blocked", "Paused", "Guest Suspended", "Stopped", "Unknown"] },
  };

  // Sparkline palettes [low, high] as RGB triplets.
  const PALETTES = {
    cpu: [[76, 175, 80], [244, 67, 54]],
    mem: [[66, 165, 245], [171, 71, 188]],
    net: [[38, 198, 218], [255, 238, 88]],
    disk: [[141, 110, 99], [255, 112, 67]],
  };

  let token = new URLSearchParams(location.hash.slice(1)).get("token")
    || localStorage.getItem(TOKEN_KEY) || "";
  if (new URLSearchParams(location.hash.slice(1)).get("token")) {
    localStorage.setItem(TOKEN_KEY, token);
    history.replaceState(null, "", location.pathname);
  }

  const els = {
    servers: document.getElementById("servers"),
    cards: document.getElementById("cards"),
    empty: document.getElementById("empty"),
    filter: document.getElementById("filter"),
    wsStatus: document.getElementById("ws-status"),
    tokenBtn: document.getElementById("token-btn"),
    tokenModal: document.getElementById("token-modal"),
    tokenInput: document.getElementById("token-input"),
    tokenSave: document.getElementById("token-save"),
    tokenCancel: document.getElementById("token-cancel"),
    confirmModal: document.getElementById("confirm-modal"),
    confirmTitle: document.getElementById("confirm-title"),
    confirmText: document.getElementById("confirm-text"),
    confirmOk: document.getElementById("confirm-ok"),
    confirmCancel: document.getElementById("confirm-cancel"),
    detailModal: document.getElementById("detail-modal"),
    detailTitle: document.getElementById("detail-title"),
    detailServer: document.getElementById("detail-server"),
    detailDot: document.getElementById("detail-dot"),
    detailTabs: document.getElementById("detail-tabs"),
    detailContent: document.getElementById("detail-content"),
    detailClose: document.getElementById("detail-close"),
    detailRefresh: document.getElementById("detail-refresh"),
    actMenu: document.getElementById("act-menu"),
    renameModal: document.getElementById("rename-modal"),
    renameInput: document.getElementById("rename-input"),
    renameOk: document.getElementById("rename-ok"),
    renameCancel: document.getElementById("rename-cancel"),
    deleteModal: document.getElementById("delete-modal"),
    deleteText: document.getElementById("delete-text"),
    deleteStorage: document.getElementById("delete-storage"),
    deleteNvram: document.getElementById("delete-nvram"),
    deleteOk: document.getElementById("delete-ok"),
    deleteCancel: document.getElementById("delete-cancel"),
    toast: document.getElementById("toast"),
    template: document.getElementById("card-template"),
  };

  // id -> { vm, el, sparks: {cpu, mem, net, disk}, visible }
  const cards = new Map();
  let filterText = "";
  let ws = null;
  let refreshTimer = null;

  // Selected servers (URIs). Nothing is displayed until the user picks
  // at least one, like the TUI server selection. Persisted per browser.
  let selectedServers = new Set();
  try {
    const saved = JSON.parse(localStorage.getItem(SERVERS_KEY) || "[]");
    if (Array.isArray(saved)) selectedServers = new Set(saved);
  } catch { /* corrupted entry: start empty */ }

  function persistServers() {
    localStorage.setItem(SERVERS_KEY, JSON.stringify([...selectedServers]));
  }

  // -- API helpers ---------------------------------------------------------

  async function api(path) {
    const resp = await fetch(path, { headers: { Authorization: "Bearer " + token } });
    if (resp.status === 401) {
      const err = new Error("unauthorized");
      err.unauthorized = true;
      throw err;
    }
    if (!resp.ok) throw new Error(`${path}: HTTP ${resp.status}`);
    return resp.json();
  }

  // -- rendering -----------------------------------------------------------

  function renderServers(servers) {
    els.servers.textContent = "";

    const all = document.createElement("button");
    all.type = "button";
    all.className = "chip-mini";
    all.textContent = "All";
    all.title = "Select all servers";
    all.addEventListener("click", () => {
      selectedServers = new Set(servers.map((s) => s.uri));
      persistServers();
      renderServers(servers);
      reapplyVisibility();
    });
    els.servers.appendChild(all);

    const none = document.createElement("button");
    none.type = "button";
    none.className = "chip-mini";
    none.textContent = "None";
    none.title = "Clear selection";
    none.addEventListener("click", () => {
      selectedServers = new Set();
      persistServers();
      renderServers(servers);
      reapplyVisibility();
    });
    els.servers.appendChild(none);

    for (const s of servers) {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "server-chip " + (s.connected ? "on" : "off")
        + (selectedServers.has(s.uri) ? " selected" : "");
      chip.title = s.connected
        ? `${s.uri}\nClick to ${selectedServers.has(s.uri) ? "hide" : "show"} its VMs`
        : `${s.uri}\n${s.error || "not connected"}`;
      chip.textContent = `${s.name} (${s.vm_count})`;
      chip.addEventListener("click", () => {
        if (selectedServers.has(s.uri)) selectedServers.delete(s.uri);
        else selectedServers.add(s.uri);
        persistServers();
        renderServers(servers);
        reapplyVisibility();
      });
      els.servers.appendChild(chip);
    }
    lastServers = servers;
  }

  let lastServers = [];

  function reapplyVisibility() {
    for (const card of cards.values()) applyFilter(card);
    updateEmptyState();
  }

  function makeSpark(card, metric) {
    const canvas = card.querySelector(".spark-canvas." + metric);
    const [low, high] = PALETTES[metric];
    const scale = (metric === "cpu" || metric === "mem") ? scalePercent : scaleLog;
    return new Sparkline(canvas, scale, low, high);
  }

  function buildCard(vm) {
    const el = els.template.content.firstElementChild.cloneNode(true);
    el.dataset.id = vm.id;
    el.querySelector(".vm-name").textContent = vm.name;
    el.querySelector(".vm-server").textContent = vm.server;
    el.querySelector(".vm-vcpus").textContent = `${vm.vcpus} vCPU`;
    el.querySelector(".vm-mem").textContent = vm.memory_mb >= 1024
      ? `${(vm.memory_mb / 1024).toFixed(1)} GiB`
      : `${vm.memory_mb} MiB`;

    // Quick action bar (visibility updated per status).
    const actionsEl = el.querySelector(".actions");
    for (const [act, cfg] of Object.entries(ACTIONS)) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "act" + (cfg.danger ? " danger" : "");
      btn.dataset.act = act;
      btn.textContent = cfg.icon;
      btn.title = cfg.label;
      actionsEl.appendChild(btn);
    }
    // \u22ef more-actions menu (always visible, like the TUI actions button)
    const more = document.createElement("button");
    more.type = "button";
    more.className = "act more";
    more.dataset.act = "menu";
    more.textContent = "\u22ef";
    more.title = "More actions";
    actionsEl.appendChild(more);

    const sparks = {
      cpu: makeSpark(el, "cpu"),
      mem: makeSpark(el, "mem"),
      net: makeSpark(el, "net"),
      disk: makeSpark(el, "disk"),
    };
    // Carry over history if the card already existed (inventory refresh).
    const prev = cards.get(vm.id);
    if (prev) {
      for (const key of Object.keys(sparks)) sparks[key].restore(prev.sparks[key]);
    }
    return { vm, el, sparks };
  }

  function updateCardStatic(card, vm) {
    card.vm = vm;
    card.el.querySelector(".vm-name").textContent = vm.name;
    card.el.querySelector(".vm-server").textContent = vm.server;
    card.el.querySelector(".vm-vcpus").textContent = `${vm.vcpus} vCPU`;
    card.el.querySelector(".vm-mem").textContent = vm.memory_mb >= 1024
      ? `${(vm.memory_mb / 1024).toFixed(1)} GiB`
      : `${vm.memory_mb} MiB`;
    card.el.querySelector(".vm-ip").textContent = vm.ipv4 || "";
    setStatus(card, vm.status);
  }

  function setStatus(card, statusText) {
    const dot = card.el.querySelector(".status-dot");
    const label = card.el.querySelector(".vm-status");
    const cls = STATUS_COLORS[statusText] || "unknown";
    dot.className = "status-dot " + cls;
    label.textContent = statusText;
    card.el.dataset.status = cls;
    updateActionButtons(card);
  }

  function updateActionButtons(card) {
    const vmStatus = card.vm ? card.vm.status : null;
    for (const btn of card.el.querySelectorAll(".actions .act")) {
      if (btn.dataset.act === "menu") { btn.hidden = false; continue; }
      const cfg = ACTIONS[btn.dataset.act];
      btn.hidden = !cfg || !cfg.states.includes(vmStatus);
    }
  }

  function applyFilter(card) {
    const matchServer = selectedServers.has(card.vm.uri);
    const matchText = !filterText
      || card.vm.name.toLowerCase().includes(filterText)
      || card.vm.server.toLowerCase().includes(filterText);
    const match = matchServer && matchText;
    card.el.style.display = match ? "" : "none";
    return match;
  }

  function renderInventory(vms) {
    const seen = new Set();
    for (const vm of vms) {
      seen.add(vm.id);
      let card = cards.get(vm.id);
      if (!card) {
        card = buildCard(vm);
        cards.set(vm.id, card);
        els.cards.appendChild(card.el);
      }
      updateCardStatic(card, vm);
      applyFilter(card);
    }
    // Drop cards for VMs that disappeared.
    for (const [id, card] of cards) {
      if (!seen.has(id)) {
        for (const spark of Object.values(card.sparks)) {
          window.removeEventListener("resize", spark._onResize);
        }
        card.el.remove();
        cards.delete(id);
      }
    }
    updateEmptyState();
  }

  function updateEmptyState() {
    const anyVisible = [...cards.values()].some((c) => c.el.style.display !== "none");
    if (selectedServers.size === 0) {
      els.empty.textContent = "Select one or more servers above to display their VMs.";
      els.empty.hidden = false;
      return;
    }
    els.empty.textContent = filterText
      ? "No VMs match the current filter."
      : "No VMs found. Are the hypervisors connected?";
    els.empty.hidden = anyVisible;
  }

  // -- live stats ----------------------------------------------------------

  function applyStats(stats) {
    let unknown = false;
    for (const [id, s] of Object.entries(stats)) {
      const card = cards.get(id);
      if (!card) {
        // Only re-pull the inventory for new VMs on servers we display;
        // stats for unselected servers are expected and must not loop.
        const uri = id.slice(id.indexOf("@") + 1);
        if (selectedServers.has(uri)) unknown = true;
        continue;
      }
      const net = Math.max(s.net_rx_kbps || 0, s.net_tx_kbps || 0);
      const disk = Math.max(s.disk_read_kbps || 0, s.disk_write_kbps || 0);
      const running = s.status === "Running" || s.status === "Blocked" || s.status === "Paused";
      card.sparks.cpu.push(running ? s.cpu_percent : null);
      card.sparks.mem.push(running ? s.mem_percent : null);
      card.sparks.net.push(running ? net : null);
      card.sparks.disk.push(running ? disk : null);
      for (const spark of Object.values(card.sparks)) spark.draw();

      card.el.querySelector(".cpu-value").textContent = running ? `${s.cpu_percent.toFixed(0)}%` : "--";
      card.el.querySelector(".mem-value").textContent = running ? `${s.mem_percent.toFixed(0)}%` : "--";
      card.el.querySelector(".net-value").textContent = running ? formatRate(net) : "--";
      card.el.querySelector(".disk-value").textContent = running ? formatRate(disk) : "--";
      if (s.status && s.status !== card.vm.status) {
        card.vm.status = s.status;
        setStatus(card, s.status);
      } else if (s.status) {
        updateActionButtons(card); // keep buttons in sync on re-push
      }
    }
    if (unknown) scheduleRefresh(2000); // new VM appeared; re-pull inventory
  }

  function setWsState(connected) {
    els.wsStatus.classList.toggle("online", connected);
    els.wsStatus.title = connected ? "Live stats: connected" : "Live stats: disconnected";
  }

  function connectWS() {
    if (!token) return;
    // Guard against parallel sockets (e.g. reconnect timer racing a manual
    // connect after a token change).
    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${proto}//${location.host}/ws/stats?token=${encodeURIComponent(token)}`;
    try {
      ws = new WebSocket(url);
    } catch {
      setTimeout(connectWS, WS_RETRY_MS);
      return;
    }
    ws.onopen = () => setWsState(true);
    ws.onmessage = (evt) => {
      try {
        const msg = JSON.parse(evt.data);
        applyStats(msg.stats || {});
      } catch (err) {
        console.error("Bad stats message", err);
      }
    };
    ws.onclose = (evt) => {
      setWsState(false);
      ws = null;
      if (evt.code === 4401) {
        showTokenModal(); // token rejected
        return;
      }
      setTimeout(connectWS, WS_RETRY_MS);
    };
    ws.onerror = () => { if (ws) ws.close(); };
  }

  // -- inventory refresh ---------------------------------------------------

  let refreshPending = false;
  function scheduleRefresh(delay = 0) {
    if (refreshPending) return;
    refreshPending = true;
    setTimeout(async () => {
      refreshPending = false;
      try {
        const [servers, vms] = await Promise.all([api("/api/servers"), api("/api/vms")]);
        renderServers(servers);
        renderInventory(vms);
      } catch (err) {
        if (err.unauthorized) showTokenModal();
        else console.error("Refresh failed", err);
      }
    }, delay);
  }

  function startPeriodicRefresh() {
    if (refreshTimer) clearInterval(refreshTimer);
    refreshTimer = setInterval(() => scheduleRefresh(), INVENTORY_REFRESH_MS);
  }

  // -- VM actions -----------------------------------------------------------

  function setCardBusy(card, busy) {
    card.el.classList.toggle("busy", busy);
    for (const btn of card.el.querySelectorAll(".actions .act")) btn.disabled = busy;
  }

  async function doAction(card, act) {
    const cfg = ACTIONS[act];
    setCardBusy(card, true);
    try {
      const resp = await fetch("/api/vms/action", {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: "Bearer " + token },
        body: JSON.stringify({ id: card.vm.id, action: act }),
      });
      if (resp.status === 401) { showTokenModal(); return; }
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`);
      showToast(`${cfg.label} \u2014 ${card.vm.name}: OK`, "ok");
      if (data.status) {
        card.vm.status = data.status;
        setStatus(card, data.status);
      }
      scheduleRefresh(1500); // settle: pull the post-action inventory
    } catch (err) {
      showToast(`${cfg.label} failed: ${err.message}`, "error");
    } finally {
      setCardBusy(card, false);
    }
  }

  els.cards.addEventListener("click", (evt) => {
    const btn = evt.target.closest(".act");
    if (btn) {
      if (btn.disabled) return;
      const cardEl = btn.closest(".card");
      const card = cardEl && cards.get(cardEl.dataset.id);
      if (!card) return;
      const act = btn.dataset.act;
      if (act === "menu") { openActMenu(card, btn); return; }
      const cfg = ACTIONS[act];
      if (!cfg) return;
      if (cfg.ui) { openDetail(card, "snapshots"); return; }
      if (cfg.confirm) {
        askConfirm(cfg.label, card.vm.name, () => doAction(card, act));
      } else {
        doAction(card, act);
      }
      return;
    }
    // Click anywhere else on the card opens the detail view.
    const cardEl = evt.target.closest(".card");
    const card = cardEl && cards.get(cardEl.dataset.id);
    if (card) openDetail(card);
  });

  // -- detail modal ----------------------------------------------------------

  let detailCard = null;
  let detailData = null;
  let detailTab = "overview";

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function fmtCell(v) {
    return (v === null || v === undefined || v === "") ? "--" : String(v);
  }

  function kvTable(pairs) {
    const table = el("table", "kv");
    const tbody = el("tbody");
    for (const [k, v] of pairs) {
      const tr = el("tr");
      tr.appendChild(el("th", null, k));
      tr.appendChild(el("td", null, fmtCell(v)));
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    return table;
  }

  function gridTable(headers, rows) {
    if (!rows.length) return el("p", "detail-loading", "None.");
    const table = el("table", "grid");
    const thead = el("thead");
    const trh = el("tr");
    headers.forEach((h) => trh.appendChild(el("th", null, h)));
    thead.appendChild(trh);
    table.appendChild(thead);
    const tbody = el("tbody");
    for (const row of rows) {
      const tr = el("tr");
      row.forEach((c) => tr.appendChild(el("td", null, fmtCell(c))));
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    return table;
  }

  function renderOverview(d) {
    const fw = d.firmware || {};
    const boot = d.boot || {};
    return kvTable([
      ["Status", d.status],
      ["Description", d.description],
      ["UUID", d.uuid],
      ["vCPUs", d.vcpus],
      ["CPU model", d.cpu_model],
      ["Memory", d.memory_mb >= 1024 ? `${(d.memory_mb / 1024).toFixed(1)} GiB` : `${d.memory_mb} MiB`],
      ["Machine type", d.machine_type],
      ["Firmware", fw.type + (fw.secure_boot ? " (Secure Boot)" : "")],
      ["Firmware path", fw.path],
      ["Boot order", (boot.order || []).join(" \u2192 ")],
      ["Boot menu", boot.menu_enabled ? "enabled" : "disabled"],
      ["Video model", d.video_model],
      ["Shared memory", d.shared_memory ? "yes" : "no"],
    ]);
  }

  function renderNetworks(d) {
    const frag = document.createDocumentFragment();
    frag.appendChild(el("h3", null, "Interfaces (definition)"));
    frag.appendChild(gridTable(
      ["MAC", "Network", "Model"],
      (d.networks || []).map((n) => [n.mac, n.network, n.model]),
    ));
    frag.appendChild(el("h3", null, "Lease IPs"));
    frag.appendChild(gridTable(
      ["Interface", "MAC", "IPv4", "IPv6"],
      (d.detail_network || []).map((i) => [i.interface, i.mac, (i.ipv4 || []).join(", "), (i.ipv6 || []).join(", ")]),
    ));
    const dns = d.network_dns_gateway || [];
    if (dns.length) {
      frag.appendChild(el("h3", null, "DNS / Gateway"));
      frag.appendChild(gridTable(
        ["Network", "Gateway", "DNS servers"],
        dns.map((n) => [n.network_name, n.gateway, (n.dns_servers || []).join(", ")]),
      ));
    }
    return frag;
  }

  function renderDisks(d) {
    return gridTable(
      ["Path", "Bus", "Cache", "Discard", "Status"],
      (d.disks || []).map((k) => [k.path, k.bus, k.cache_mode, k.discard_mode, k.status]),
    );
  }

  function renderDevices(d) {
    const frag = document.createDocumentFragment();
    const devices = d.devices || {};
    let any = false;
    for (const [cat, items] of Object.entries(devices)) {
      if (!items || !items.length) continue;
      any = true;
      frag.appendChild(el("h3", null, cat));
      const list = el("ul", "device-list");
      for (const item of items) {
        const text = (item && typeof item === "object")
          ? Object.entries(item).map(([k, v]) => `${k}=${v}`).join(", ")
          : String(item);
        list.appendChild(el("li", null, text));
      }
      frag.appendChild(list);
    }
    if (!any) frag.appendChild(el("p", "detail-loading", "No devices reported."));
    return frag;
  }

  function renderXml(d) {
    const wrap = el("div");
    const btn = el("button", "secondary copy-btn", "Copy XML");
    btn.type = "button";
    btn.addEventListener("click", () => {
      navigator.clipboard.writeText(d.xml || "").then(
        () => showToast("XML copied to clipboard", "ok"),
        () => showToast("Copy failed", "error"),
      );
    });
    wrap.appendChild(btn);
    wrap.appendChild(el("pre", "xml", d.xml));
    return wrap;
  }

  const DETAIL_RENDERERS = {
    overview: renderOverview,
    networks: renderNetworks,
    disks: renderDisks,
    devices: renderDevices,
    snapshots: renderSnapshots,
    xml: renderXml,
  };

  function renderDetail() {
    els.detailContent.textContent = "";
    if (!detailData) {
      els.detailContent.appendChild(el("p", "detail-loading", "Loading..."));
      return;
    }
    const renderer = DETAIL_RENDERERS[detailTab] || renderOverview;
    els.detailContent.appendChild(renderer(detailData));
  }

  function setDetailTab(tab) {
    detailTab = tab;
    for (const t of els.detailTabs.querySelectorAll(".tab")) {
      t.classList.toggle("active", t.dataset.tab === tab);
    }
    renderDetail();
    if (tab === "snapshots") loadSnapshots();
  }

  async function loadDetail() {
    if (!detailCard) return;
    detailData = null;
    renderDetail();
    try {
      const resp = await fetch(
        `/api/vms/detail?id=${encodeURIComponent(detailCard.vm.id)}`,
        { headers: { Authorization: "Bearer " + token } },
      );
      if (resp.status === 401) { closeDetail(); showTokenModal(); return; }
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      detailData = await resp.json();
      els.detailTitle.textContent = detailData.name;
      els.detailServer.textContent = detailData.server;
      els.detailDot.className = "status-dot " + (STATUS_COLORS[detailData.status] || "unknown");
      renderDetail();
      if (detailTab === "snapshots") loadSnapshots();
    } catch (err) {
      els.detailContent.textContent = "";
      els.detailContent.appendChild(el("p", "detail-loading", "Failed to load details: " + err.message));
    }
  }

  function openDetail(card, tab) {
    detailCard = card;
    els.detailModal.hidden = false;
    els.detailTitle.textContent = card.vm.name;
    els.detailServer.textContent = card.vm.server;
    els.detailDot.className = "status-dot " + (STATUS_COLORS[card.vm.status] || "unknown");
    setDetailTab(tab || "overview");
    loadDetail();
  }

  function closeDetail() {
    els.detailModal.hidden = true;
    detailCard = null;
    detailData = null;
  }

  els.detailClose.addEventListener("click", closeDetail);
  els.detailRefresh.addEventListener("click", loadDetail);
  els.detailTabs.addEventListener("click", (e) => {
    const t = e.target.closest(".tab");
    if (t) setDetailTab(t.dataset.tab);
  });
  els.detailModal.addEventListener("click", (e) => {
    if (e.target === els.detailModal) closeDetail();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    if (!els.actMenu.hidden) closeActMenu();
    else if (!els.renameModal.hidden) els.renameCancel.click();
    else if (!els.deleteModal.hidden) els.deleteCancel.click();
    else if (!els.detailModal.hidden) closeDetail();
    else if (!els.confirmModal.hidden) els.confirmCancel.click();
    else if (!els.tokenModal.hidden) hideTokenModal();
  });

  // -- generic POST helper -----------------------------------------------------

  async function postJSON(url, body) {
    const resp = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: "Bearer " + token },
      body: JSON.stringify(body),
    });
    if (resp.status === 401) {
      showTokenModal();
      throw new Error("unauthorized");
    }
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`);
    return data;
  }

  // -- \u22ef actions menu --------------------------------------------------------

  let menuCard = null;

  function openActMenu(card, anchor) {
    menuCard = card;
    els.actMenu.hidden = false;
    const r = anchor.getBoundingClientRect();
    const mw = els.actMenu.offsetWidth;
    const mh = els.actMenu.offsetHeight;
    let left = r.left;
    let top = r.bottom + 4;
    if (left + mw > window.innerWidth - 8) left = window.innerWidth - mw - 8;
    if (top + mh > window.innerHeight - 8) top = Math.max(8, r.top - mh - 4);
    els.actMenu.style.left = `${left}px`;
    els.actMenu.style.top = `${top}px`;
  }

  function closeActMenu() {
    els.actMenu.hidden = true;
    menuCard = null;
  }

  document.addEventListener("click", (e) => {
    if (!els.actMenu.hidden && !e.target.closest("#act-menu") && !e.target.closest(".act.more")) {
      closeActMenu();
    }
  });
  window.addEventListener("resize", closeActMenu);
  window.addEventListener("scroll", closeActMenu, true);

  els.actMenu.addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b || !menuCard) return;
    const card = menuCard;
    closeActMenu();
    const mact = b.dataset.mact;
    if (mact === "rename") askRename(card);
    else if (mact === "delete") askDelete(card);
    else if (mact === "xml") openDetail(card, "xml");
    else if (mact === "snapshots") openDetail(card, "snapshots");
  });

  // -- rename -------------------------------------------------------------------

  let renameCard = null;

  function askRename(card) {
    renameCard = card;
    els.renameInput.value = card.vm.name;
    els.renameModal.hidden = false;
    els.renameInput.focus();
    els.renameInput.select();
  }

  async function doRename() {
    const name = els.renameInput.value.trim();
    const card = renameCard;
    els.renameModal.hidden = true;
    renameCard = null;
    if (!name || !card) return;
    try {
      const data = await postJSON("/api/vms/rename", { id: card.vm.id, new_name: name });
      showToast(`Renamed ${data.old_name} \u2192 ${data.new_name}`, "ok");
      card.vm.name = data.new_name;
      card.el.querySelector(".vm-name").textContent = data.new_name;
      scheduleRefresh(1000);
    } catch (err) {
      if (err.message !== "unauthorized") showToast(`Rename failed: ${err.message}`, "error");
    }
  }

  els.renameOk.addEventListener("click", doRename);
  els.renameCancel.addEventListener("click", () => {
    els.renameModal.hidden = true;
    renameCard = null;
  });
  els.renameInput.addEventListener("keydown", (e) => { if (e.key === "Enter") doRename(); });

  // -- delete -------------------------------------------------------------------

  let deleteCard = null;

  function askDelete(card) {
    deleteCard = card;
    els.deleteText.textContent =
      `Permanently undefine "${card.vm.name}" from "${card.vm.server}"? This cannot be undone.`;
    els.deleteStorage.checked = false;
    els.deleteNvram.checked = false;
    els.deleteModal.hidden = false;
    els.deleteCancel.focus();
  }

  async function doDelete() {
    const card = deleteCard;
    els.deleteModal.hidden = true;
    deleteCard = null;
    if (!card) return;
    const name = card.vm.name;
    try {
      await postJSON("/api/vms/delete", {
        id: card.vm.id,
        delete_storage: els.deleteStorage.checked,
        delete_nvram: els.deleteNvram.checked,
      });
      showToast(`Deleted ${name}`, "ok");
      scheduleRefresh(0);
    } catch (err) {
      if (err.message !== "unauthorized") showToast(`Delete failed: ${err.message}`, "error");
    }
  }

  els.deleteOk.addEventListener("click", doDelete);
  els.deleteCancel.addEventListener("click", () => {
    els.deleteModal.hidden = true;
    deleteCard = null;
  });

  // -- snapshots -----------------------------------------------------------------

  async function loadSnapshots() {
    if (!detailCard || !detailData) return;
    detailData.snapshots = null;
    detailData.snapshotsError = null;
    try {
      const resp = await fetch(
        `/api/vms/snapshots?id=${encodeURIComponent(detailCard.vm.id)}`,
        { headers: { Authorization: "Bearer " + token } },
      );
      if (resp.status === 401) { closeDetail(); showTokenModal(); return; }
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      detailData.snapshots = await resp.json();
    } catch (err) {
      detailData.snapshotsError = err.message;
    }
    if (detailTab === "snapshots") renderDetail();
  }

  async function snapshotOp(kind, payload, okMsg) {
    const urls = {
      create: "/api/vms/snapshots",
      restore: "/api/vms/snapshots/restore",
      delete: "/api/vms/snapshots/delete",
    };
    try {
      await postJSON(urls[kind], payload);
      showToast(okMsg, "ok");
      loadSnapshots();
      if (kind !== "create") scheduleRefresh(1500); // state may have changed
    } catch (err) {
      if (err.message !== "unauthorized") {
        showToast(`Snapshot ${kind} failed: ${err.message}`, "error");
      }
    }
  }

  function renderSnapshots(d) {
    const frag = document.createDocumentFragment();
    const vmId = detailCard ? detailCard.vm.id : d.id;

    // Create form
    const form = el("div", "snap-form");
    const nameIn = el("input", "snap-name");
    nameIn.type = "text";
    nameIn.placeholder = "Snapshot name";
    const descIn = el("input", "snap-desc");
    descIn.type = "text";
    descIn.placeholder = "Description (optional)";
    const qLabel = el("label", "check");
    const qBox = document.createElement("input");
    qBox.type = "checkbox";
    qLabel.appendChild(qBox);
    qLabel.appendChild(document.createTextNode("Quiesce"));
    const createBtn = el("button", null, "\ud83d\udcf7 Create");
    createBtn.type = "button";
    createBtn.addEventListener("click", () => {
      const name = nameIn.value.trim();
      if (!name) { showToast("Snapshot name is required", "error"); return; }
      snapshotOp(
        "create",
        { id: vmId, name, description: descIn.value.trim(), quiesce: qBox.checked },
        `Snapshot "${name}" created`,
      );
      nameIn.value = "";
      descIn.value = "";
      qBox.checked = false;
    });
    form.append(nameIn, descIn, qLabel, createBtn);
    frag.appendChild(form);

    if (d.snapshotsError) {
      frag.appendChild(el("p", "detail-loading", "Failed to load snapshots: " + d.snapshotsError));
      return frag;
    }
    if (!d.snapshots) {
      frag.appendChild(el("p", "detail-loading", "Loading..."));
      return frag;
    }
    if (!d.snapshots.length) {
      frag.appendChild(el("p", "detail-loading", "No snapshots for this VM."));
      return frag;
    }

    const table = el("table", "grid");
    const thead = el("thead");
    const trh = el("tr");
    ["Name", "Created", "State", "Description", ""].forEach((h) => trh.appendChild(el("th", null, h)));
    thead.appendChild(trh);
    table.appendChild(thead);
    const tbody = el("tbody");
    for (const s of d.snapshots) {
      const tr = el("tr");
      tr.appendChild(el("td", null, s.name));
      tr.appendChild(el("td", null, s.creation_time));
      tr.appendChild(el("td", null, s.state));
      tr.appendChild(el("td", null, s.description));
      const td = el("td", "snap-actions");
      const restoreB = el("button", "secondary mini", "Restore");
      restoreB.type = "button";
      restoreB.title = "Revert the VM to this snapshot";
      restoreB.addEventListener("click", () => {
        askConfirm(`Restore snapshot "${s.name}"`, d.name, () => {
          snapshotOp("restore", { id: vmId, name: s.name }, `Restored snapshot "${s.name}"`);
        });
      });
      const deleteB = el("button", "danger mini", "Delete");
      deleteB.type = "button";
      deleteB.addEventListener("click", () => {
        askConfirm(`Delete snapshot "${s.name}"`, d.name, () => {
          snapshotOp("delete", { id: vmId, name: s.name }, `Deleted snapshot "${s.name}"`);
        });
      });
      td.append(restoreB, deleteB);
      tr.appendChild(td);
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    frag.appendChild(table);
    return frag;
  }

  // -- confirm modal ---------------------------------------------------------

  let confirmCallback = null;

  function askConfirm(actionLabel, vmName, onConfirm) {
    els.confirmTitle.textContent = actionLabel;
    els.confirmText.textContent = `Apply "${actionLabel}" to "${vmName}"?`;
    confirmCallback = onConfirm;
    els.confirmModal.hidden = false;
    els.confirmOk.focus();
  }

  els.confirmOk.addEventListener("click", () => {
    els.confirmModal.hidden = true;
    const cb = confirmCallback;
    confirmCallback = null;
    if (cb) cb();
  });
  els.confirmCancel.addEventListener("click", () => {
    els.confirmModal.hidden = true;
    confirmCallback = null;
  });

  // -- toast ------------------------------------------------------------------

  let toastTimer = null;
  function showToast(message, kind) {
    els.toast.textContent = message;
    els.toast.className = kind === "error" ? "error" : "ok";
    els.toast.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { els.toast.hidden = true; }, 4000);
  }

  // -- token modal ---------------------------------------------------------

  function showTokenModal() {
    els.tokenModal.hidden = false;
    els.tokenInput.value = "";
    els.tokenInput.focus();
  }

  function hideTokenModal() { els.tokenModal.hidden = true; }

  function saveToken() {
    const value = els.tokenInput.value.trim();
    if (!value) return;
    token = value;
    localStorage.setItem(TOKEN_KEY, token);
    hideTokenModal();
    if (ws) {
      const old = ws;
      ws = null; // detach first so onclose's retry timer hits the guard
      try { old.close(); } catch { /* ignore */ }
    }
    scheduleRefresh();
    connectWS();
  }

  // -- init ----------------------------------------------------------------

  els.filter.addEventListener("input", () => {
    filterText = els.filter.value.trim().toLowerCase();
    for (const card of cards.values()) applyFilter(card);
    updateEmptyState();
  });
  els.tokenBtn.addEventListener("click", showTokenModal);
  els.tokenSave.addEventListener("click", saveToken);
  els.tokenCancel.addEventListener("click", hideTokenModal);
  els.tokenInput.addEventListener("keydown", (e) => { if (e.key === "Enter") saveToken(); });

  if (!token) {
    showTokenModal();
  } else {
    scheduleRefresh();
    connectWS();
  }
  startPeriodicRefresh();
})();
