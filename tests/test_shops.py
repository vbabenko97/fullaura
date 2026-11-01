"""Reading real shops' pages: what code takes as fact, and what it refuses to assume."""

from __future__ import annotations

import gzip
import json

import httpx2
import pytest

from backend.app.shops import (
    AGENT,
    INDEX_TTL_S,
    MAX_SITEMAP_FILES,
    MAX_UNZIPPED_BYTES,
    RETRY_FAILED_SHOP_S,
    Robots,
    Shop,
    ShopIndex,
    buyable,
    read_page,
)


def page(*blocks: object) -> str:
    scripts = "".join(
        f'<script type="application/ld+json">{json.dumps(block)}</script>' for block in blocks
    )
    return f"<html><head>{scripts}</head><body>A page</body></html>"


def offer(
    price: str = "49.90", availability: str = "InStock", **extra: object
) -> dict[str, object]:
    return {
        "@type": "Offer",
        "price": price,
        "priceCurrency": "EUR",
        "availability": f"https://schema.org/{availability}",
        **extra,
    }


def product(name: str = "Hemd", offers: object = None, **extra: object) -> dict[str, object]:
    return {"@type": "Product", "name": name, "offers": offers or offer(), **extra}


def test_a_page_without_a_priced_product_gives_no_facts() -> None:
    assert read_page("<html>no data</html>") is None
    assert read_page(page({"@type": "Organization", "name": "A shop"})) is None
    assert read_page(page({"@type": "Product", "name": "Hemd"})) is None
    assert read_page('<script type="application/ld+json">{broken</script>') is None


def test_a_size_on_its_own_product_decides_stock_for_that_size() -> None:
    # The first size listed is sold out and the wanted one is not, so reading only the first would
    # call the product unavailable.
    html = page(
        product("Schuh", offer("54.95", "OutOfStock"), size="27"),
        product("Schuh", offer("54.95", "InStock"), size="43"),
    )
    facts = read_page(html)
    assert facts is not None and len(facts.offers) == 2

    found = buyable("https://shop.example/p", "Shop", facts, ["43"])
    assert found is not None and found.size_checked and found.price_cents == 5495
    assert buyable("https://shop.example/p", "Shop", facts, ["27"]) is None
    assert buyable("https://shop.example/p", "Shop", facts, ["99"]) is None


def test_a_size_in_the_offer_name_is_read() -> None:
    html = page(
        product(
            offers=[
                offer(name="moonshadow / S"),
                offer(name="moonshadow / L", availability="OutOfStock"),
            ]
        )
    )
    facts = read_page(html)
    assert facts is not None
    assert buyable("u", "Shop", facts, ["s"]) is not None
    assert buyable("u", "Shop", facts, ["L"]) is None


def test_a_page_that_lists_no_sizes_leaves_the_size_unchecked() -> None:
    facts = read_page(page(product(material="100% Baumwolle", color="blau")))
    assert facts is not None
    found = buyable("u", "Shop", facts, ["L"])
    assert found is not None
    assert not found.size_checked
    assert (found.material, found.color) == ("100% Baumwolle", "blau")


def test_only_stock_in_euros_can_be_bought() -> None:
    facts = read_page(page(product(offers=offer(availability="OutOfStock"))))
    assert facts is not None and buyable("u", "Shop", facts, []) is None
    facts = read_page(page(product(offers={**offer(), "priceCurrency": "GBP"})))
    assert facts is not None and buyable("u", "Shop", facts, []) is None


def test_the_lowest_price_in_stock_is_the_price() -> None:
    html = page(product(offers=[offer("60.00"), offer("40.00", "OutOfStock"), offer("50.00")]))
    facts = read_page(html)
    assert facts is not None
    found = buyable("u", "Shop", facts, [])
    assert found is not None and found.price_cents == 5000


def serve(routes: dict[str, httpx2.Response], seen: list[str] | None = None) -> httpx2.Client:
    def answer(request: httpx2.Request) -> httpx2.Response:
        if seen is not None:
            seen.append(str(request.url))
        return routes.get(str(request.url), httpx2.Response(404))

    return httpx2.Client(transport=httpx2.MockTransport(answer), follow_redirects=True)


SHOP = Shop("Test Shop", "https://shop.example", "/p/")
JACKET = "https://shop.example/p/winterjacke-maenner-1"
WOMEN = "https://shop.example/p/winterjacke-damen-2"
HIDDEN = "https://shop.example/private/p/winterjacke-maenner-3"
OTHER = "https://shop.example/p/hemd-maenner-4"
CATEGORY = "https://shop.example/c/winterjacke"
SITEMAP = (
    "<urlset>"
    + "".join(f"<url><loc>{url}</loc></url>" for url in (JACKET, WOMEN, HIDDEN, OTHER, CATEGORY))
    + "</urlset>"
)
ROBOTS = "User-agent: *\nDisallow: /private/\nSitemap: https://shop.example/sitemap.xml\n"


