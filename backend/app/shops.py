"""Real products from shops' own sites, read when a person asks for them.

Addresses come from each shop's sitemap and facts from the structured data on its product page, both
only where the shop's robots.txt allows this user agent, and only on the shop's own host over HTTPS,
through redirects too. A model never supplies an address that code has not listed, so it cannot
name a product that does not exist. Addresses and facts stay in process memory; nothing is written
to disk.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import zlib
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urljoin, urlsplit

import httpx2

logger = logging.getLogger(__name__)

AGENT = "FullAura/0.1 (closed beta; reads a product page when a person asks for it)"
AGENT_TOKEN = "fullaura"
INDEX_TTL_S = 24 * 60 * 60
PAGE_TTL_S = 60 * 60
RETRY_FAILED_SHOP_S = 10 * 60
LOAD_WAIT_S = 60.0
MAX_SITEMAP_FILES = 12
PAGES_PER_SHOP = 6
READERS = 4
MAX_CACHED_PAGES = 2000
MAX_REDIRECTS = 3
MAX_PAGE_BYTES = 4 * 1024 * 1024
MAX_SITEMAP_BYTES = 64 * 1024 * 1024
MAX_UNZIPPED_BYTES = 256 * 1024 * 1024
MAX_TEXT_CHARS = 300
READ_BUDGET_S = 30.0  # one whole body, so a slow drip cannot hold a reader
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
# A price in euros and cents; "1.299" could be either 1299 or 1.30, so it is not read as a price.
_PRICE = re.compile(r"\d+([.,]\d{1,2})?")
_REDIRECTS = {301, 302, 303, 307, 308}
_ONE_SIZE = {"one size", "onesize", "einheitsgröße", "einheitsgroesse", "os"}


class ShopReadError(Exception):
    """A shop's file or page could not be read, which says nothing about its products."""


@dataclass(frozen=True, slots=True)
class Shop:
    name: str
    base: str
    product_path: str  # a product page's address contains this

    def owns(self, url: str) -> bool:
        """Only this shop's own host over HTTPS: a sitemap or redirect that points elsewhere could
        reach an internal address, or show another site's price under this shop's name."""
        try:
            parts = urlsplit(url)
            return parts.scheme == "https" and parts.hostname == urlsplit(self.base).hostname
        except ValueError:
            return False


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
    sizes: tuple[str, ...]  # the labels this offer could be read as a size by
    color: str | None


@dataclass(frozen=True, slots=True)
class PageFacts:
    title: str
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


@dataclass(frozen=True, slots=True)
class ShopSearch:
    """What a search found, and how many pages could not be read, which is not "nothing on sale"."""

    products: list[ShopProduct]
    read: int = 0
    failed: int = 0


class Robots:
    """A robots.txt as RFC 9309 reads it: the group for this agent or else for all, the longest
    matching rule wins, an allow wins a tie, and * and $ work."""

    def __init__(self, rules: list[tuple[bool, re.Pattern[str], int]]) -> None:
        self._rules = rules

    @classmethod
    def parse(cls, text: str) -> Robots | None:
        """None for anything that is not a robots file, such as a challenge page."""
        groups: list[tuple[set[str], list[tuple[bool, str]]]] = []
        agents: set[str] = set()
        rules: list[tuple[bool, str]] = []
        in_rules = False
        for raw in text.splitlines():
            key, _, value = raw.split("#", 1)[0].partition(":")
            key, value = key.strip().lower(), value.strip()
            if key == "user-agent":
                if in_rules:
                    agents, rules, in_rules = set(), [], False
                if not agents:
                    groups.append((agents, rules))
                agents.add(value.lower())
            elif key in {"allow", "disallow"} and groups:
                in_rules = True
                if value:
                    rules.append((key == "allow", value))
        if not groups:
            return None
        named = [group for group in groups if any(a != "*" and a in AGENT_TOKEN for a in group[0])]
        chosen = named or [group for group in groups if "*" in group[0]]
        compiled = []
        for _, group_rules in chosen:
            for allow, pattern in group_rules:
                end = pattern.endswith("$")
                body = re.escape(pattern[:-1] if end else pattern).replace(r"\*", ".*")
                compiled.append((allow, re.compile(body + ("$" if end else "")), len(pattern)))
        return cls(compiled)

    def allows(self, url: str) -> bool:
        parts = urlsplit(url)
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        matches = [(length, allow) for allow, rule, length in self._rules if rule.match(path)]
        return max(matches)[1] if matches else True


def _text(value: Any) -> str | None:
    """A shop's text as plain, valid text of a bounded length."""
    if isinstance(value, str | int | float) and not isinstance(value, bool):
        cleaned = str(value).encode("utf-8", "ignore").decode("utf-8")
        cleaned = " ".join(cleaned.split())[:MAX_TEXT_CHARS]
        return cleaned or None
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


def _offer_sizes(product: dict[str, Any], offer: dict[str, Any]) -> tuple[str, ...]:
    """A shop states a size on the product of that size, on the offer, or in the offer's name, as
    in "moonshadow / L" or "L / navy"; each part of a name may be the size."""
    size = _text(product.get("size")) or _text(offer.get("size"))
    if size:
        return (size,)
    name = _text(offer.get("name")) or ""
    return tuple(part.strip() for part in name.split(" / ")) if " / " in name else ()


