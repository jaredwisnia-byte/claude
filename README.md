# Midnight Watch

A 24-hour countdown clock in the style of the Bulletin of the Atomic Scientists' Doomsday Clock. It counts down to midnight and puts a red marker on the dial at the time of each geopolitically significant event. Every event also goes into a ledger below the clock with its source and timestamp.

- **Dial:** the full face is 24 hours, with midnight at the top. The hand shows the current time, and the shaded arc is the time left until midnight.
- **Markers:** a solid red pip is an event since midnight. A hollow pip is an event from before midnight. Tap a marker to jump to its entry in the ledger.
- **Events:** six categories: Politics, Science, Economics, Finance, Engineering and Geopolitics. Each one pulls headlines from major outlets that match its keywords, using the [GDELT](https://www.gdeltproject.org/) news index. On open, the app loads the last 8 hours and then checks again every minute. Syndicated duplicates are merged, and anything older than 8 hours is dropped. Filter chips limit the list and dial to a single category.
- **Notifications:** while the app is open, new events trigger a notification. While it's closed, a scheduled GitHub Actions job (`.github/workflows/notify.yml`) checks every 5 minutes and sends Web Push alerts. That's GitHub's shortest schedule, and runs can be late.

Categories, keywords and outlets live in `config.js`.

## Install on iPhone

1. Host the repo with GitHub Pages: **Settings → Pages → Build and deployment → Source: Deploy from a branch**, then pick `master` and `/ (root)`.
2. On your iPhone, open the Pages URL (`https://<user>.github.io/<repo>/`) in Safari.
3. Tap **Share → Add to Home Screen**. The app then opens full-screen from its own icon.

## Notifications while the app is closed

1. Open the app from your Home Screen, tap **Enable notifications**, and allow them.
2. Tap **Copy push key**.
3. In the repo, go to **Settings → Secrets and variables → Actions** and add two repository secrets:
   - `PUSH_SUBSCRIPTION`: the push key you just copied.
   - `VAPID_PRIVATE_KEY`: the private signing key that pairs with `VAPID_PUBLIC_KEY` in `config.js`.
4. **Actions → Push notifications → Run workflow** starts it right away. The first run only records existing events; later runs notify you about new ones. If a run finds more than 3 new events, they arrive as a single summary.

GitHub pauses scheduled workflows after 60 days without repo activity. Re-enable it from the Actions tab if that happens.

## Run locally

```sh
python3 -m http.server 8000
# open http://localhost:8000
```

This is a static web app with no build step and no dependencies.