def routes(**pages: str) -> dict[str, httpx2.Response]:
    return {
        "https://shop.example/robots.txt": httpx2.Response(200, text=ROBOTS),
        "https://shop.example/sitemap.xml": httpx2.Response(200, text=SITEMAP),
        JACKET: httpx2.Response(
            200, text=pages.get("jacket", page(product("Winterjacke", offer("120.00"))))
        ),
        WOMEN: httpx2.Response(200, text=page(product("Damenjacke", offer("90.00")))),
        HIDDEN: httpx2.Response(200, text=page(product("Hidden", offer("10.00")))),
    }


def test_search_returns_only_listed_pages_of_the_line_within_the_budget() -> None:
    seen: list[str] = []
    index = ShopIndex((SHOP,), serve(routes(), seen))
    found = index.search(["jacke"], "men", [], 15000).products
    assert [product.url for product in found] == [JACKET]
    # Robots.txt excludes one address, so it is never read, and a page of another line is not read.
    assert HIDDEN not in seen and WOMEN not in seen and CATEGORY not in seen
    assert index.search(["jacke"], "men", [], 10000).products == []  # over the budget
    assert [product.url for product in index.search(["jacke"], "women", [], 15000).products] == [
        WOMEN
    ]
    assert index.search(["ab"], "men", [], 15000).products == []  # too short to mean anything


def test_a_second_search_reads_the_page_from_memory() -> None:
    seen: list[str] = []
    index = ShopIndex((SHOP,), serve(routes(), seen))
    index.search(["jacke"], "men", [], 15000)
    index.search(["winterjacke"], "men", [], 15000)
    assert seen.count(JACKET) == 1
    assert seen.count("https://shop.example/sitemap.xml") == 1


def test_a_sitemap_index_and_compressed_files_are_followed_products_first() -> None:
    index_file = (
        "<sitemapindex><sitemap><loc>https://shop.example/pages.xml</loc></sitemap>"
        "<sitemap><loc>https://shop.example/products.xml.gz</loc></sitemap></sitemapindex>"
    )
    all_routes = {
        **routes(),
        "https://shop.example/sitemap.xml": httpx2.Response(200, text=index_file),
        "https://shop.example/products.xml.gz": httpx2.Response(
            200, content=gzip.compress(SITEMAP.encode())
        ),
        "https://shop.example/pages.xml": httpx2.Response(200, text="<urlset></urlset>"),
    }
    seen: list[str] = []
    index = ShopIndex((SHOP,), serve(all_routes, seen))
    assert index.load() == 4
    assert seen.index("https://shop.example/products.xml.gz") < seen.index(
        "https://shop.example/pages.xml"
    )


def test_a_robots_file_that_names_no_sitemap_falls_back_to_the_standard_address() -> None:
    all_routes = {
        **routes(),
        "https://shop.example/robots.txt": httpx2.Response(200, text="User-agent: *\n"),
    }
    index = ShopIndex((SHOP,), serve(all_routes))
    assert index.load() == 4


def test_a_sitemap_that_robots_excludes_is_not_read() -> None:
    all_routes = {
        **routes(),
        "https://shop.example/robots.txt": httpx2.Response(
            200, text="User-agent: *\nDisallow: /\n"
        ),
    }
    seen: list[str] = []
    index = ShopIndex((SHOP,), serve(all_routes, seen))
    assert index.load() == 0
    assert seen == ["https://shop.example/robots.txt"]


def test_men_as_a_word_is_not_found_inside_other_words() -> None:
    blumen = "https://shop.example/p/blumen-jacke-damen-5"
    sitemap = f"<urlset><url><loc>{blumen}</loc></url></urlset>"
    all_routes = {
        **routes(),
        "https://shop.example/sitemap.xml": httpx2.Response(200, text=sitemap),
        blumen: httpx2.Response(200, text=page(product("Blumenjacke", offer("50.00")))),
    }
    index = ShopIndex((SHOP,), serve(all_routes))
    assert [found.url for found in index.search(["jacke"], "women", [], 15000).products] == [blumen]


def test_a_shop_that_does_not_answer_leaves_the_others_working() -> None:
    down = Shop("Down Shop", "https://down.example", "/p/")

    def answer(request: httpx2.Request) -> httpx2.Response:
        if "down.example" in str(request.url):
            raise httpx2.ConnectError("no route")
        return routes().get(str(request.url), httpx2.Response(404))

    client = httpx2.Client(transport=httpx2.MockTransport(answer), follow_redirects=True)
    index = ShopIndex((down, SHOP), client)
    assert [product.url for product in index.search(["jacke"], "men", [], 15000).products] == [
        JACKET
    ]