def _offer_price(offer: dict[str, Any]) -> int | None:
    """The price in cents. A range's low price is not the price of this product."""
    if offer.get("@type") == "AggregateOffer" and offer.get("lowPrice") != offer.get("highPrice"):
        return None
    price = _text(offer.get("price") if offer.get("price") is not None else offer.get("lowPrice"))
    if price is None or not _PRICE.fullmatch(price):
        return None
    cents = round(float(price.replace(",", ".")) * 100)
    return cents if cents > 0 else None


def read_page(html: str) -> PageFacts | None:
    """The facts in a page's structured data, or None when it describes no product with a price.
    Each size can be its own product with its own offer, so every product with the first one's name
    is read; another name is another product, such as a recommendation on the same page."""
    offers: list[Offer] = []
    title = material = None
    for block in _JSON_LD.findall(html):
        try:
            products = _products_in(json.loads(block.strip()))
        except (json.JSONDecodeError, RecursionError):
            continue
        for product in products:
            name = _text(product.get("name"))
            if name is None or (title is not None and name != title):
                continue
            listed = product.get("offers") or []
            for offer in listed if isinstance(listed, list) else [listed]:
                if not isinstance(offer, dict):
                    continue
                cents = _offer_price(offer)
                if cents is None:
                    continue
                title = name
                material = material or _text(product.get("material"))
                offers.append(
                    Offer(
                        cents,
                        _text(offer.get("priceCurrency")),
                        str(offer.get("availability", "")).endswith("InStock"),
                        _offer_sizes(product, offer),
                        _text(product.get("color")) or _text(offer.get("color")),
                    )
                )
    return PageFacts(title, material, tuple(offers)) if title and offers else None


def buyable(url: str, shop: str, facts: PageFacts, sizes: Iterable[str]) -> ShopProduct | None:
    """The product at its lowest price in stock, or None when the page shows it cannot be bought
    in euros, or not in any size the person gave. A page that lists no sizes leaves the size
    unchecked, and a one-size product fits."""
    wanted = {size.strip().lower() for size in sizes if size.strip()}
    offers = [offer for offer in facts.offers if offer.currency == "EUR" and offer.in_stock]
    checked = False
    if any(offer.sizes for offer in facts.offers) and wanted:
        fitting = [
            offer
            for offer in offers
            if {label.lower() for label in offer.sizes} & (wanted | _ONE_SIZE)
        ]
        if not fitting:
            return None
        offers, checked = fitting, True
    if not offers:
        return None
    cheapest = min(offers, key=lambda offer: offer.price_cents)
    return ShopProduct(
        url, shop, facts.title, cheapest.price_cents, cheapest.color, facts.material, checked
    )


