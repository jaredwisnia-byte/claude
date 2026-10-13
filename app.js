(() => {
  "use strict";

  const CFG = self.MW_CONFIG;
  const DAY_MS = 24 * 60 * 60 * 1000;
  const WINDOW_MS = CFG.WINDOW_HOURS * 60 * 60 * 1000;
  const POLL_MS = CFG.POLL_SECONDS * 1000;
  const STORE_KEY = "midnight-watch.events.v3";
  const FILTER_KEY = "midnight-watch.filter";
  const CATEGORY_LABEL = Object.fromEntries(CFG.CATEGORIES.map((c) => [c.id, c.label]));

  const $ = (id) => document.getElementById(id);
  const SVG_NS = "http://www.w3.org/2000/svg";

  let events = loadEvents();
  let filter = loadFilter();
  let activeKey = null;
  let freshKeys = new Set();
  let lastFetch = null;
  let fetching = false;

  // ---------- geometry ----------

  const msSinceMidnight = (d) =>
    d.getHours() * 3600e3 + d.getMinutes() * 60e3 + d.getSeconds() * 1e3 + d.getMilliseconds();

  const angleFor = (d) => (msSinceMidnight(d) / DAY_MS) * Math.PI * 2;

  const polar = (angle, r) => [r * Math.sin(angle), -r * Math.cos(angle)];

  function el(tag, attrs, parent) {
    const node = document.createElementNS(SVG_NS, tag);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
    if (parent) parent.appendChild(node);
    return node;
  }

  function drawFace() {
    const ticks = $("ticks");
    const labels = $("labels");
    for (let i = 0; i < 96; i++) {
      const a = (i / 96) * Math.PI * 2;
      const hour = i % 4 === 0;
      const major = i % 12 === 0;
      const inner = major ? 160 : hour ? 166 : 174;
      const [x1, y1] = polar(a, inner);
      const [x2, y2] = polar(a, 182);
      el("line", { x1, y1, x2, y2, class: "tick " + (major ? "major" : hour ? "" : "minor") }, ticks);
    }
    for (let h = 0; h < 24; h += 3) {
      const [x, y] = polar((h / 24) * Math.PI * 2, 140);
      const t = el("text", { x, y, class: "label" + (h === 0 ? " midnight" : "") }, labels);
      t.textContent = h === 0 ? "00" : String(h).padStart(2, "0");
    }
  }

  function drawHand(now) {
    const a = angleFor(now);
    const deg = (a * 180) / Math.PI;
    $("hand").setAttribute("transform", `rotate(${deg})`);

    // Shade the arc still remaining before midnight.
    const R = 182;
    const [sx, sy] = polar(a, R);
    const large = Math.PI * 2 - a > Math.PI ? 1 : 0;
    $("remaining").setAttribute("d", `M0 0 L${sx} ${sy} A${R} ${R} 0 ${large} 1 0 ${-R} Z`);
  }

  // ---------- text ----------

  function plural(n, word) {
    return `${n} ${word}${n === 1 ? "" : "s"}`;
  }

  function drawHeadline(now) {
    const midnight = new Date(now);
    midnight.setHours(24, 0, 0, 0);
    const left = Math.max(0, midnight - now);
    const h = Math.floor(left / 3600e3);
    const m = Math.floor((left % 3600e3) / 60e3);
    const s = Math.floor((left % 60e3) / 1e3);

    let phrase;
    if (h > 0) phrase = `${plural(h, "hour")}, ${plural(m, "minute")}`;
    else if (m > 0) phrase = plural(m, "minute");
    else phrase = plural(s, "second");
    const text = `It is ${phrase} to midnight`;
    if ($("headline").textContent !== text) $("headline").textContent = text;

    const pad = (n) => String(n).padStart(2, "0");
    $("countdown").textContent = `T−${pad(h)}:${pad(m)}:${pad(s)}`;
  }

  const timeFmt = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", hour12: false });
  const dateFmt = new Intl.DateTimeFormat(undefined, { weekday: "short", day: "numeric", month: "short" });

  function ago(ts, now) {
    const mins = Math.round((now - ts) / 60e3);
    if (mins < 1) return "just now";
    if (mins < 60) return `${mins}m ago`;
    const h = Math.floor(mins / 60);
    return `${h}h ${mins % 60}m ago`;
  }

  // ---------- storage ----------

  function loadEvents() {
    try {
      const raw = JSON.parse(localStorage.getItem(STORE_KEY) || "[]");
      return Array.isArray(raw) ? raw : [];
    } catch {
      return [];
    }
  }

  function saveEvents() {
    try {
      localStorage.setItem(STORE_KEY, JSON.stringify(events));
    } catch {
      /* storage unavailable: keep in memory only */
    }
  }

  function loadFilter() {
    try {
      const f = localStorage.getItem(FILTER_KEY);
      return f && (f === "all" || CATEGORY_LABEL[f]) ? f : "all";
    } catch {
      return "all";
    }
  }

  // ---------- events ----------

  function prune(now) {
    events = events.filter((e) => now - e.ts < WINDOW_MS && e.ts <= now + 5 * 60e3);
    events.sort((a, b) => b.ts - a.ts);
  }

  // The collector workflow republishes events.json every few minutes; the query
  // string keeps browser and CDN caches from serving an older copy.
  async function fetchFeed() {
    const res = await fetch(`${CFG.DATA_URL}?t=${Date.now()}`, { cache: "no-store" });
    if (res.status === 404) throw new Error("no data published yet");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    if (!Array.isArray(data.events)) throw new Error("bad data");
    return data;
  }

  async function poll() {
    if (fetching) return;
    fetching = true;
    $("refresh").disabled = true;
    setStatus("Checking for new events…");
    try {
      const data = await fetchFeed();
      const isFirstLoad = events.length === 0;
      const known = new Set(events.map((e) => e.key));
      const added = data.events.filter((e) => !known.has(e.key) && CATEGORY_LABEL[e.category]);
      // Keep events the server has since dropped (e.g. pushed past its cap) until they age out.
      events = [...added, ...events];
      prune(Date.now());
      saveEvents();
      if (!isFirstLoad) for (const ev of added) freshKeys.add(ev.key);
      lastFetch = new Date();
      let msg = `Checked ${timeFmt.format(lastFetch)} · news as of ${timeFmt.format(data.updated)}`;
      if (!isFirstLoad && added.length) msg += ` · ${added.length} new`;
      setStatus(msg);
      if (!isFirstLoad && added.length) notifyLocal(added);
    } catch (err) {
      setStatus(`Couldn't load events (${err.message}). Showing saved events.`, true);
    } finally {
      fetching = false;
      $("refresh").disabled = false;
      render();
    }
  }

  function setStatus(text, isError = false) {
    const s = $("status");
    s.textContent = text;
    s.classList.toggle("error", isError);
  }

  // ---------- notifications ----------

  const isStandalone = () =>
    window.matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
  const isIOS = () => /iPhone|iPad|iPod/.test(navigator.userAgent);
  const notificationsSupported = () => "Notification" in window && "serviceWorker" in navigator;

  // While the app is running, announce new events directly. The tag matches the
  // one the push notifier uses, so the same event isn't announced twice.
  async function notifyLocal(added) {
    if (!notificationsSupported() || Notification.permission !== "granted") return;
    const reg = await navigator.serviceWorker.ready.catch(() => null);
    if (!reg) return;
    const shown = added.filter((e) => filter === "all" || e.category === filter).slice(0, 3);
    for (const ev of shown) {
      reg.showNotification(`${CATEGORY_LABEL[ev.category]} · ${ev.source}`, {
        body: ev.title,
        tag: ev.key,
        icon: "apple-touch-icon.png",
        data: { url: ev.url },
      });
    }
  }

  function b64urlToBytes(s) {
    const pad = "=".repeat((4 - (s.length % 4)) % 4);
    const bin = atob((s + pad).replace(/-/g, "+").replace(/_/g, "/"));
    return Uint8Array.from(bin, (c) => c.charCodeAt(0));
  }

  function renderNotifyPanel() {
    const btn = $("notify-btn");
    const note = $("notify-note");
    const subBox = $("notify-sub");
    subBox.hidden = true;

    if (isIOS() && !isStandalone()) {
      btn.hidden = true;
      note.textContent = "To get notifications, add this app to your Home Screen (Share → Add to Home Screen), then open it from there.";
      return;
    }
    if (!notificationsSupported() || !("PushManager" in window)) {
      btn.hidden = true;
      note.textContent = "This browser doesn't support notifications.";
      return;
    }
    if (Notification.permission === "denied") {
      btn.hidden = true;
      note.textContent = "Notifications are blocked. Turn them on in Settings → Notifications → Midnight.";
      return;
    }
    btn.hidden = false;
    if (Notification.permission === "granted") {
      btn.textContent = "Show push key";
      note.textContent = "Notifications are on while the app is open. To get them while it's closed, copy your push key into the repo (see below).";
    } else {
      btn.textContent = "Enable notifications";
      note.textContent = "Get an alert when a new event is logged.";
    }
  }

  async function enableNotifications() {
    const btn = $("notify-btn");
    btn.disabled = true;
    try {
      const perm = await Notification.requestPermission();
      if (perm !== "granted") return;
      const reg = await navigator.serviceWorker.ready;
      let sub = await reg.pushManager.getSubscription();
      if (!sub) {
        sub = await reg.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: b64urlToBytes(CFG.VAPID_PUBLIC_KEY),
        });
      }
      renderNotifyPanel();
      $("notify-sub-text").value = JSON.stringify(sub);
      $("notify-sub").hidden = false;
    } catch (err) {
      $("notify-note").textContent = `Couldn't enable notifications: ${err.message}`;
    } finally {
      btn.disabled = false;
    }
  }

  async function copySubscription() {
    const text = $("notify-sub-text").value;
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      $("notify-sub-text").select();
      document.execCommand("copy");
    }
    $("notify-copy").textContent = "Copied";
    setTimeout(() => ($("notify-copy").textContent = "Copy push key"), 2000);
  }

  // ---------- render ----------

  function startOfToday(now) {
    const d = new Date(now);
    d.setHours(0, 0, 0, 0);
    return d.getTime();
  }

  const visible = () => (filter === "all" ? events : events.filter((e) => e.category === filter));

  function drawMarkers(now) {
    const g = $("markers");
    g.replaceChildren();
    const today0 = startOfToday(now);
    // Oldest first so newer markers render on top; nudge clustered markers outward.
    const sorted = [...visible()].sort((a, b) => a.ts - b.ts);
    const placed = [];
    for (const ev of sorted) {
      const a = angleFor(new Date(ev.ts));
      let level = 0;
      while (placed.some((p) => p.level === level && Math.abs(p.a - a) < 0.045) && level < 2) level++;
      placed.push({ a, level });

      const r = 193 + level * 13;
      const [sx, sy] = polar(a, 182);
      const [px, py] = polar(a, r);
      const cls = ["marker"];
      if (ev.ts < today0) cls.push("past");
      if (ev.key === activeKey) cls.push("active");
      if (freshKeys.has(ev.key)) cls.push("fresh");

      const mg = el("g", { class: cls.join(" "), "data-key": ev.key, tabindex: "0", role: "button" }, g);
      el("title", {}, mg).textContent = `${timeFmt.format(ev.ts)} — ${ev.title}`;
      el("circle", { class: "halo", cx: px, cy: py, r: 13 }, mg);
      el("line", { class: "stem", x1: sx, y1: sy, x2: px, y2: py }, mg);
      el("circle", { class: "pip", cx: px, cy: py, r: 6.5 }, mg);
    }
  }

  function drawFilters() {
    const bar = $("filters");
    bar.replaceChildren();
    const counts = { all: events.length };
    for (const e of events) counts[e.category] = (counts[e.category] || 0) + 1;
    const chips = [{ id: "all", label: "All" }, ...CFG.CATEGORIES];
    for (const c of chips) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "chip";
      b.dataset.filter = c.id;
      b.setAttribute("aria-pressed", String(filter === c.id));
      b.textContent = `${c.label} ${counts[c.id] || 0}`;
      bar.appendChild(b);
    }
  }

  function drawList(now) {
    const list = $("events");
    list.replaceChildren();
    const today0 = startOfToday(now);
    const shown = visible();
    const today = shown.filter((e) => e.ts >= today0);
    const earlier = shown.filter((e) => e.ts < today0);

    $("legend-text").textContent =
      `${plural(shown.length, "event")} in the last ${CFG.WINDOW_HOURS} hours` +
      (earlier.length ? ` · ${earlier.length} before midnight (hollow)` : "");

    if (!shown.length) {
      const li = document.createElement("li");
      li.className = "empty";
      li.textContent = `No events logged in the last ${CFG.WINDOW_HOURS} hours.`;
      list.appendChild(li);
      return;
    }

    const group = (label, items, past) => {
      if (!items.length) return;
      const h = document.createElement("li");
      h.className = "day-rule";
      h.textContent = label;
      list.appendChild(h);
      for (const ev of items) {
        const li = document.createElement("li");
        li.className = "event" + (past ? " past" : "") + (ev.key === activeKey ? " active" : "");
        li.dataset.key = ev.key;

        const bullet = document.createElement("span");
        bullet.className = "bullet";
        const body = document.createElement("div");

        const a = document.createElement("a");
        a.className = "event-title";
        a.href = ev.url;
        a.target = "_blank";
        a.rel = "noopener";
        a.textContent = ev.title;

        const meta = document.createElement("div");
        meta.className = "event-meta";
        const cat = document.createElement("span");
        cat.className = "cat";
        cat.textContent = CATEGORY_LABEL[ev.category] || ev.category;
        const src = document.createElement("span");
        src.className = "src";
        src.textContent = ev.source;
        const when = document.createElement("time");
        when.dateTime = new Date(ev.ts).toISOString();
        when.textContent = `${timeFmt.format(ev.ts)} · ${ago(ev.ts, now)}`;
        meta.append(cat, src, when);

        body.append(a, meta);
        li.append(bullet, body);
        list.appendChild(li);
      }
    };

    group(`Today · ${dateFmt.format(now)}`, today, false);
    group(`Before midnight · ${dateFmt.format(today0 - 1)}`, earlier, true);
  }

  function render() {
    const now = new Date();
    prune(now.getTime());
    drawFilters();
    drawMarkers(now);
    drawList(now);
  }

  function activate(key, scroll) {
    activeKey = activeKey === key ? null : key;
    render();
    if (activeKey && scroll) {
      const li = document.querySelector(`.event[data-key="${CSS.escape(activeKey)}"]`);
      if (li) li.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }

  // ---------- wiring ----------

  $("markers").addEventListener("click", (e) => {
    const m = e.target.closest(".marker");
    if (m) activate(m.dataset.key, true);
  });
  $("markers").addEventListener("keydown", (e) => {
    const m = e.target.closest(".marker");
    if (m && (e.key === "Enter" || e.key === " ")) {
      e.preventDefault();
      activate(m.dataset.key, true);
    }
  });
  $("events").addEventListener("click", (e) => {
    if (e.target.closest("a")) return;
    const li = e.target.closest(".event");
    if (li) activate(li.dataset.key, false);
  });
  $("filters").addEventListener("click", (e) => {
    const chip = e.target.closest(".chip");
    if (!chip) return;
    filter = chip.dataset.filter;
    try { localStorage.setItem(FILTER_KEY, filter); } catch { /* ignore */ }
    render();
  });
  $("refresh").addEventListener("click", poll);
  $("notify-btn").addEventListener("click", enableNotifications);
  $("notify-copy").addEventListener("click", copySubscription);

  drawFace();
  renderNotifyPanel();
  let lastMinute = -1;
  function tick() {
    const now = new Date();
    drawHand(now);
    drawHeadline(now);
    // Re-render once a minute so "ago" labels and the midnight rollover stay current.
    if (now.getMinutes() !== lastMinute) {
      lastMinute = now.getMinutes();
      render();
    }
  }
  tick();
  setInterval(tick, 1000);

  poll();
  setInterval(poll, POLL_MS);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && (!lastFetch || Date.now() - lastFetch > 30e3)) poll();
  });

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("sw.js").catch(() => {});
  }
})();
