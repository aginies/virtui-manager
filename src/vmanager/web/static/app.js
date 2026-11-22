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
  const INVENTORY_REFRESH_MS = 30000;
  const WS_RETRY_MS = 4000;

  const STATUS_COLORS = {
    Running: "running",
    Blocked: "running",
    Paused: "paused",
    "PM Suspended": "paused",
    "Shut off": "stopped",
    Unknown: "unknown",
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
    template: document.getElementById("card-template"),
  };

  // id -> { vm, el, sparks: {cpu, mem, net, disk}, visible }
  const cards = new Map();
  let filterText = "";
  let ws = null;
  let refreshTimer = null;

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
    for (const s of servers) {
      const chip = document.createElement("span");
      chip.className = "server-chip " + (s.connected ? "on" : "off");
      chip.title = s.connected ? s.uri : `${s.uri}\n${s.error || "not connected"}`;
      chip.textContent = `${s.name} (${s.vm_count})`;
      els.servers.appendChild(chip);
    }
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
  }

  function applyFilter(card) {
    const match = !filterText
      || card.vm.name.toLowerCase().includes(filterText)
      || card.vm.server.toLowerCase().includes(filterText);
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
    els.empty.hidden = cards.size === 0 || anyVisible;
    if (cards.size === 0 && !filterText) els.empty.hidden = false;
  }

  // -- live stats ----------------------------------------------------------

  function applyStats(stats) {
    let unknown = false;
    for (const [id, s] of Object.entries(stats)) {
      const card = cards.get(id);
      if (!card) { unknown = true; continue; }
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
    if (ws) { try { ws.close(); } catch { /* ignore */ } }
    scheduleRefresh();
    connectWS();
  }

  // -- init ----------------------------------------------------------------

  els.filter.addEventListener("input", () => {
    filterText = els.filter.value.trim().toLowerCase();
    let anyVisible = false;
    for (const card of cards.values()) {
      if (applyFilter(card)) anyVisible = true;
    }
    els.empty.hidden = anyVisible || cards.size === 0;
    if (cards.size > 0 && !anyVisible) els.empty.hidden = false;
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