def test_the_user_agent_is_named_for_robots_rules() -> None:
    assert AGENT.startswith("FullAura/")


def test_a_product_of_another_name_on_the_page_is_not_this_product() -> None:
    html = page(product("Parka", offer("199.00")), product("Socken", offer("9.99")))
    facts = read_page(html)
    assert facts is not None
    found = buyable("u", "Shop", facts, [])
    assert found is not None and (found.title, found.price_cents) == ("Parka", 19900)


def test_the_color_is_that_of_the_offer_in_stock() -> None:
    html = page(
        product("Jacke", offer("149.00", "OutOfStock"), color="navy"),
        product("Jacke", offer("159.00"), color="rot"),
    )
    facts = read_page(html)
    assert facts is not None
    found = buyable("u", "Shop", facts, [])
    assert found is not None and (found.color, found.price_cents) == ("rot", 15900)


@pytest.mark.parametrize("price", ["1.299", "Infinity", "NaN", "abc", "", "0", "-5"])
def test_a_price_that_cannot_be_read_is_not_a_price(price: str) -> None:
    assert read_page(page(product(offers=offer(price)))) is None


def test_a_range_of_prices_is_not_a_price() -> None:
    ranged = {
        "@type": "AggregateOffer",
        "lowPrice": "20",
        "highPrice": "200",
        "priceCurrency": "EUR",
        "availability": "https://schema.org/InStock",
    }
    assert read_page(page(product(offers=ranged))) is None


def test_broken_text_from_a_shop_is_cleaned_and_bounded() -> None:
    facts = read_page(page(product("Hemd\ud800 " + "x" * 1000)))
    assert facts is not None
    assert len(facts.title) <= 300
    facts.title.encode("utf-8")


@pytest.mark.parametrize(
    ("name", "wanted", "fits"),
    [
        ("Blue / 32/32", "32/32", True),
        ("Blue / 32/34", "32/32", False),
        ("L / Navy", "L", True),
        ("One Size", "L", True),
    ],
)
def test_sizes_in_an_offer_name_are_read_whole(name: str, wanted: str, fits: bool) -> None:
    offers = offer(name=name) if " / " in name else offer(size=name)
    facts = read_page(page(product(offers=offers)))
    assert facts is not None
    assert (buyable("u", "Shop", facts, [wanted]) is not None) is fits


@pytest.mark.parametrize(
    ("rules", "path", "allowed"),
    [
        ("User-agent: *\nDisallow: /*/p/\n", "/de/p/hemd", False),
        ("User-agent: *\nAllow: /\nDisallow: /de/p/\n", "/de/p/hemd", False),
        ("User-agent: *\nDisallow: /de/\nAllow: /de/p/\n", "/de/p/hemd", True),
        ("User-agent: *\nDisallow: /*.pdf$\n", "/a.pdf", False),
        ("User-agent: *\nDisallow: /*.pdf$\n", "/a.pdf.html", True),
        ("User-agent: *\nDisallow:\n", "/anything", True),
        ("User-agent: FullAura\nDisallow: /p/\nUser-agent: *\nDisallow:\n", "/p/x", False),
        ("User-agent: other\nDisallow: /\n", "/p/x", True),
        ("User-agent: a\nUser-agent: *\nDisallow: /p/\n", "/p/x", False),
    ],
)
def test_robots_rules_follow_the_standard(rules: str, path: str, allowed: bool) -> None:
    robots = Robots.parse(rules)
    assert robots is not None
    assert robots.allows(f"https://shop.example{path}") is allowed


def test_a_page_in_place_of_robots_txt_leaves_the_shop_out() -> None:
    assert Robots.parse("<html><body>Are you a robot?</body></html>") is None
    all_routes = {
        **routes(),
        "https://shop.example/robots.txt": httpx2.Response(
            200, text="<html>Blocked</html>", headers={"content-type": "text/html"}
        ),
    }
    seen: list[str] = []
    assert ShopIndex((SHOP,), serve(all_routes, seen)).load() == 0
    assert seen == ["https://shop.example/robots.txt"]


