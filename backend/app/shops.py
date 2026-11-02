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
from collections import deque
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
RETRY_FAILED_PAGES_S = 2 * 60  # how long a shop that keeps failing on its pages is left alone
LOAD_WAIT_S = 60.0
MAX_SITEMAP_FILES = 12
PRODUCTS_PER_SHOP = 6  # a search stops reading a shop once this many can be bought
MAX_PAGES_PER_SEARCH = 18  # across all shops
SEARCH_BUDGET_S = 20.0  # no new pages after this; the ones being read still finish
FAILURES_TO_STOP = 3  # failed reads in a row that show a shop is failing, not just one page
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
# What an address must name for a person who chose a line. A page that names none may be any line,
# and on the shops read on 2026-11-02 a title never named a line that its address did not.
_THIS_LINE: dict[str, re.Pattern[str]] = {
    "men": re.compile(rf"{_MEN}|unisex"),
    "women": re.compile(r"frauen|damen|women|unisex"),
    "any": re.compile(r""),
}
# Not words to find in an address: shops write the line differently, as damen or frauen, and a
# search applies the person's line anyway, so one of these would only hide products of that line.
_LINE_WORDS = frozenset(
    {"damen", "dame", "frauen", "frau", "women", "women's", "womens", "woman"}
    | {"herren", "herr", "männer", "maenner", "mann", "men", "men's", "mens", "man"}
)
_JSON_LD = re.compile(r"<script[^>]+application/ld\+json[^>]*>(.*?)</script>", re.S | re.I)
_CANONICAL_LINK = re.compile(r"<link\b[^>]*\brel=[\"']?canonical\b[^>]*>", re.I)
_LINK_ADDRESS = re.compile(r"\bhref=[\"']([^\"']+)", re.I)
_IN_STOCK = re.compile(r"(?:https?://(?:www\.)?schema\.org/|schema:)?InStock")
_LOCATION = re.compile(r"<loc>\s*(.*?)\s*</loc>")
_SITEMAP_LINE = re.compile(r"(?im)^sitemap:\s*(\S+)")
# A price in euros and cents; "1.299" could be either 1299 or 1.30, so it is not read as a price.
_PRICE = re.compile(r"\d+([.,]\d{1,2})?")
_REDIRECTS = {301, 302, 303, 307, 308}
_ONE_SIZE = {"one size", "onesize", "einheitsgröße", "einheitsgroesse", "os"}
_PAGE_ADDRESS_KEYS = ("url", "mainEntityOfPage")


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
    """What a search found, and how many pages could not be read, which is not "nothing on sale". A
    shop left alone because it keeps failing counts as one such page."""

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


@dataclass(frozen=True, slots=True)
class _Described:
    """A product in a page's structured data, with the group of variants it belongs to, if any."""

    product: dict[str, Any]
    group: dict[str, Any] | None
    main: bool  # a page's mainEntity holds it

    @property
    def family(self) -> str | None:
        """What it is sold as: a variant takes its group's name, and each size may add to that."""
        return _text((self.group or {}).get("name")) or _text(self.product.get("name"))


def _products_in(
    data: Any, main_ids: set[str], group: dict[str, Any] | None = None, main: bool = False
) -> list[_Described]:
    if isinstance(data, list):
        return [product for entry in data for product in _products_in(entry, main_ids, group, main)]
    if not isinstance(data, dict):
        return []
    found = []
    kind = data.get("@type")
    kinds = kind if isinstance(kind, list) else [kind]
    if "Product" in kinds:
        found.append(_Described(data, group, main))
    if "@graph" in data:
        found.extend(_products_in(data["@graph"], main_ids))
    if "mainEntity" in data:
        entity = data["mainEntity"]
        if isinstance(entity, dict) and "@type" not in entity and "@id" in entity:
            main_ids.add(str(entity["@id"]))
        found.extend(_products_in(entity, main_ids, main=True))
    if "hasVariant" in data:
        parent = data if "Product" in kinds or "ProductGroup" in kinds else group
        found.extend(_products_in(data["hasVariant"], main_ids, parent, main))
    return found


def _page_key(address: Any, base: str) -> str | None:
    """What makes two addresses one page: host and path, whatever the scheme or size chosen."""
    if isinstance(address, dict):
        address = address.get("@id")
    if not isinstance(address, str) or not address.strip():
        return None
    try:
        parts = urlsplit(urljoin(base, address.strip()))
    except ValueError:
        return None
    host = (parts.hostname or "").removeprefix("www.")
    return f"{host}{parts.path.rstrip('/').lower()}" if host else None


