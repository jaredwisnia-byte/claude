// Collects the last WINDOW_HOURS of headlines from the RSS feeds in config.js,
// sorts them into categories, writes OUT_DIR/events.json for the app, and sends
// a Web Push notification for each event not seen in a previous run.
// Run by .github/workflows/collect.yml.
//
// Env:
//   OUT_DIR            where events.json and seen.json live (the previous run's copies are read first)
//   PUSH_SUBSCRIPTION  optional; JSON from the app's "Copy push key" button (one object or an array)
//   VAPID_PRIVATE_KEY  optional; private half of config.js's VAPID_PUBLIC_KEY
//   APP_URL            the deployed app, used as the VAPID subject and summary link
//   FEED_FIXTURE_DIR   optional; read feeds from <dir>/<index>.xml instead of the network (testing)
import fs from "node:fs";
import path from "node:path";
import { XMLParser } from "fast-xml-parser";
import webpush from "web-push";
import "../config.js";

const CFG = globalThis.MW_CONFIG;
const OUT_DIR = process.env.OUT_DIR || "out";
const APP_URL = process.env.APP_URL || "https://example.com/";
const WINDOW_MS = CFG.WINDOW_HOURS * 60 * 60 * 1000;
const KEEP_SEEN_MS = 24 * 60 * 60 * 1000;
const MAX_EVENTS = 200;
const MAX_INDIVIDUAL = 3;
const LABEL = Object.fromEntries(CFG.CATEGORIES.map((c) => [c.id, c.label]));

const warn = (msg) => console.log(`::warning::${msg}`);
const parser = new XMLParser({ ignoreAttributes: false, attributeNamePrefix: "@", textNodeName: "#text" });

