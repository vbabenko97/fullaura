"""Real products from shops' own sites, read when a person asks for them.

Addresses come from each shop's sitemap and facts from the structured data on its product page, both
only where the shop's robots.txt allows this user agent. A model never supplies an address that code
has not listed, so it cannot name a product that does not exist. Addresses and facts stay in process
memory; nothing is written to disk.
"""

from __future__ import annotations

import gzip
import json
import logging
import re
import threading
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Literal
from urllib.robotparser import RobotFileParser

import httpx2

logger = logging.getLogger(__name__)

AGENT = "FullAura/0.1 (closed beta; reads a product page when a person asks for it)"
INDEX_TTL_S = 24 * 60 * 60
PAGE_TTL_S = 60 * 60
RETRY_FAILED_SHOP_S = 10 * 60
MAX_SITEMAP_FILES = 12
PAGES_PER_SHOP = 6
READERS = 4
MAX_CACHED_PAGES = 2000
TIMEOUT = httpx2.Timeout(15.0, connect=5.0)

Line = Literal["men", "women", "any"]
# "men" as a whole word only: it also sits inside damen, women and blumen.
_MEN = r"maenner|männer|herren|(?<![a-z])men(?![a-z])"
_OTHER_LINE: dict[str, re.Pattern[str]] = {
    "men": re.compile(r"frauen|damen|women|kinder|kids"),
    "women": re.compile(rf"{_MEN}|kinder|kids"),
    "any": re.compile(r"kinder|kids"),
}
_THIS_LINE: dict[str, re.Pattern[str]] = {
    "men": re.compile(_MEN),
    "women": re.compile(r"frauen|damen|women"),
    "any": re.compile(r"$^"),
}
_JSON_LD = re.compile(r"<script[^>]+application/ld\+json[^>]*>(.*?)</script>", re.S | re.I)
_LOCATION = re.compile(r"<loc>\s*(.*?)\s*</loc>")
_SITEMAP_LINE = re.compile(r"(?im)^sitemap:\s*(\S+)")


@dataclass(frozen=True, slots=True)
class Shop:
    name: str
    base: str
    product_path: str  # a product page's address contains this


# The sources whose terms docs/DATA.md records for product facts.
SHOPS = (
    Shop("Tom Tailor", "https://www.tom-tailor.at", "/de/p/"),
    Shop("Tchibo", "https://www.tchibo.at", "/products/"),
    Shop("Giesswein", "https://www.giesswein.com", "/products/"),
)


@dataclass(frozen=True, slots=True)
class Offer:
    price_cents: int
    currency: str | None
    in_stock: bool
    size: str | None


@dataclass(frozen=True, slots=True)
class PageFacts:
    title: str
    color: str | None
    material: str | None
    offers: tuple[Offer, ...]


@dataclass(frozen=True, slots=True)
class ShopProduct:
    """A product code read from its shop's page, on sale in euros, with its price at that moment."""

    url: str
    shop: str
    title: str
    price_cents: int
    color: str | None
    material: str | None
    size_checked: bool  # the page lists sizes and has one the person gave, in stock


def _text(value: Any) -> str | None:
    if isinstance(value, str | int | float) and str(value).strip():
        return str(value).strip()
    if isinstance(value, dict):
        return _text(value.get("name"))
    return None