def _names_this_page(entry: _Described, pages: set[str], base: str) -> bool | None:
    """Whether a product says it is the one this page sells: True, False when it names another
    page, None when it says nothing. Its page addresses decide, each of which must be this page,
    and a variant without any is where its group is. Only without them can an @id or an offer's
    address confirm this; neither can deny it, as neither has to be a page's address."""
    product = entry.product
    named = [_page_key(product.get(key), base) for key in _PAGE_ADDRESS_KEYS]
    if not any(named) and entry.group:
        named = [_page_key(entry.group.get(key), base) for key in _PAGE_ADDRESS_KEYS]
    stated = {key for key in named if key}
    if stated:
        return stated <= pages
    listed = product.get("offers")
    offers = listed if isinstance(listed, list) else [listed]
    confirming = [
        _page_key(product.get("@id"), base),
        *(_page_key(offer.get("url"), base) for offer in offers if isinstance(offer, dict)),
    ]
    return True if pages.intersection(confirming) else None


def _main_product(html: str, addresses: tuple[str, ...]) -> tuple[str, list[_Described]] | None:
    """What this page sells, and each variant of it, or None when the data does not say. A product
    that names this page or its canonical address, or a mainEntity, decides, and then only what it
    decided counts; a product that names another page is never this one. When nothing decides,
    every product left must share one name and every block must be readable, as an unreadable one
    may hold the real product."""
    described: list[_Described] = []
    main_ids: set[str] = set()
    whole = True
    for block in _JSON_LD.findall(html):
        try:
            described.extend(_products_in(json.loads(block.strip()), main_ids))
        except (json.JSONDecodeError, RecursionError):
            whole = False
    base = addresses[0] if addresses else ""
    link = _CANONICAL_LINK.search(html)
    canonical = _LINK_ADDRESS.search(link.group(0)) if link else None
    keys = [_page_key(address, base) for address in addresses]
    keys.append(_page_key(canonical.group(1), base) if canonical else None)
    pages = {key for key in keys if key}

    kept: list[tuple[str, _Described]] = []
    decided: list[tuple[str, _Described]] = []
    for entry in described:
        family = entry.family
        says = _names_this_page(entry, pages, base)
        if family is None or says is False:
            continue
        kept.append((family, entry))
        ids = {str(source.get("@id")) for source in (entry.product, entry.group or {})}
        if says or entry.main or ids & main_ids:
            decided.append((family, entry))
    if not decided and not whole:
        return None
    chosen = decided or kept
    families = {family for family, _ in chosen}
    if len(families) != 1:
        return None
    return families.pop(), [entry for _, entry in chosen]


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


def read_page(html: str, *addresses: str) -> PageFacts | None:
    """The facts in a page's structured data about the product it sells, or None when it has no
    price for that product or does not say which one that is. A page holds other products too, such
    as recommendations; addresses are where the page was asked for and where it was answered from.
    Each size can be its own product with its own offer, so every variant is read."""
    main = _main_product(html, addresses)
    if main is None:
        return None
    title, variants = main
    offers: list[Offer] = []
    material = None
    for entry in variants:
        product = entry.product
        listed = product.get("offers") or []
        for offer in listed if isinstance(listed, list) else [listed]:
            if not isinstance(offer, dict):
                continue
            cents = _offer_price(offer)
            if cents is None:
                continue
            material = material or _text(product.get("material"))
            offers.append(
                Offer(
                    cents,
                    _text(offer.get("priceCurrency")),
                    _IN_STOCK.fullmatch(str(offer.get("availability", "")).strip()) is not None,
                    _offer_sizes(product, offer),
                    _text(product.get("color")) or _text(offer.get("color")),
                )
            )
    return PageFacts(title, material, tuple(offers)) if offers else None


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


@dataclass(slots=True)
class _Reading:
    """One shop's part in a search: what is left to read, and how that has gone so far."""

    shop: Shop
    robots: Robots
    addresses: deque[str]
    found: int = 0
    failures: int = 0  # in a row


