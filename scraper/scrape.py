"""
Pre-owned guitar gear scraper.

Collects used/second-hand listings from UK retailers and writes them to
docs/data/listings.json, which the static website in docs/ reads.

Usage:
    python scraper/scrape.py                 # scrape all sites
    python scraper/scrape.py --sites coda    # scrape just one (comma-separated)
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = ROOT / "docs" / "data" / "listings.json"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
MAX_PAGES = 100  # safety cap per site (Richtone keeps sold items listed, so it needs ~60+ pages)

log = logging.getLogger("scraper")


@dataclass
class Item:
    site: str
    id: str
    title: str
    url: str
    price: float | None
    brand: str = ""
    image: str = ""
    category: str = ""
    in_stock: bool = True
    first_seen: str = ""
    last_seen: str = ""
    price_history: list = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.site}:{self.id}"


# --------------------------------------------------------------------------- helpers

session = requests.Session()
session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en-GB,en;q=0.9"})


def get_soup(url: str, delay: float) -> BeautifulSoup:
    time.sleep(delay)
    log.info("GET %s", url)
    r = session.get(url, timeout=30)
    r.raise_for_status()
    return BeautifulSoup(r.text, "lxml")


def parse_price(text: str | None) -> float | None:
    if not text:
        return None
    m = re.search(r"\d[\d,]*(?:\.\d+)?", text.replace("£", ""))
    return float(m.group(0).replace(",", "")) if m else None


def clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


SUFFIX_RE = re.compile(
    r"\s*[~\-–(]*\s*(pre-?owned|second\s*hand|secondhand|2nd\s*hand|used)\s*\)?\s*$", re.I
)
PREFIX_RE = re.compile(r"^(second\s*hand|pre-?owned|used)\s+", re.I)


def tidy_title(title: str) -> str:
    title = clean(title)
    title = re.sub(r"\s*\((pre-?owned|used|second\s*hand)\)", "", title, flags=re.I)
    title = re.sub(r"\s*-\s*2nd hand(\s*\(\d+\))?", "", title, flags=re.I)
    title = SUFFIX_RE.sub("", title)
    title = PREFIX_RE.sub("", title)
    return title.strip(" -~")


# Simple keyword-based categoriser. Order matters: first match wins.
CATEGORY_RULES = [
    ("Bass", r"\bbass\b(?!\s*(amp|cab|head|combo|pedal|drum|preamp|di\b))|\bprecision\b|\bp-?bass\b|\bthunderbird\b|\bstingray\b|\bwarwick\b|\bspector\b|\bsadowsky\b"),
    ("Pedals & Effects", r"pedal|overdrive|distortion|fuzz|delay|chorus|phaser|flanger|\bwah\b|crybaby|compressor|looper|loop station|\btuner\b|boost|multi[- ]?(fx|effect)|effects processor|helix|\bhx\b|kemper|quad cortex|axe-?fx|headrush|tremolo pedal|octave|\bclone\b|big muff|footswitch|foot controller|envelope filter|\bstrymon\b|eventide|\bfx unit|\bgt-\d"),
    ("Amps & Cabs", r"\bamp\b|amplifier|combo|\bhead\b|\bcab\b|cabinet|\b[0-9]x1[02]\b|deluxe reverb|twin reverb|ac30|ac15|jcm|jmp|plexi|bassman|princeton|blues junior|hot rod|katana|nextone|\bcelestion\b"),
    ("Acoustic", r"acoustic|dreadnought|\bj-?45\b|\bhd-?28\b|\bd-?28\b|\bd-?18\b|\b0{1,3}-?\d{2}|\bom-|grand auditorium|grand concert|parlou?r|classical|nylon|\bukulele\b|taylor \d{3}|\bgs mini\b|\bmartin\b|jumbo|lowden|resonator|dobro|la mancha|alhambra|furch|\bbrook\b|atkin|collings|\bmaton\b"),
    ("Electric", r"guitar|strat|tele|les paul|\bsg\b|\bes-?3\d\d|\b335\b|jazzmaster|jaguar|mustang|cabronita|firebird|explorer|flying v|\bprs\b|custom 2[24]|silver sky|\bltd\b|ibanez|gretsch|rickenbacker|esquire|semi-?hollow|hollow-?body|superstrat|revstar|pacifica|hagstrom|music ?man|chapman|mayones|schecter|jackson|charvel|\besp\b|suhr|\blsl\b|godin|\bduesenberg\b|\bepiphone\b|\bsquier\b|\bgibson\b|\bfender\b"),
]


def categorise(*texts: str) -> str:
    blob = " ".join(t for t in texts if t).lower()
    for name, pattern in CATEGORY_RULES:
        if re.search(pattern, blob):
            return name
    return "Other"


KNOWN_BRANDS: set[str] = set()  # filled from sites that expose brand, used for Coda


def guess_brand(title: str) -> str:
    low = title.lower()
    best = ""
    for b in KNOWN_BRANDS:
        if low.startswith(b.lower() + " ") and len(b) > len(best):
            best = b
    if best:
        return best
    first = title.split(" ")[0] if title else ""
    return first if first and not first[0].isdigit() else ""


# --------------------------------------------------------------------------- sites


def scrape_guitarguitar() -> list[Item]:
    items: list[Item] = []
    seen: set[str] = set()
    base = "https://www.guitarguitar.co.uk"
    sections = {
        "guitars/electric": "Electric",
        "guitars/acoustic": "Acoustic",
        "guitars/bass": "Bass",
        "amps": "Amps & Cabs",
        "pedals": "Pedals & Effects",
    }
    for section, category in sections.items():
        url: str | None = f"{base}/pre-owned/{section}/"
        pages = 0
        while url and pages < MAX_PAGES:
            soup = get_soup(url, delay=2)
            pages += 1
            for card in soup.select("a.product[href*='/product/']"):
                href = urljoin(base, card["href"])
                m = re.search(r"/product/([^-/]+)--", href)
                pid = m.group(1) if m else href
                h3 = card.select_one("h3")
                if not h3 or pid in seen:
                    continue
                seen.add(pid)
                brand = clean(h3.strong.get_text()) if h3.strong else ""
                title = tidy_title(card.get("title") or h3.get_text())
                img = card.select_one("img")
                image = (img.get("data-src") or img.get("src") or "") if img else ""
                pounds = card.select_one(".js-pounds")
                pennies = card.select_one(".js-pennies")
                price = parse_price((pounds.get_text() if pounds else "") + (pennies.get_text() if pennies else ""))
                avail = clean(card.select_one(".availability").get_text()) if card.select_one(".availability") else ""
                items.append(Item(
                    site="guitarguitar", id=pid, title=title, url=href,
                    price=price, brand=brand, image=image.replace("/Content/Images/blank.png", ""),
                    category=category, in_stock="out of stock" not in avail.lower(),
                ))
            nxt = soup.select_one("a.next-page-button[href]")
            url = urljoin(base, nxt["href"]) if nxt else None
    return items


def scrape_richtone() -> list[Item]:
    # robots.txt asks for a 10s crawl delay; we fetch 100 items per page to keep request count low.
    items: list[Item] = []
    seen: set[str] = set()
    for page in range(1, MAX_PAGES + 1):
        try:
            soup = get_soup(f"https://richtonemusic.co.uk/second-hand/?limit=100&sort=newest&page={page}", delay=10)
        except requests.HTTPError:
            if page == 1:
                raise
            log.warning("richtone page %d failed; keeping the %d items found so far", page, len(items))
            break
        cards = soup.select("article.card[data-entity-id]")
        new = 0
        before = sum(i.in_stock for i in items)
        for card in cards:
            pid = card["data-entity-id"]
            if pid in seen:
                continue
            seen.add(pid)
            new += 1
            link = card.select_one("a.card-figure__link, .card-title a")
            img = card.select_one("img.card-image")
            image = ""
            if img:
                srcset = img.get("data-srcset") or ""
                m = re.search(r"(\S+)\s+640w", srcset)
                image = m.group(1) if m else (img.get("data-src") or img.get("src") or "")
            price_el = card.select_one("[data-product-price-with-tax], .price--withTax, .price--main")
            price = parse_price(price_el.get_text() if price_el else card.get("data-product-price"))
            src_cat = card.get("data-product-category", "")
            title = tidy_title(card.get("data-name", ""))
            brand = clean(card.get("data-product-brand"))
            items.append(Item(
                site="richtone", id=pid, title=title, url=link["href"] if link else "",
                price=price, brand=brand if brand and brand.lower() != "other" else guess_brand(title), image=image,
                category=categorise(src_cat.split(",")[0], title),
                in_stock="out of stock" not in card.get_text(" ").lower(),
            ))
        # Sorted newest first, and sold items stay listed as "Out of Stock" — stop once a whole page is sold.
        if len(cards) < 100 or new == 0 or sum(i.in_stock for i in items) == before:
            break
    return items


def scrape_coda() -> list[Item]:
    items: list[Item] = []
    seen: set[str] = set()
    for page in range(1, MAX_PAGES + 1):
        soup = get_soup(f"https://www.coda-music.com/secondhand.html?product_list_limit=96&p={page}", delay=3)
        cards = soup.select("li.product-item")
        new = 0
        for card in cards:
            box = card.select_one("[data-product-id]")
            link = card.select_one("a.product-item-link")
            if not box or not link:
                continue
            pid = box["data-product-id"]
            if pid in seen:
                continue
            seen.add(pid)
            new += 1
            title = tidy_title(link.get_text())
            amount = card.select_one("[data-price-type='finalPrice']")
            price = float(amount["data-price-amount"]) if amount and amount.get("data-price-amount") else None
            img = card.select_one("img.product-image-photo")
            items.append(Item(
                site="coda", id=pid, title=title, url=link["href"], price=price,
                brand=guess_brand(title), image=img.get("src", "") if img else "",
                category=categorise(title),
                in_stock="out of stock" not in card.get_text(" ").lower(),
            ))
        # Magento serves the last page again for out-of-range page numbers
        if new == 0 or not soup.select_one("a.action.next"):
            break
    return items


def scrape_andertons() -> list[Item]:
    # Andertons renders product cards with JavaScript, so load the public page in a headless browser.
    from playwright.sync_api import sync_playwright

    base = "https://www.andertons.co.uk"
    # Only the guitar-related sections (the full second-hand list also has drums, keys, PA...).
    sections = {
        "second-hand-electric-guitars": "Electric",
        "second-hand-acoustic-guitars": "Acoustic",
        "second-hand-bass-guitars": "Bass",
        "second-hand-amps": "Amps & Cabs",
        "second-hand-guitar-pedals": "Pedals & Effects",
    }
    items: list[Item] = []
    seen: set[str] = set()
    first = True
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(user_agent=USER_AGENT, locale="en-GB")
        for section, category in sections.items():
            for n in range(1, MAX_PAGES + 1):
                if not first:
                    time.sleep(10)  # robots.txt crawl-delay
                first = False
                url = f"{base}/browse/second-hand/{section}/?sort_by=__cnstrc_release_time&sort_order=descending&page={n}"
                log.info("GET %s (browser)", url)
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                try:
                    # Wait for the main product grid (the carousel renders earlier and doesn't count).
                    page.wait_for_function(
                        """() => [...document.querySelectorAll("article[data-testid='product-card']")]
                                 .some(a => !a.closest('.slick-list, .slick-track, .slick-slider'))""",
                        timeout=30000)
                except Exception:
                    log.warning("no product cards on %s", url)
                    break
                soup = BeautifulSoup(page.content(), "lxml")
                # Skip the recommendation carousel (.slick-list), which shows items from other sections.
                cards = [c for c in soup.select("article[data-testid='product-card']")
                         if not c.find_parent(class_=re.compile(r"slick-(list|track|slider)"))]
                new = 0
                for card in cards:
                    pid = card.get("data-product-id", "")
                    link = card.select_one("a.productCard__title__link")
                    if not pid or not link or pid in seen:
                        continue
                    seen.add(pid)
                    new += 1
                    title = tidy_title(link.get_text())
                    brand_el = card.select_one(".productCard__brand")
                    brand = clean(brand_el.get_text()) if brand_el else ""
                    img = card.select_one("img.productCard__image__img")
                    price_el = card.select_one(".productCard__price")
                    stock_el = card.select_one(".stockStatus")
                    items.append(Item(
                        site="andertons", id=pid, title=title, url=urljoin(base, link["href"]),
                        price=parse_price(price_el.get_text() if price_el else None),
                        brand=brand if brand and brand.lower() != "other" else guess_brand(title),
                        image=img.get("src", "") if img else "", category=category,
                        in_stock=not stock_el or "out" not in stock_el.get_text().lower(),
                    ))
                if new == 0 or len(cards) < 48:
                    break
        browser.close()
    return items


SITES: dict[str, Callable[[], list[Item]]] = {
    "guitarguitar": scrape_guitarguitar,
    "richtone": scrape_richtone,
    "andertons": scrape_andertons,
    "coda": scrape_coda,  # last, so it can use brand names learned from the other sites
}

SITE_INFO = {
    "guitarguitar": {"name": "guitarguitar", "url": "https://www.guitarguitar.co.uk/pre-owned/"},
    "richtone": {"name": "Richtone Music", "url": "https://richtonemusic.co.uk/second-hand/"},
    "andertons": {"name": "Andertons", "url": "https://www.andertons.co.uk/browse/second-hand/"},
    "coda": {"name": "Coda Music", "url": "https://www.coda-music.com/secondhand.html"},
}


# --------------------------------------------------------------------------- merge & save


def load_previous() -> dict:
    if DATA_FILE.exists():
        return json.loads(DATA_FILE.read_text())
    return {"items": [], "sites": {}}


def merge(previous: dict, scraped: dict[str, list[Item]], failed: set[str], now: str) -> dict:
    prev_items = {f"{i['site']}:{i['id']}": i for i in previous.get("items", [])}
    out: list[dict] = []

    for site, items in scraped.items():
        for item in items:
            if not item.in_stock:  # sold / unavailable — nothing to buy
                continue
            old = prev_items.get(item.key)
            item.first_seen = old["first_seen"] if old else now
            item.last_seen = now
            item.price_history = old.get("price_history", []) if old else []
            if item.price is not None and (not item.price_history or item.price_history[-1][1] != item.price):
                item.price_history.append([now, item.price])
            out.append(asdict(item))

    # Keep previous data for sites that failed this run, so a temporary error doesn't wipe them.
    for key, old in prev_items.items():
        if old["site"] in failed:
            out.append(old)

    sites = previous.get("sites", {})
    for site in SITES:
        info = dict(SITE_INFO[site])
        info["count"] = sum(1 for i in out if i["site"] == site)
        info["last_success"] = now if site in scraped else sites.get(site, {}).get("last_success")
        info["last_error"] = sites.get(site, {}).get("last_error") if site in failed else None
        sites[site] = info

    # Shops spell brands differently ("BOSS" / "Boss"): use the most common spelling for each.
    spellings: dict[str, Counter] = {}
    for i in out:
        spellings.setdefault(i["brand"].lower(), Counter())[i["brand"]] += 1
    for i in out:
        i["brand"] = spellings[i["brand"].lower()].most_common(1)[0][0]

    out.sort(key=lambda i: i["first_seen"], reverse=True)
    return {"generated_at": now, "sites": sites, "items": out}


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sites", default=",".join(SITES), help="comma-separated: " + ",".join(SITES))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    wanted = [s.strip() for s in args.sites.split(",") if s.strip()]
    previous = load_previous()
    for i in previous.get("items", []):
        if i.get("brand") and i["site"] != "coda":
            KNOWN_BRANDS.add(i["brand"])

    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    scraped: dict[str, list[Item]] = {}
    failed: set[str] = set(SITES) - set(wanted)  # sites not requested keep their old data
    errors: dict[str, str] = {}

    for site in SITES:
        if site not in wanted:
            continue
        try:
            items = SITES[site]()
            if not items:
                raise RuntimeError("no items found (page layout may have changed)")
            scraped[site] = items
            KNOWN_BRANDS.update(i.brand for i in items if i.brand and site != "coda")
            log.info("%s: %d items", site, len(items))
        except Exception as exc:  # keep going with the other sites
            log.exception("%s failed", site)
            failed.add(site)
            errors[site] = f"{now}: {exc}"

    data = merge(previous, scraped, failed, now)
    for site, err in errors.items():
        data["sites"][site]["last_error"] = err

    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(data, indent=1, ensure_ascii=False))
    log.info("wrote %d items to %s", len(data["items"]), DATA_FILE)
    return 1 if errors and not scraped else 0


if __name__ == "__main__":
    sys.exit(main())