const text = (v) => {
  if (v == null) return "";
  if (typeof v === "object") return text(v["#text"] ?? v["@href"] ?? "");
  return String(v);
};
const clean = (s) =>
  text(s)
    .replace(/<[^>]*>/g, " ")
    .replace(/&nbsp;/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&quot;/g, '"')
    .replace(/&apos;/g, "'")
    .replace(/&#x([0-9a-f]+);/gi, (_, h) => String.fromCodePoint(parseInt(h, 16)))
    .replace(/&#(\d+);/g, (_, d) => String.fromCodePoint(+d))
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/\s+/g, " ")
    .trim();
const list = (v) => (v == null ? [] : Array.isArray(v) ? v : [v]);

function linkOf(item) {
  for (const l of list(item.link)) {
    if (typeof l === "string" && l.startsWith("http")) return l.trim();
    if (l && typeof l === "object" && (!l["@rel"] || l["@rel"] === "alternate") && l["@href"]) return l["@href"];
  }
  const guid = text(item.guid);
  return guid.startsWith("http") ? guid : "";
}

async function readFeed(feed, i) {
  if (process.env.FEED_FIXTURE_DIR) {
    const f = path.join(process.env.FEED_FIXTURE_DIR, `${i}.xml`);
    return fs.existsSync(f) ? fs.readFileSync(f, "utf8") : "";
  }
  const res = await fetch(feed.url, {
    headers: { "user-agent": "MidnightWatch/1.0 (+https://github.com/jaredwisnia-byte/claude)" },
    signal: AbortSignal.timeout(15000),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.text();
}

function itemsOf(xml) {
  const doc = parser.parse(xml);
  return [
    ...list(doc.rss?.channel?.item),
    ...list(doc.feed?.entry),
    ...list(doc["rdf:RDF"]?.item),
  ];
}

async function collect(now) {
  const results = await Promise.allSettled(CFG.FEEDS.map(readFeed));
  const events = [];
  const keys = new Set();
  let ok = 0;
  results.forEach((r, i) => {
    const feed = CFG.FEEDS[i];
    if (r.status === "rejected") return warn(`${feed.url}: ${r.reason.message}`);
    let items;
    try {
      items = itemsOf(r.value);
    } catch (err) {
      return warn(`${feed.url}: unparseable (${err.message})`);
    }
    if (!items.length) return warn(`${feed.url}: no items`);
    ok++;
    for (const it of items) {
      const title = clean(it.title);
      const url = linkOf(it);
      const ts = Date.parse(text(it.pubDate || it.published || it.updated || it["dc:date"]));
      if (!title || !url || !Number.isFinite(ts)) continue;
      if (now - ts >= WINDOW_MS || ts > now + 5 * 60e3) continue;
      const key = CFG.titleKey(title);
      if (keys.has(key)) continue;
      const category = CFG.categorize(title, clean(it.description || it.summary), feed.category);
      if (!category) continue;
      keys.add(key);
      events.push({ key, title, url, source: feed.source, ts, category });
    }
  });
  events.sort((a, b) => b.ts - a.ts);
  return { events: events.slice(0, MAX_EVENTS), feedsOk: ok };
}

function readJson(file, fallback) {
  try {
    return JSON.parse(fs.readFileSync(path.join(OUT_DIR, file), "utf8"));
  } catch {
    return fallback;
  }
}

function messagesFor(fresh) {
  if (fresh.length <= MAX_INDIVIDUAL) {
    return fresh.map((ev) => ({ title: `${LABEL[ev.category]} · ${ev.source}`, body: ev.title, tag: ev.key, url: ev.url }));
  }
  const top = fresh.slice(0, MAX_INDIVIDUAL).map((ev) => `• ${ev.title}`);
  return [{
    title: `${fresh.length} new events`,
    body: `${top.join("\n")}\n…and ${fresh.length - MAX_INDIVIDUAL} more`,
    tag: `summary-${Date.now()}`,
    url: APP_URL,
  }];
}

async function sendPushes(fresh) {
  if (!process.env.PUSH_SUBSCRIPTION || !process.env.VAPID_PRIVATE_KEY) {
    console.log("Push secrets not set; skipping notifications.");
    return;
  }
  const subs = [].concat(JSON.parse(process.env.PUSH_SUBSCRIPTION));
  webpush.setVapidDetails(APP_URL, CFG.VAPID_PUBLIC_KEY, process.env.VAPID_PRIVATE_KEY);
  const msgs = messagesFor(fresh);
  for (const msg of msgs) {
    for (const sub of subs) {
      try {
        await webpush.sendNotification(sub, JSON.stringify(msg), { TTL: 3600, urgency: "high" });
      } catch (err) {
        if (err.statusCode === 404 || err.statusCode === 410) {
          warn('Push subscription expired. Open the app, tap "Show push key" and update the PUSH_SUBSCRIPTION secret.');
        } else {
          warn(`Push failed: ${err.statusCode || ""} ${err.body || err.message}`);
        }
      }
    }
  }
  console.log(`Sent ${msgs.length} notification(s).`);
}

async function main() {
  const now = Date.now();
  const prevSeen = readJson("seen.json", null);
  const { events, feedsOk } = await collect(now);
  console.log(`${feedsOk}/${CFG.FEEDS.length} feeds read, ${events.length} events in the last ${CFG.WINDOW_HOURS}h.`);
  if (!feedsOk) throw new Error("No feeds could be read; leaving the previous events.json in place.");

  const seen = prevSeen || {};
  const fresh = prevSeen ? events.filter((ev) => !(ev.key in seen)) : [];
  for (const ev of events) seen[ev.key] ??= ev.ts;
  for (const [k, ts] of Object.entries(seen)) if (now - ts > KEEP_SEEN_MS) delete seen[k];

  fs.mkdirSync(OUT_DIR, { recursive: true });
  fs.writeFileSync(path.join(OUT_DIR, "events.json"), JSON.stringify({ updated: now, events }));
  fs.writeFileSync(path.join(OUT_DIR, "seen.json"), JSON.stringify(seen));

  if (!prevSeen) {
    console.log("First run: recorded existing events without notifying.");
    return;
  }
  console.log(`${fresh.length} new since the last run.`);
  if (fresh.length) await sendPushes(fresh);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
