# Midnight Watch

A 24-hour countdown clock in the style of the Bulletin of the Atomic Scientists' Doomsday Clock. It counts down to midnight and puts a red marker on the dial at the time of each geopolitically significant event. Every event also goes into a ledger below the clock with its source and timestamp.

- **Dial:** the full face is 24 hours, with midnight at the top. The hand shows the current time, and the shaded arc is the time left until midnight.
- **Markers:** a solid red pip is an event since midnight. A hollow pip is an event from the previous 24 hours. Tap a marker to jump to its entry in the ledger.
- **Events:** the app pulls English-language headlines from major outlets (Reuters, AP, BBC, Al Jazeera, the Guardian and others) that match geopolitical keywords such as sanctions, missile, coup and ceasefire. They come from the [GDELT](https://www.gdeltproject.org/) news index, which is checked every 5 minutes. Syndicated duplicates are merged, and events older than 24 hours are dropped.

Edit `KEYWORDS` and `OUTLETS` at the top of `app.js` to change what counts as an event.

## Install on iPhone

1. Host the repo with GitHub Pages: **Settings → Pages → Build and deployment → Source: Deploy from a branch**, then pick `master` and `/ (root)`.
2. On your iPhone, open the Pages URL (`https://<user>.github.io/<repo>/`) in Safari.
3. Tap **Share → Add to Home Screen**. The app then opens full-screen from its own icon.

## Run locally

```sh
python3 -m http.server 8000
# open http://localhost:8000
```

This is a static web app with no build step and no dependencies.