def _next_portion(readings: list[_Reading], pages_left: int) -> list[tuple[_Reading, str]]:
    """The next pages to read: an equal share from each shop that still needs some, and no more at
    once than there are readers, so that the time limit is looked at often."""
    needing = [
        reading
        for reading in readings
        if reading.addresses
        and reading.found < PRODUCTS_PER_SHOP
        and reading.failures < FAILURES_TO_STOP
    ]
    portion: list[tuple[_Reading, str]] = []
    for reading in needing:
        share = max(1, READERS // len(needing))
        count = min(share, PRODUCTS_PER_SHOP - reading.found, pages_left - len(portion))
        while count > 0 and reading.addresses:
            portion.append((reading, reading.addresses.popleft()))
            count -= 1
    return portion


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
        self._down_until: dict[str, float] = {}

    def _get(
        self, shop: Shop, url: str, robots: Robots | None, limit: int
    ) -> tuple[int, str, bytes, str]:
        """Status, content type, body and final address of an address on the shop's own host,
        following redirects only to pages it owns and robots.txt allows, and reading at most limit
        bytes."""
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
                return response.status_code, kind, bytes(body), url
        raise ShopReadError("too many redirects")

    def _load_shop(self, shop: Shop) -> tuple[Robots, list[str]]:
        status, kind, body, _ = self._get(shop, f"{shop.base}/robots.txt", None, MAX_PAGE_BYTES)
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
            status, _, content, _ = self._get(shop, url, robots, MAX_SITEMAP_BYTES)
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
        status, _, body, final = self._get(shop, url, robots, MAX_PAGE_BYTES)
        if status not in (200, 404, 410):
            raise ShopReadError(f"page answered {status}")
        facts = read_page(body.decode("utf-8", "ignore"), url, final) if status == 200 else None
        if status == 200 and facts is None:
            logger.info("shop page names no single priced product: %s", shop.name)
        with self._lock:
            if len(self._pages) >= MAX_CACHED_PAGES:
                self._pages.clear()
            self._pages[url] = (now + PAGE_TTL_S, facts)
        return facts

    def pages_down(self) -> bool:
        """Whether every shop with a list keeps failing on its pages, so no search can read any."""
        now = self._clock()
        with self._lock:
            listed = [name for name, entry in self._addresses.items() if entry[1]]
            return bool(listed) and all(now < self._down_until.get(name, 0.0) for name in listed)

    def search(
        self, words: Iterable[str], line: Line, sizes: Iterable[str], budget_cents: int
    ) -> ShopSearch:
        """Products whose address holds every word, read from their pages, that can be bought now
        within the budget. The address must name the person's clothing line, or unisex, unless they
        take any line, so a product that names none is not read for someone who chose one. Pages
        are read a few at a time until each shop has enough products, has no address left or keeps
        failing, or until the search reaches its limit of pages or of time. A shop that keeps
        failing is then left alone for a while, and the other shops are read as usual."""
        self.load(wait_s=0)
        cleaned = (word.strip().lower() for word in words)
        wanted = [word for word in cleaned if len(word) >= 3 and word not in _LINE_WORDS]
        if not wanted:
            return ShopSearch([])
        sizes = list(sizes)
        readings: list[_Reading] = []
        left_alone = 0
        for shop in self._shops:
            now = self._clock()
            with self._lock:
                entry = self._addresses.get(shop.name)
                robots = self._robots.get(shop.name)
                down = now < self._down_until.get(shop.name, 0.0)
            if entry is None or robots is None:
                continue
            matches = [
                url
                for url in entry[1]
                if all(word in url.lower() for word in wanted)
                and _THIS_LINE[line].search(url.lower())
                and not _OTHER_LINE[line].search(url.lower())
                and robots.allows(url)
            ]
            if down:
                left_alone += bool(matches)
                continue
            readings.append(_Reading(shop, robots, deque(matches)))

        def read(entry: tuple[_Reading, str]) -> PageFacts | None | ShopReadError:
            try:
                return self._facts(entry[0].shop, entry[1], entry[0].robots)
            except Exception as error:  # a page's bad answer leaves the other pages to read
                logger.warning("shop page not read: %s", type(error).__name__)
                return ShopReadError()

        products: list[ShopProduct] = []
        read_count = failed = 0
        deadline = self._clock() + SEARCH_BUDGET_S
        with ThreadPoolExecutor(READERS) as readers:
            while self._clock() < deadline:
                portion = _next_portion(readings, MAX_PAGES_PER_SEARCH - read_count - failed)
                if not portion:
                    break
                for (reading, url), facts in zip(portion, readers.map(read, portion), strict=True):
                    if isinstance(facts, ShopReadError):
                        failed += 1
                        reading.failures += 1
                        continue
                    read_count += 1
                    reading.failures = 0
                    product = buyable(url, reading.shop.name, facts, sizes) if facts else None
                    if (
                        product
                        and product.price_cents <= budget_cents
                        and not _OTHER_LINE[line].search(product.title.lower())
                    ):
                        products.append(product)
                        reading.found += 1
        back_at = self._clock() + RETRY_FAILED_PAGES_S
        with self._lock:
            for reading in readings:
                if reading.failures >= FAILURES_TO_STOP:
                    self._down_until[reading.shop.name] = back_at
        return ShopSearch(products, read_count, failed + left_alone)
