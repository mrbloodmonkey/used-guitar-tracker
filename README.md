# Used Guitar Tracker

Tracks pre-owned / second-hand gear from UK retailers in one place, so you don't have
to check each site every day:

| Shop | Source page | How it's read |
|---|---|---|
| guitarguitar | https://www.guitarguitar.co.uk/pre-owned/ | HTML (requests + BeautifulSoup) |
| Richtone Music | https://richtonemusic.co.uk/second-hand/ | HTML, 100 per page, 10 s between requests (robots.txt crawl-delay) |
| Andertons | https://www.andertons.co.uk/browse/second-hand/ | Headless Chromium (Playwright) — the listing is rendered with JavaScript; 10 s between pages |
| Coda Music | https://www.coda-music.com/secondhand.html | HTML |

## How it fits together

```
scraper/scrape.py  ──writes──▶  docs/data/listings.json  ◀──reads──  docs/ (static website)
        ▲
GitHub Action (.github/workflows/scrape.yml) runs it twice a day and commits the JSON.
GitHub Pages serves docs/.
```

* Each item keeps `first_seen`, `last_seen` and a `price_history`, so the site can show
  **NEW** and **PRICE DROP** badges.
* Items that disappear from a shop (sold) are dropped on the next run.
* If one shop fails (site down, layout changed, blocked), its previous items are kept and
  the shop's pill in the header shows ⚠ with the error on hover.

## The website

* Search, filter by type (Electric / Acoustic / Bass / Amps / Pedals), shop, brand, price and age.
* Click ☆ next to a brand to add it to **My brands**, then tick *My brands only*.
* *New since last visit* shows only stock that appeared since you last opened the page.
* Filters and My brands are saved in your browser (localStorage).
* Each card links straight through to the retailer's product page.

## Running locally

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/playwright install chromium
.venv/bin/python scraper/scrape.py                    # all shops (~5 min, mostly polite delays)
.venv/bin/python scraper/scrape.py --sites coda       # one shop
python3 -m http.server -d docs 8000                   # then open http://localhost:8000
```

## GitHub setup (one-off)

1. Push this repo to GitHub.
2. **Settings → Pages**: Source = *Deploy from a branch*, Branch = `main`, folder = `/docs`.
3. **Actions** tab: enable workflows, then run *Scrape used gear* once by hand.

The site is then at `https://<your-username>.github.io/<repo-name>/`.

> Note: some shops may block requests from GitHub's data-centre IPs. If a shop keeps
> showing ⚠ on the site, run the scraper from your own machine instead (e.g. a daily
> `cron`/`launchd` job that runs the scraper and does `git commit && git push`).

## Fixing a broken shop

Shop layouts change. Each shop is one function in `scraper/scrape.py`
(`scrape_guitarguitar`, `scrape_richtone`, `scrape_andertons`, `scrape_coda`) using CSS
selectors — update the selectors and re-run with `--sites <name>`.
