// Shared by the web app (app.js) and the collector (scripts/collect.mjs).
(function (root) {
  "use strict";

  // Order matters: ties go to the category listed first.
  const CATEGORIES = [
    {
      id: "geopolitics",
      label: "Geopolitics",
      keywords: [
        "sanction", "sanctions", "missile", "missiles", "invasion", "invade", "airstrike", "airstrikes",
        "coup", "ceasefire", "truce", "troops", "peace talks", "annexation", "annex", "blockade", "nato",
        "security council", "nuclear weapon", "nuclear weapons", "warplane", "drone strike", "drone attack",
        "military", "border clash", "hostage", "hostages", "insurgents", "rebels", "war", "diplomat",
        "diplomats", "embassy", "un general assembly", "g7", "g20", "summit", "treaty",
      ],
    },
    {
      id: "politics",
      label: "Politics",
      keywords: [
        "election", "elections", "parliament", "congress", "senate", "prime minister", "white house",
        "legislation", "referendum", "impeachment", "supreme court", "cabinet", "president", "governor",
        "lawmakers", "vote", "voters", "campaign", "republicans", "democrats", "opposition leader", "bill",
      ],
    },
    {
      id: "economics",
      label: "Economics",
      keywords: [
        "inflation", "recession", "unemployment", "tariff", "tariffs", "central bank", "interest rate",
        "interest rates", "trade deficit", "jobs report", "economic growth", "consumer prices", "gdp",
        "economy", "economic", "trade war", "imf", "world bank", "budget deficit", "wages",
      ],
    },
    {
      id: "finance",
      label: "Finance",
      keywords: [
        "stock market", "stocks", "shares", "wall street", "federal reserve", "the fed", "bond yields",
        "treasury yields", "earnings", "bitcoin", "crypto", "nasdaq", "s&p 500", "dow", "merger",
        "acquisition", "bankruptcy", "ipo", "investors", "hedge fund", "banks", "oil prices",
      ],
    },
    {
      id: "science",
      label: "Science",
      keywords: [
        "scientists", "researchers", "nasa", "telescope", "discovery", "clinical trial", "physics",
        "genome", "climate", "vaccine", "astronomers", "species", "fossil", "study finds", "experiment",
        "esa", "mars", "moon", "asteroid", "virus", "biology",
      ],
    },
    {
      id: "engineering",
      label: "Engineering",
      keywords: [
        "engineering", "engineers", "semiconductor", "semiconductors", "chip", "chips", "chipmaker",
        "artificial intelligence", "ai", "rocket", "robot", "robotics", "infrastructure", "nuclear reactor",
        "spacex", "battery", "batteries", "satellite", "satellites", "electric vehicle", "evs", "quantum",
        "data center", "power grid", "bridge", "software",
      ],
    },
  ];

  // `category` is the fallback for items that match no keywords; feeds without
  // one only contribute items that match a category's keywords.
  const FEEDS = [
    { url: "https://feeds.bbci.co.uk/news/world/rss.xml", source: "bbc.co.uk" },
    { url: "https://feeds.bbci.co.uk/news/politics/rss.xml", source: "bbc.co.uk", category: "politics" },
    { url: "https://feeds.bbci.co.uk/news/business/rss.xml", source: "bbc.co.uk", category: "economics" },
    { url: "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml", source: "bbc.co.uk", category: "science" },
    { url: "https://feeds.bbci.co.uk/news/technology/rss.xml", source: "bbc.co.uk", category: "engineering" },
    { url: "https://rss.nytimes.com/services/xml/rss/nyt/World.xml", source: "nytimes.com" },
    { url: "https://rss.nytimes.com/services/xml/rss/nyt/Politics.xml", source: "nytimes.com", category: "politics" },
    { url: "https://rss.nytimes.com/services/xml/rss/nyt/Economy.xml", source: "nytimes.com", category: "economics" },
    { url: "https://rss.nytimes.com/services/xml/rss/nyt/Science.xml", source: "nytimes.com", category: "science" },
    { url: "https://www.theguardian.com/world/rss", source: "theguardian.com" },
    { url: "https://www.theguardian.com/science/rss", source: "theguardian.com", category: "science" },
    { url: "https://www.theguardian.com/business/economics/rss", source: "theguardian.com", category: "economics" },
    { url: "https://feeds.npr.org/1004/rss.xml", source: "npr.org" },
    { url: "https://feeds.npr.org/1014/rss.xml", source: "npr.org", category: "politics" },
    { url: "https://www.aljazeera.com/xml/rss/all.xml", source: "aljazeera.com" },
    { url: "https://www.france24.com/en/rss", source: "france24.com" },
    { url: "https://www.cnbc.com/id/10000664/device/rss/rss.html", source: "cnbc.com", category: "finance" },
    { url: "https://www.cnbc.com/id/20910258/device/rss/rss.html", source: "cnbc.com", category: "economics" },
    { url: "https://feeds.content.dowjones.io/public/rss/mw_topstories", source: "marketwatch.com", category: "finance" },
    { url: "https://feeds.arstechnica.com/arstechnica/index", source: "arstechnica.com" },
    { url: "https://spectrum.ieee.org/feeds/feed.rss", source: "spectrum.ieee.org", category: "engineering" },
  ];

  const escapeRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const MATCHERS = CATEGORIES.map((c) => ({
    id: c.id,
    re: new RegExp(`(?:^|[^a-z0-9])(?:${c.keywords.map(escapeRe).join("|")})(?![a-z0-9])`, "g"),
  }));

  // Picks the category whose keywords appear most often in the headline and
  // summary (headline hits count double), or the feed's fallback category.
  function categorize(title, summary, fallback) {
    const t = ` ${title.toLowerCase()} `;
    const s = ` ${(summary || "").toLowerCase()} `;
    let best = null;
    let bestScore = 0;
    for (const m of MATCHERS) {
      const score = 2 * (t.match(m.re) || []).length + (s.match(m.re) || []).length;
      if (score > bestScore) {
        best = m.id;
        bestScore = score;
      }
    }
    return best || fallback || null;
  }

  // Outlets syndicate the same story under many URLs; key on the headline.
  const titleKey = (t) => t.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim().slice(0, 70);

  root.MW_CONFIG = {
    WINDOW_HOURS: 8,
    POLL_SECONDS: 60,
    // Published by .github/workflows/collect.yml to the repo's `data` branch.
    DATA_URL: "https://raw.githubusercontent.com/jaredwisnia-byte/claude/data/events.json",
    VAPID_PUBLIC_KEY: "BKUjaNUpIasNa9EHbWuH-VHjBwNi-dvviAyYJsbg_oPSRJ08poLzABY1pFs0zbLyStF0FpLi9YiAYdiCHltmVMw",
    CATEGORIES,
    FEEDS,
    categorize,
    titleKey,
  };
})(typeof self !== "undefined" ? self : globalThis);