def _products_in(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [product for entry in data for product in _products_in(entry)]
    if not isinstance(data, dict):
        return []
    found = []
    kind = data.get("@type")
    if kind == "Product" or (isinstance(kind, list) and "Product" in kind):
        found.append(data)
    for key in ("@graph", "mainEntity", "hasVariant"):
        if key in data:
            found.extend(_products_in(data[key]))
    return found


def _offer_size(product: dict[str, Any], offer: dict[str, Any]) -> str | None:
    """A shop states a size on the product of that size, on the offer, or as "color / size"."""
    size = _text(product.get("size")) or _text(offer.get("size"))
    if size:
        return size
    name = _text(offer.get("name")) or ""
    return name.rsplit("/", 1)[-1].strip() if "/" in name else None


def read_page(html: str) -> PageFacts | None:
    """The facts in a page's structured data, or None when it describes no product with a price.
    Each size can be its own product with its own offer, so every one is read."""
    offers: list[Offer] = []
    title = color = material = None
    for block in _JSON_LD.findall(html):
        try:
            products = _products_in(json.loads(block.strip()))
        except json.JSONDecodeError:
            continue
        for product in products:
            listed = product.get("offers") or []
            for offer in listed if isinstance(listed, list) else [listed]:
                if not isinstance(offer, dict):
                    continue
                price = offer.get("price") or offer.get("lowPrice")
                try:
                    cents = round(float(str(price).replace(",", ".")) * 100)
                except ValueError:
                    continue
                if cents <= 0:
                    continue
                title = title or _text(product.get("name"))
                color = color or _text(product.get("color"))
                material = material or _text(product.get("material"))
                offers.append(
                    Offer(
                        cents,
                        _text(offer.get("priceCurrency")),
                        str(offer.get("availability", "")).endswith("InStock"),
                        _offer_size(product, offer),
                    )
                )
    return PageFacts(title, color, material, tuple(offers)) if title and offers else None


def buyable(url: str, shop: str, facts: PageFacts, sizes: Iterable[str]) -> ShopProduct | None:
    """The product at its lowest price in stock, or None when the page shows it cannot be bought
    in euros, or not in any size the person gave. A page that lists no sizes leaves the size
    unchecked."""
    wanted = {size.strip().lower() for size in sizes if size.strip()}
    offers = [offer for offer in facts.offers if offer.currency == "EUR" and offer.in_stock]
    if any(offer.size for offer in facts.offers) and wanted:
        fitting = [offer for offer in offers if offer.size and offer.size.lower() in wanted]
        if not fitting:
            return None
        offers, checked = fitting, True
    else:
        checked = False
    if not offers:
        return None
    return ShopProduct(
        url,
        shop,
        facts.title,
        min(offer.price_cents for offer in offers),
        facts.color,
        facts.material,
        checked,
    )


class ShopIndex:
    """The shops' product addresses, loaded on first use and again each day, and the facts of pages
    read in the last hour, so that a person's next search does not read them again."""

    def __init__(
        self,
        shops: tuple[Shop, ...] = SHOPS,
        client: httpx2.Client | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._shops = shops
        self._client = client or httpx2.Client(
            headers={"User-Agent": AGENT, "Accept-Language": "de-AT,de"},
            timeout=TIMEOUT,
            follow_redirects=True,
        )
        self._clock = clock
        self._lock = threading.Lock()
        self._loading = threading.Lock()
        self._addresses: dict[str, tuple[float, list[str]]] = {}
        self._robots: dict[str, RobotFileParser] = {}
        self._pages: dict[str, tuple[float, PageFacts | None]] = {}

    def _read_sitemap(self, response: httpx2.Response) -> str:
        content = response.content
        if content[:2] == b"\x1f\x8b":
            content = gzip.decompress(content)
        return content.decode("utf-8", "ignore")

    def _load_shop(self, shop: Shop) -> tuple[RobotFileParser, list[str]]:
        robots = self._client.get(f"{shop.base}/robots.txt")
        robots.raise_for_status()
        parser = RobotFileParser()
        parser.parse(robots.text.splitlines())
        # Not every robots.txt names its sitemap; the standard address is then the place to look.
        named = _SITEMAP_LINE.findall(robots.text) or [f"{shop.base}/sitemap.xml"]
        queue = [url for url in named if parser.can_fetch(AGENT, url)]
        seen: set[str] = set()
        found: list[str] = []
        files = 0
        while queue and files < MAX_SITEMAP_FILES:
            url = queue.pop(0)
            if url in seen:
                continue
            seen.add(url)
            response = self._client.get(url)
            if response.status_code >= 400:
                continue
            files += 1
            body = self._read_sitemap(response)
            locations = _LOCATION.findall(body)
            if "<sitemapindex" in body:
                # Files that name products first, so the limit on files reaches them.
                children = [loc for loc in locations if parser.can_fetch(AGENT, loc)]
                queue = sorted(children, key=lambda loc: "product" not in loc.lower()) + queue
            else:
                found.extend(loc for loc in locations if shop.product_path in loc)
        return parser, list(dict.fromkeys(found))

    def load(self) -> int:
        """Bring every shop's addresses up to date, and return how many there are. A shop that does
        not answer is left out and tried again later, so the others keep working."""
        with self._loading:
            for shop in self._shops:
                now = self._clock()
                with self._lock:
                    entry = self._addresses.get(shop.name)
                if entry and entry[0] > now:
                    continue
                try:
                    parser, addresses = self._load_shop(shop)
                except (httpx2.HTTPError, OSError, EOFError) as error:
                    logger.warning("shop list not read: %s: %s", shop.name, type(error).__name__)
                    with self._lock:
                        self._addresses[shop.name] = (
                            now + RETRY_FAILED_SHOP_S,
                            entry[1] if entry else [],
                        )
                    continue
                logger.info("shop list read: %s: %d addresses", shop.name, len(addresses))
                with self._lock:
                    self._robots[shop.name] = parser
                    self._addresses[shop.name] = (now + INDEX_TTL_S, addresses)
        with self._lock:
            return sum(len(entry[1]) for entry in self._addresses.values())

    def _facts(self, url: str) -> PageFacts | None:
        now = self._clock()
        with self._lock:
            cached = self._pages.get(url)
        if cached and cached[0] > now:
            return cached[1]
        try:
            page = self._client.get(url)
        except httpx2.HTTPError as error:
            logger.warning("shop page not read: %s", type(error).__name__)
            return None
        facts = read_page(page.text) if page.status_code < 400 else None
        with self._lock:
            if len(self._pages) >= MAX_CACHED_PAGES:
                self._pages.clear()
            self._pages[url] = (now + PAGE_TTL_S, facts)
        return facts

    def search(
        self, words: Iterable[str], line: Line, sizes: Iterable[str], budget_cents: int
    ) -> list[ShopProduct]:
        """Products whose address holds every word, read from their pages, that can be bought now
        within the budget. The person's clothing line comes first, and the others stay out."""
        self.load()
        wanted = [word.strip().lower() for word in words if len(word.strip()) >= 3]
        if not wanted:
            return []
        sizes = list(sizes)
        chosen: list[tuple[Shop, str]] = []
        for shop in self._shops:
            with self._lock:
                entry = self._addresses.get(shop.name)
                parser = self._robots.get(shop.name)
            if entry is None or parser is None:
                continue
            matches = [
                url
                for url in entry[1]
                if all(word in url.lower() for word in wanted)
                and not _OTHER_LINE[line].search(url.lower())
                and parser.can_fetch(AGENT, url)
            ]
            matches.sort(key=lambda url: not _THIS_LINE[line].search(url.lower()))
            chosen.extend((shop, url) for url in matches[:PAGES_PER_SHOP])
        if not chosen:
            return []
        with ThreadPoolExecutor(READERS) as readers:
            pages = list(readers.map(lambda pair: self._facts(pair[1]), chosen))
        products = []
        for (shop, url), facts in zip(chosen, pages, strict=True):
            product = buyable(url, shop.name, facts, sizes) if facts else None
            if product and product.price_cents <= budget_cents:
                products.append(product)
        return products