def _unzipped(content: bytes) -> bytes:
    if content[:2] != b"\x1f\x8b":
        return content
    unzip = zlib.decompressobj(31)
    try:
        body = unzip.decompress(content, MAX_UNZIPPED_BYTES)
    except zlib.error as error:
        raise ShopReadError("corrupt sitemap") from error
    if unzip.unconsumed_tail:
        raise ShopReadError("sitemap too large")
    return body


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
            headers={"User-Agent": AGENT, "Accept-Language": "de-AT,de"}, timeout=TIMEOUT
        )
        self._clock = clock
        self._lock = threading.Lock()
        self._loading = threading.Lock()
        self._addresses: dict[str, tuple[float, list[str]]] = {}
        self._robots: dict[str, Robots] = {}
        self._pages: dict[str, tuple[float, PageFacts | None]] = {}

    def _get(
        self, shop: Shop, url: str, robots: Robots | None, limit: int
    ) -> tuple[int, str, bytes]:
        """Status, content type and body of an address on the shop's own host, following redirects
        only to pages it owns and robots.txt allows, and reading at most limit bytes."""
        started = time.monotonic()
        for _ in range(MAX_REDIRECTS + 1):
            if not shop.owns(url) or (robots is not None and not robots.allows(url)):
                raise ShopReadError("address outside the shop or not allowed")
            with self._client.stream("GET", url, follow_redirects=False) as response:
                if response.status_code in _REDIRECTS:
                    url = urljoin(url, response.headers.get("location", ""))
                    continue
                body = bytearray()
                for chunk in response.iter_bytes():
                    body += chunk
                    if len(body) > limit or time.monotonic() - started > READ_BUDGET_S:
                        raise ShopReadError("answer too large or too slow")
                kind = response.headers.get("content-type", "").lower()
                return response.status_code, kind, bytes(body)
        raise ShopReadError("too many redirects")

    def _load_shop(self, shop: Shop) -> tuple[Robots, list[str]]:
        status, kind, body = self._get(shop, f"{shop.base}/robots.txt", None, MAX_PAGE_BYTES)
        text = body.decode("utf-8", "ignore")
        robots = Robots.parse(text) if status < 400 and "html" not in kind else None
        if robots is None:
            raise ShopReadError("no robots file")
        # Not every robots.txt names its sitemap; the standard address is then the place to look.
        named = _SITEMAP_LINE.findall(text) or [f"{shop.base}/sitemap.xml"]
        queue = [url for url in named if shop.owns(url) and robots.allows(url)]
        seen: set[str] = set()
        found: list[str] = []
        files = 0
        while queue and files < MAX_SITEMAP_FILES:
            url = queue.pop(0)
            if url in seen:
                continue
            seen.add(url)
            files += 1
            status, _, content = self._get(shop, url, robots, MAX_SITEMAP_BYTES)
            if status >= 400:
                continue
            sitemap = _unzipped(content).decode("utf-8", "ignore")
            locations = _LOCATION.findall(sitemap)
            if "<sitemapindex" in sitemap:
                # Files that name products first, so the limit on files reaches them.
                children = [loc for loc in locations if shop.owns(loc) and robots.allows(loc)]
                queue = sorted(children, key=lambda loc: "product" not in loc.lower()) + queue
            else:
                found.extend(
                    loc for loc in locations if shop.product_path in loc and shop.owns(loc)
                )
        if not found:
            raise ShopReadError("no product address found")
        return robots, list(dict.fromkeys(found))

    def load(self, wait_s: float = LOAD_WAIT_S) -> int:
        """Bring every shop's addresses up to date, waiting at most wait_s for a load already
        running, and return how many there are. A shop that fails keeps its last list and is tried
        again later, so the others keep working."""
        if self._loading.acquire(timeout=wait_s):
            try:
                for shop in self._shops:
                    self._load_if_due(shop)
            finally:
                self._loading.release()
        with self._lock:
            return sum(len(entry[1]) for entry in self._addresses.values())

    def _load_if_due(self, shop: Shop) -> None:
        now = self._clock()
        with self._lock:
            entry = self._addresses.get(shop.name)
        if entry and entry[0] > now:
            return
        try:
            robots, addresses = self._load_shop(shop)
        except Exception as error:  # one bad answer from one shop must not end the others
            logger.warning("shop list not read: %s: %s", shop.name, type(error).__name__)
            with self._lock:
                self._addresses[shop.name] = (now + RETRY_FAILED_SHOP_S, entry[1] if entry else [])
            return
        logger.info("shop list read: %s: %d addresses", shop.name, len(addresses))
        with self._lock:
            self._robots[shop.name] = robots
            self._addresses[shop.name] = (now + INDEX_TTL_S, addresses)

    def _facts(self, shop: Shop, url: str, robots: Robots) -> PageFacts | None:
        """The page's facts, or None when it holds no product. Only an answer about the page is
        kept; a failed read raises and is tried again next time."""
        now = self._clock()
        with self._lock:
            cached = self._pages.get(url)
        if cached and cached[0] > now:
            return cached[1]
        status, _, body = self._get(shop, url, robots, MAX_PAGE_BYTES)
        if status not in (200, 404, 410):
            raise ShopReadError(f"page answered {status}")
        facts = read_page(body.decode("utf-8", "ignore")) if status == 200 else None
        with self._lock:
            if len(self._pages) >= MAX_CACHED_PAGES:
                self._pages.clear()
            self._pages[url] = (now + PAGE_TTL_S, facts)
        return facts

    def search(
        self, words: Iterable[str], line: Line, sizes: Iterable[str], budget_cents: int
    ) -> ShopSearch:
        """Products whose address holds every word, read from their pages, that can be bought now
        within the budget. The person's clothing line comes first, and the others stay out."""
        self.load(wait_s=0)
        wanted = [word.strip().lower() for word in words if len(word.strip()) >= 3]
        if not wanted:
            return ShopSearch([])
        sizes = list(sizes)
        chosen: list[tuple[Shop, str, Robots]] = []
        for shop in self._shops:
            with self._lock:
                entry = self._addresses.get(shop.name)
                robots = self._robots.get(shop.name)
            if entry is None or robots is None:
                continue
            matches = [
                url
                for url in entry[1]
                if all(word in url.lower() for word in wanted)
                and not _OTHER_LINE[line].search(url.lower())
                and robots.allows(url)
            ]
            matches.sort(key=lambda url: not _THIS_LINE[line].search(url.lower()))
            chosen.extend((shop, url, robots) for url in matches[:PAGES_PER_SHOP])
        if not chosen:
            return ShopSearch([])

        def read(entry: tuple[Shop, str, Robots]) -> PageFacts | None | ShopReadError:
            try:
                return self._facts(*entry)
            except Exception as error:  # a page's bad answer leaves the other pages to read
                logger.warning("shop page not read: %s", type(error).__name__)
                return ShopReadError()

        with ThreadPoolExecutor(READERS) as readers:
            pages = list(readers.map(read, chosen))
        products = []
        failed = 0
        for (shop, url, _), facts in zip(chosen, pages, strict=True):
            if isinstance(facts, ShopReadError):
                failed += 1
                continue
            product = buyable(url, shop.name, facts, sizes) if facts else None
            if product and product.price_cents <= budget_cents:
                products.append(product)
        return ShopSearch(products, len(chosen) - failed, failed)
