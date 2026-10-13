(() => {
  "use strict";

  const POLL_MS = 5 * 60 * 1000;
  const WINDOW_MS = 24 * 60 * 60 * 1000;
  const STORE_KEY = "midnight-watch.events.v1";

  // GDELT DOC 2.0 API: English-language articles from major outlets whose
  // text matches geopolitical keywords, ranked by relevance and recency.
  const KEYWORDS = [
    "sanctions", "missile", "nuclear", "invasion", "airstrike", "coup",
    "ceasefire", "troops", "military", '"peace talks"', "annexation",
    "blockade", '"state of emergency"', "NATO", '"security council"',
  ];
  const OUTLETS = [
    "reuters.com", "apnews.com", "bbc.com", "bbc.co.uk", "aljazeera.com",
    "theguardian.com", "nytimes.com", "ft.com", "npr.org", "cnn.com",
    "washingtonpost.com", "france24.com", "dw.com",
  ];
  const QUERY =
    `(${KEYWORDS.join(" OR ")}) ` +
    `(${OUTLETS.map((d) => "domainis:" + d).join(" OR ")}) sourcelang:english`;
  const GDELT_URL =
    "https://api.gdeltproject.org/api/v2/doc/doc?" +
    new URLSearchParams({
      query: QUERY,
      mode: "artlist",
      format: "json",
      timespan: "24h",
      maxrecords: "75",
      sort: "hybridrel",
    });
  // Used only if the browser can't reach GDELT directly (e.g. a CORS failure).
  const FALLBACK_PROXY = "https://api.allorigins.win/raw?url=";

  const $ = (id) => document.getElementById(id);
  const SVG_NS = "http://www.w3.org/2000/svg";

  let events = loadEvents();
  let activeKey = null;
  let freshKeys = new Set();
  let lastFetch = null;
  let fetching = false;

  // ---------- geometry ----------

  const msSinceMidnight = (d) =>
    d.getHours() * 3600e3 + d.getMinutes() * 60e3 + d.getSeconds() * 1e3 + d.getMilliseconds();

  const angleFor = (d) => (msSinceMidnight(d) / WINDOW_MS) * Math.PI * 2;

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

  // ---------- events ----------

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

  function parseSeenDate(s) {
    const m = /^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z$/.exec(s || "");
    if (!m) return NaN;
    return Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6]);
  }

  // Wire services syndicate the same story under many URLs; key on the headline.
  const titleKey = (t) => t.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim().slice(0, 70);

  function merge(articles) {
    const known = new Set(events.map((e) => e.key));
    const added = [];
    for (const a of articles) {
      const ts = parseSeenDate(a.seendate);
      const title = (a.title || "").trim();
      if (!title || !a.url || !Number.isFinite(ts)) continue;
      const key = titleKey(title);
      if (known.has(key)) continue;
      known.add(key);
      const ev = { key, title, url: a.url, source: a.domain || new URL(a.url).hostname, ts };
      events.push(ev);
      added.push(ev);
    }
    return added;
  }

  function prune(now) {
    events = events.filter((e) => now - e.ts < WINDOW_MS && e.ts <= now + 5 * 60e3);
    events.sort((a, b) => b.ts - a.ts);
  }

  async function fetchJson(url) {
    const get = async (u) => {
      const res = await fetch(u, { cache: "no-store" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const text = await res.text();
      // GDELT returns plain-text errors (e.g. rate limits) with a 200 status.
      try {
        return JSON.parse(text);
      } catch {
        throw new Error(text.slice(0, 120) || "Bad response");
      }
    };
    try {
      return await get(url);
    } catch (direct) {
      try {
        return await get(FALLBACK_PROXY + encodeURIComponent(url));
      } catch {
        throw direct;
      }
    }
  }

  async function poll() {
    if (fetching) return;
    fetching = true;
    $("refresh").disabled = true;
    setStatus("Checking the wires…");
    try {
      const data = await fetchJson(GDELT_URL);
      const isFirstLoad = events.length === 0;
      const added = merge(data.articles || []);
      const now = Date.now();
      prune(now);
      saveEvents();
      freshKeys = isFirstLoad ? new Set() : new Set(added.map((e) => e.key));
      lastFetch = new Date();
      setStatus(
        `Updated ${timeFmt.format(lastFetch)}` + (added.length && !isFirstLoad ? ` · ${added.length} new` : "")
      );
      if (freshKeys.size && navigator.vibrate) navigator.vibrate(80);
    } catch (err) {
      setStatus(`Couldn't reach the news feed (${err.message}). Showing saved events.`, true);
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

  // ---------- render ----------

  function startOfToday(now) {
    const d = new Date(now);
    d.setHours(0, 0, 0, 0);
    return d.getTime();
  }

  function drawMarkers(now) {
    const g = $("markers");
    g.replaceChildren();
    const today0 = startOfToday(now);
    // Oldest first so newer markers render on top; nudge clustered markers outward.
    const sorted = [...events].sort((a, b) => a.ts - b.ts);
    const placed = [];
    for (const ev of sorted) {
      const a = angleFor(new Date(ev.ts));
      let level = 0;
      while (placed.some((p) => p.level === level && Math.abs(p.a - a) < 0.045) && level < 2) level++;
      placed.push({ a, level });

      const r = 193 + level * 13;
      const [sx, sy] = polar(a, 182);
      const [px, py] = polar(a, r);
      const isToday = ev.ts >= today0;
      const cls = ["marker"];
      if (!isToday) cls.push("past");
      if (ev.key === activeKey) cls.push("active");
      if (freshKeys.has(ev.key)) cls.push("fresh");

      const mg = el("g", { class: cls.join(" "), "data-key": ev.key, tabindex: "0", role: "button" }, g);
      el("title", {}, mg).textContent = `${timeFmt.format(ev.ts)} — ${ev.title}`;
      el("circle", { class: "halo", cx: px, cy: py, r: 13 }, mg);
      el("line", { class: "stem", x1: sx, y1: sy, x2: px, y2: py }, mg);
      el("circle", { class: "pip", cx: px, cy: py, r: 6.5 }, mg);
    }
  }

  function drawList(now) {
    const list = $("events");
    list.replaceChildren();
    const today0 = startOfToday(now);
    const today = events.filter((e) => e.ts >= today0);
    const earlier = events.filter((e) => e.ts < today0);

    $("legend-text").textContent =
      `${plural(today.length, "event")} since midnight` +
      (earlier.length ? ` · ${earlier.length} earlier (hollow)` : "");

    if (!events.length) {
      const li = document.createElement("li");
      li.className = "empty";
      li.textContent = "No events logged in the last 24 hours.";
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
        li.id = "ev-" + ev.key.replace(/ /g, "-");
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
        const src = document.createElement("span");
        src.className = "src";
        src.textContent = ev.source;
        const when = document.createElement("time");
        when.dateTime = new Date(ev.ts).toISOString();
        when.textContent = `${timeFmt.format(ev.ts)} · ${ago(ev.ts, now)}`;
        meta.append(src, when);

        body.append(a, meta);
        li.append(bullet, body);
        list.appendChild(li);
      }
    };

    group(`Today · ${dateFmt.format(now)}`, today, false);
    group(`Earlier · ${dateFmt.format(today0 - 1)}`, earlier, true);
  }

  function render() {
    const now = new Date();
    prune(now.getTime());
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
  $("refresh").addEventListener("click", poll);

  drawFace();
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
    if (document.visibilityState === "visible" && (!lastFetch || Date.now() - lastFetch > 60e3)) poll();
  });

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("sw.js").catch(() => {});
  }
})();
