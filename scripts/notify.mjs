// Polls GDELT for every category and sends a Web Push notification for each new event.
// Run by .github/workflows/notify.yml. Seen events persist between runs in STATE_FILE.
//
// Env:
//   PUSH_SUBSCRIPTION  JSON from the app's "Copy push key" button (one object or an array)
//   VAPID_PRIVATE_KEY  private half of config.js's VAPID_PUBLIC_KEY
//   APP_URL            the deployed app, used as the VAPID subject and summary link
//   STATE_FILE         where seen events are kept (default .notify-state/seen.json)
//   GDELT_FIXTURE      optional JSON file of articles to use instead of GDELT (testing)
import fs from "node:fs";
import path from "node:path";
import webpush from "web-push";
import "../config.js";

const CFG = globalThis.MW_CONFIG;
const MAX_INDIVIDUAL = 3;
const KEEP_SEEN_MS = 24 * 60 * 60 * 1000;
const WINDOW_MS = CFG.WINDOW_HOURS * 60 * 60 * 1000;
const STATE_FILE = process.env.STATE_FILE || ".notify-state/seen.json";
const APP_URL = process.env.APP_URL || "https://example.com/";
const LABEL = Object.fromEntries(CFG.CATEGORIES.map((c) => [c.id, c.label]));

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const warn = (msg) => console.log(`::warning::${msg}`);

function loadState() {
  try {
    return JSON.parse(fs.readFileSync(STATE_FILE, "utf8"));
  } catch {
    return null;
  }
}

function saveState(state) {
  fs.mkdirSync(path.dirname(STATE_FILE), { recursive: true });
  fs.writeFileSync(STATE_FILE, JSON.stringify(state));
}

async function fetchCategory(cat) {
  if (process.env.GDELT_FIXTURE) {
    const all = JSON.parse(fs.readFileSync(process.env.GDELT_FIXTURE, "utf8"));
    return all[cat.id] || [];
  }
  const res = await fetch(CFG.gdeltUrl(cat, CFG.WINDOW_HOURS));
  const text = await res.text();
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${text.slice(0, 120)}`);
  try {
    return JSON.parse(text).articles || [];
  } catch {
    throw new Error(text.slice(0, 120));
  }
}

async function collect() {
  const events = [];
  const keys = new Set();
  let failures = 0;
  for (const [i, cat] of CFG.CATEGORIES.entries()) {
    if (i > 0 && !process.env.GDELT_FIXTURE) await sleep(CFG.REQUEST_GAP_MS);
    try {
      for (const a of await fetchCategory(cat)) {
        const ts = CFG.parseSeenDate(a.seendate);
        const title = (a.title || "").trim();
        if (!title || !a.url || !Number.isFinite(ts)) continue;
        const key = CFG.titleKey(title);
        if (keys.has(key)) continue;
        keys.add(key);
        events.push({ key, title, url: a.url, source: a.domain || new URL(a.url).hostname, ts, category: cat.id });
      }
    } catch (err) {
      failures++;
      warn(`${cat.label}: ${err.message}`);
    }
  }
  return { events, failures };
}

function messagesFor(fresh) {
  if (fresh.length <= MAX_INDIVIDUAL) {
    return fresh.map((ev) => ({
      title: `${LABEL[ev.category]} · ${ev.source}`,
      body: ev.title,
      tag: ev.key,
      url: ev.url,
    }));
  }
  const top = fresh.slice(0, MAX_INDIVIDUAL).map((ev) => `• ${ev.title}`);
  return [{
    title: `${fresh.length} new events`,
    body: top.join("\n") + `\n…and ${fresh.length - MAX_INDIVIDUAL} more`,
    tag: `summary-${Date.now()}`,
    url: APP_URL,
  }];
}

async function main() {
  const subs = [].concat(JSON.parse(process.env.PUSH_SUBSCRIPTION));
  webpush.setVapidDetails(APP_URL, CFG.VAPID_PUBLIC_KEY, process.env.VAPID_PRIVATE_KEY);

  const now = Date.now();
  const state = loadState();
  const { events, failures } = await collect();
  if (failures === CFG.CATEGORIES.length) {
    warn("Couldn't reach GDELT for any category; nothing sent.");
    return;
  }

  const seen = state ? state.seen : {};
  const fresh = events
    .filter((ev) => !(ev.key in seen) && now - ev.ts < WINDOW_MS)
    .sort((a, b) => b.ts - a.ts);
  for (const ev of events) seen[ev.key] ??= ev.ts;
  for (const [k, ts] of Object.entries(seen)) if (now - ts > KEEP_SEEN_MS) delete seen[k];
  saveState({ seen });

  // On the very first run everything looks new; record it without notifying.
  if (!state) {
    console.log(`First run: recorded ${events.length} existing events without notifying.`);
    return;
  }
  console.log(`${events.length} events fetched, ${fresh.length} new.`);
  if (!fresh.length) return;

  for (const msg of messagesFor(fresh)) {
    for (const sub of subs) {
      try {
        await webpush.sendNotification(sub, JSON.stringify(msg), { TTL: 3600, urgency: "high" });
      } catch (err) {
        if (err.statusCode === 404 || err.statusCode === 410) {
          warn("Push subscription expired. Open the app, tap \"Show push key\" and update the PUSH_SUBSCRIPTION secret.");
        } else {
          warn(`Push failed: ${err.statusCode || ""} ${err.body || err.message}`);
        }
      }
    }
  }
  console.log(`Sent ${messagesFor(fresh).length} notification(s).`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
