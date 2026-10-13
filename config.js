// Shared by the web app (app.js) and the push notifier (scripts/notify.mjs).
(function (root) {
  "use strict";

  const NEWS_OUTLETS = [
    "reuters.com", "apnews.com", "bbc.com", "bbc.co.uk", "aljazeera.com",
    "theguardian.com", "nytimes.com", "npr.org", "cnn.com", "washingtonpost.com",
    "france24.com", "dw.com", "politico.com", "axios.com",
  ];

  // Order matters: when a headline matches several categories, the first wins.
  const CATEGORIES = [
    {
      id: "geopolitics",
      label: "Geopolitics",
      keywords: [
        "sanctions", "missile", "invasion", "airstrike", "coup", "ceasefire", "troops",
        '"peace talks"', "annexation", "blockade", "NATO", '"security council"', '"nuclear weapons"',
      ],
      outlets: [],
    },
    {
      id: "politics",
      label: "Politics",
      keywords: [
        "election", "parliament", "congress", "senate", '"prime minister"', '"white house"',
        "legislation", "referendum", "impeachment", '"supreme court"', '"cabinet minister"',
      ],
      outlets: [],
    },
    {
      id: "economics",
      label: "Economics",
      keywords: [
        "inflation", "recession", "unemployment", "tariffs", '"central bank"', '"interest rates"',
        '"trade deficit"', '"jobs report"', '"economic growth"', '"consumer prices"',
      ],
      outlets: ["economist.com", "bloomberg.com", "wsj.com", "ft.com"],
    },
    {
      id: "finance",
      label: "Finance",
      keywords: [
        '"stock market"', '"wall street"', '"federal reserve"', '"bond yields"', "earnings",
        "bitcoin", "nasdaq", '"s&p 500"', "merger", "acquisition", "bankruptcy",
      ],
      outlets: ["bloomberg.com", "wsj.com", "ft.com", "cnbc.com", "marketwatch.com"],
    },
    {
      id: "science",
      label: "Science",
      keywords: [
        "scientists", "researchers", "NASA", "telescope", "discovery", '"clinical trial"',
        "physics", "genome", "climate", "vaccine", "astronomers",
      ],
      outlets: ["nature.com", "science.org", "scientificamerican.com", "newscientist.com", "sciencedaily.com"],
    },
    {
      id: "engineering",
      label: "Engineering",
      keywords: [
        "engineering", "semiconductor", '"artificial intelligence"', "rocket", "robotics",
        "infrastructure", '"nuclear reactor"', "SpaceX", "battery", "chipmaker", "satellite",
      ],
      outlets: ["arstechnica.com", "technologyreview.com", "spectrum.ieee.org", "theverge.com", "wired.com"],
    },
  ];

  function gdeltUrl(category, hours) {
    const outlets = [...new Set([...NEWS_OUTLETS, ...category.outlets])];
    const query =
      `(${category.keywords.join(" OR ")}) ` +
      `(${outlets.map((d) => "domainis:" + d).join(" OR ")}) sourcelang:english`;
    return (
      "https://api.gdeltproject.org/api/v2/doc/doc?" +
      new URLSearchParams({
        query,
        mode: "artlist",
        format: "json",
        timespan: `${hours}h`,
        maxrecords: "30",
        sort: "hybridrel",
      })
    );
  }

  function parseSeenDate(s) {
    const m = /^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z$/.exec(s || "");
    if (!m) return NaN;
    return Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6]);
  }

  // Outlets syndicate the same story under many URLs; key on the headline.
  const titleKey = (t) => t.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim().slice(0, 70);

  root.MW_CONFIG = {
    WINDOW_HOURS: 8,
    POLL_SECONDS: 60,
    // GDELT allows one request every 5 seconds per client.
    REQUEST_GAP_MS: 5500,
    VAPID_PUBLIC_KEY: "BKUjaNUpIasNa9EHbWuH-VHjBwNi-dvviAyYJsbg_oPSRJ08poLzABY1pFs0zbLyStF0FpLi9YiAYdiCHltmVMw",
    CATEGORIES,
    gdeltUrl,
    parseSeenDate,
    titleKey,
  };
})(typeof self !== "undefined" ? self : globalThis);