def test_no_request_leaves_the_shop() -> None:
    inside = "https://shop.example/p/jacke-1"
    outside = [
        "http://169.254.169.254/p/jacke-2",
        "https://other.example/p/jacke-3",
        "http://shop.example/p/jacke-4",
        "https://shop.example.evil.example/p/jacke-5",
        "https://[bad/p/jacke-6",
    ]
    sitemap = (
        "<urlset>"
        + "".join(f"<url><loc>{url}</loc></url>" for url in [inside, *outside])
        + "</urlset>"
    )
    robots = "User-agent: *\nSitemap: https://other.example/map.xml\nSitemap: https://shop.example/sitemap.xml\n"
    all_routes = {
        "https://shop.example/robots.txt": httpx2.Response(200, text=robots),
        "https://shop.example/sitemap.xml": httpx2.Response(200, text=sitemap),
        inside: httpx2.Response(200, text=page(product("Jacke", offer("50.00")))),
    }
    seen: list[str] = []
    index = ShopIndex((SHOP,), serve(all_routes, seen))
    assert [found.url for found in index.search(["jacke"], "men", [], 15000).products] == [inside]
    assert all(url.startswith("https://shop.example/") for url in seen)


def test_a_redirect_away_from_the_shop_is_not_followed() -> None:
    target = "https://other.example/p/jacke"
    all_routes = {**routes(), JACKET: httpx2.Response(302, headers={"location": target})}
    seen: list[str] = []
    found = ShopIndex((SHOP,), serve(all_routes, seen)).search(["jacke"], "men", [], 15000)
    assert found.products == []
    assert found.failed == 1
    assert target not in seen


def test_a_redirect_within_the_shop_is_followed() -> None:
    moved = "https://shop.example/p/neu"
    all_routes = {
        **routes(),
        JACKET: httpx2.Response(301, headers={"location": "/p/neu"}),
        moved: httpx2.Response(200, text=page(product("Jacke", offer("50.00")))),
    }
    found = ShopIndex((SHOP,), serve(all_routes)).search(["jacke"], "men", [], 15000)
    assert [product.url for product in found.products] == [JACKET]


def test_pages_that_cannot_be_read_are_reported_and_tried_again() -> None:
    all_routes = {**routes(), JACKET: httpx2.Response(503)}
    seen: list[str] = []
    index = ShopIndex((SHOP,), serve(all_routes, seen))
    first = index.search(["jacke"], "men", [], 15000)
    assert (first.products, first.read, first.failed) == ([], 0, 1)
    index.search(["jacke"], "men", [], 15000)
    assert seen.count(JACKET) == 2


def test_a_failed_refresh_keeps_the_last_list_and_is_tried_again_soon() -> None:
    now = [0.0]
    all_routes = routes()
    seen: list[str] = []
    index = ShopIndex((SHOP,), serve(all_routes, seen), clock=lambda: now[0])
    assert index.load() == 4
    now[0] = INDEX_TTL_S + 1
    all_routes["https://shop.example/sitemap.xml"] = httpx2.Response(503)
    assert index.load() == 4
    seen.clear()
    assert index.load() == 4
    assert seen == []  # not again at once
    now[0] += RETRY_FAILED_SHOP_S + 1
    all_routes["https://shop.example/sitemap.xml"] = httpx2.Response(200, text=SITEMAP)
    index.load()
    assert "https://shop.example/sitemap.xml" in seen


def test_failed_sitemap_files_count_against_the_limit() -> None:
    children = "".join(
        f"<sitemap><loc>https://shop.example/m{i}.xml</loc></sitemap>" for i in range(50)
    )
    all_routes = {
        **routes(),
        "https://shop.example/sitemap.xml": httpx2.Response(
            200, text=f"<sitemapindex>{children}</sitemapindex>"
        ),
    }
    seen: list[str] = []
    assert ShopIndex((SHOP,), serve(all_routes, seen)).load() == 0
    assert len(seen) <= MAX_SITEMAP_FILES + 1


@pytest.mark.parametrize(
    "bad",
    [
        httpx2.Response(200, content=b"\x1f\x8bnot gzip"),
        httpx2.Response(200, text="<urlset><url><loc>https://[bad/p/x</loc></url></urlset>"),
    ],
)
def test_one_bad_file_does_not_end_the_other_shops(bad: httpx2.Response) -> None:
    broken = Shop("Broken", "https://broken.example", "/p/")
    all_routes = {
        **routes(),
        "https://broken.example/robots.txt": httpx2.Response(200, text="User-agent: *\n"),
        "https://broken.example/sitemap.xml": bad,
    }
    index = ShopIndex((broken, SHOP), serve(all_routes))
    assert index.load() == 4


def test_an_oversized_sitemap_is_refused() -> None:
    bomb = gzip.compress(b"<urlset></urlset>" + b" " * (MAX_UNZIPPED_BYTES + 10))
    all_routes = {
        **routes(),
        "https://shop.example/sitemap.xml": httpx2.Response(200, content=bomb),
    }
    assert ShopIndex((SHOP,), serve(all_routes)).load() == 0
