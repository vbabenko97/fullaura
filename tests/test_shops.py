"""Reading real shops' pages: what code takes as fact, and what it refuses to assume."""

from __future__ import annotations

import gzip
import json

import httpx2
import pytest

from backend.app.shops import (
    AGENT,
    FAILURES_TO_STOP,
    INDEX_TTL_S,
    MAX_PAGES_PER_SEARCH,
    MAX_SITEMAP_FILES,
    MAX_UNZIPPED_BYTES,
    PRODUCTS_PER_SHOP,
    READERS,
    RETRY_FAILED_PAGES_S,
    RETRY_FAILED_SHOP_S,
    SEARCH_BUDGET_S,
    Line,
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


@pytest.mark.parametrize(
    ("words", "line", "address"),
    [
        (["damen", "jacke"], "women", WOMEN),
        (["frauen", "jacke"], "women", WOMEN),
        (["herren", "jacke"], "men", JACKET),
        (["Männer", "jacke"], "men", JACKET),
        (["men's", "jacke"], "men", JACKET),
    ],
)
def test_a_word_for_the_clothing_line_does_not_narrow_the_search(
    words: list[str], line: Line, address: str
) -> None:
    # Shops write the line differently, as damen or frauen, and the line is applied anyway.
    found = ShopIndex((SHOP,), serve(routes())).search(words, line, [], 15000)
    assert [product.url for product in found.products] == [address]


def test_a_chosen_line_must_be_named_in_the_address_and_no_other_page_is_read() -> None:
    addresses = {
        "plain": "https://shop.example/p/jacke-1",
        "men": "https://shop.example/p/jacke-herren-2",
        "women": "https://shop.example/p/jacke-frauen-3",
        "unisex": "https://shop.example/p/jacke-unisex-4",
        "kids": "https://shop.example/p/jacke-kinder-5",
    }
    sitemap = "".join(f"<url><loc>{address}</loc></url>" for address in addresses.values())
    all_routes = {
        "https://shop.example/robots.txt": httpx2.Response(200, text=ROBOTS),
        "https://shop.example/sitemap.xml": httpx2.Response(
            200, text=f"<urlset>{sitemap}</urlset>"
        ),
        **{
            address: httpx2.Response(200, text=page(product("Jacke", offer("50.00"))))
            for address in addresses.values()
        },
    }
    seen: list[str] = []
    index = ShopIndex((SHOP,), serve(all_routes, seen))

    def found(line: Line) -> set[str]:
        return {item.url for item in index.search(["jacke"], line, [], 15000).products}

    assert found("men") == {addresses["men"], addresses["unisex"]}
    # A page that could not be this line is not read at all.
    assert addresses["plain"] not in seen and addresses["women"] not in seen
    assert found("women") == {addresses["women"], addresses["unisex"]}
    # Either also takes what names no line, and no line ever takes children's clothes.
    assert found("any") == {
        addresses["plain"],
        addresses["men"],
        addresses["women"],
        addresses["unisex"],
    }


def test_a_title_that_names_another_line_takes_the_product_out() -> None:
    all_routes = listing("https://shop.example", [page(product("Damen-Parka"))], "jacke-herren")
    found = ShopIndex((SHOP,), serve(all_routes)).search(["jacke"], "men", [], 15000)
    assert (found.products, found.read) == ([], 1)


def test_a_word_for_the_line_alone_is_not_a_search() -> None:
    seen: list[str] = []
    found = ShopIndex((SHOP,), serve(routes(), seen)).search(["damen"], "women", [], 15000)
    assert (found.products, found.read, found.failed) == ([], 0, 0)
    assert page_reads(seen) == []


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


PARKA = "https://shop.example/p/parka-1"
PARKA_PRODUCT = product("Parka", offer("199.00"), url=PARKA)
SOCKS = product("Socken", offer("9.99"), url="https://shop.example/p/socken-2")
UNADDRESSED_SOCKS = product("Socken", offer("9.99"))


@pytest.mark.parametrize(
    "blocks",
    [
        (SOCKS, PARKA_PRODUCT),
        (PARKA_PRODUCT, SOCKS),
        (UNADDRESSED_SOCKS, PARKA_PRODUCT),
        (PARKA_PRODUCT, UNADDRESSED_SOCKS),
    ],
)
def test_a_recommendation_before_or_after_the_product_is_not_this_product(
    blocks: tuple[dict[str, object], ...],
) -> None:
    facts = read_page(page(*blocks), PARKA)
    assert facts is not None
    found = buyable(PARKA, "Shop", facts, [])
    assert found is not None and (found.title, found.price_cents) == ("Parka", 19900)
    assert len(facts.offers) == 1


@pytest.mark.parametrize(
    "main",
    [
        product("Parka", offer("199.00"), url="/p/parka-1"),
        product("Parka", offer("199.00"), url=f"{PARKA}/?variant=3#size"),
        product("Parka", offer("199.00"), url="http://shop.example/P/Parka-1"),
        product("Parka", offer("199.00"), url="https://www.shop.example/p/parka-1"),
        product("Parka", offer("199.00"), **{"@id": f"{PARKA}#product"}),
        product("Parka", offer("199.00"), mainEntityOfPage=PARKA),
        product("Parka", offer("199.00"), mainEntityOfPage={"@id": PARKA}),
        product("Parka", offer("199.00", url=f"{PARKA}?variant=7")),
        {"@type": "ItemPage", "mainEntity": product("Parka", offer("199.00"))},
        {
            "@graph": [
                {"@type": "WebPage", "mainEntity": {"@id": "#main"}},
                product("Parka", offer("199.00"), **{"@id": "#main"}),
            ]
        },
    ],
)
def test_each_way_a_page_names_its_product_is_read(main: dict[str, object]) -> None:
    facts = read_page(page(UNADDRESSED_SOCKS, main), PARKA)
    assert facts is not None and facts.title == "Parka"
    assert [offer.price_cents for offer in facts.offers] == [19900]


def test_only_this_pages_color_counts_in_a_group_of_colors() -> None:
    # Tom Tailor's shape: a group without an address, this color with its offer, others beside it.
    group = {
        "@type": "ProductGroup",
        "name": "Parka in blau",
        "hasVariant": [
            {"@type": "Product", "name": "Parka in rot", "url": "https://shop.example/p/parka-2"},
            product("Parka in rot", offer("99.00"), url=f"{PARKA}-rot", color="rot"),
            product("Parka in blau", offer("199.00"), url=PARKA, color="blau", material="Wolle"),
        ],
    }
    facts = read_page(page(group), PARKA)
    assert facts is not None and facts.title == "Parka in blau" and facts.material == "Wolle"
    assert [(offer.price_cents, offer.color) for offer in facts.offers] == [(19900, "blau")]


def test_every_size_of_the_product_is_read_as_one_product() -> None:
    by_group = {
        "@type": "ProductGroup",
        "name": "Parka",
        "url": PARKA,
        "hasVariant": [
            product("Parka - S", offer("199.00", "OutOfStock"), size="S"),
            product("Parka - L", offer("199.00"), size="L"),
        ],
    }
    by_address = [
        product("Parka", offer("199.00", "OutOfStock"), url=f"{PARKA}?variant=1", size="S"),
        product("Parka", offer("199.00"), url=f"{PARKA}?variant=2", size="L"),
    ]
    for html in (page(by_group, SOCKS), page(*by_address, SOCKS)):
        facts = read_page(html, PARKA)
        assert facts is not None and facts.title == "Parka" and len(facts.offers) == 2
        assert buyable(PARKA, "Shop", facts, ["L"]) is not None
        assert buyable(PARKA, "Shop", facts, ["S"]) is None


def test_the_same_name_without_an_address_is_still_one_product() -> None:
    facts = read_page(page(product("Parka", size="S"), product("Parka", size="L")), PARKA)
    assert facts is not None and len(facts.offers) == 2


@pytest.mark.parametrize(
    "html",
    [
        # Two names and nothing that says which one this page sells.
        page(product("Parka", offer("199.00")), UNADDRESSED_SOCKS),
        # Each claims to be this page.
        page(PARKA_PRODUCT, product("Socken", offer("9.99"), url=PARKA)),
        page(
            {"@type": "ItemPage", "mainEntity": [product("Parka"), product("Socken")]},
        ),
        # Only another page's product is described.
        page(SOCKS),
        page(
            {
                "@type": "ProductGroup",
                "name": "Socken",
                "hasVariant": [SOCKS, product("Socken", url="https://shop.example/p/socken-3")],
            }
        ),
        # The block that may hold the product cannot be read, so the other one proves nothing.
        '<script type="application/ld+json">{broken</script>' + page(UNADDRESSED_SOCKS),
        # This page's product has no price; a recommendation's price is not its price.
        page({"@type": "Product", "name": "Parka", "url": PARKA}, UNADDRESSED_SOCKS),
    ],
)
def test_a_page_that_does_not_say_which_product_it_sells_gives_no_facts(html: str) -> None:
    assert read_page(html, PARKA) is None


def test_the_canonical_address_also_names_the_page() -> None:
    moved = "https://shop.example/de/p/parka-1"
    html = page(product("Parka", offer("199.00"), url=moved), SOCKS)
    assert read_page(html, PARKA) is None
    canonical = f'<link data-hid="c" rel="canonical" href="{moved}">{html}'
    facts = read_page(canonical, PARKA)
    assert facts is not None and facts.title == "Parka"


OTHER_PAGE = "https://shop.example/p/socken-2"


@pytest.mark.parametrize(
    "socks",
    [
        product("Socken", offer("9.99", url=PARKA), url=OTHER_PAGE),
        product("Socken", offer("9.99"), url=OTHER_PAGE, **{"@id": PARKA}),
        product("Socken", offer("9.99"), url=OTHER_PAGE, mainEntityOfPage=PARKA),
        product("Socken", offer("9.99"), url=PARKA, mainEntityOfPage=OTHER_PAGE),
        {
            "@type": "ProductGroup",
            "name": "Socken",
            "url": OTHER_PAGE,
            "hasVariant": [product("Socken", offer("9.99", url=PARKA))],
        },
    ],
)
def test_a_product_that_names_another_page_is_not_this_pages_whatever_else_it_says(
    socks: dict[str, object],
) -> None:
    assert read_page(page(socks), PARKA) is None
    facts = read_page(page(socks, PARKA_PRODUCT), PARKA)
    assert facts is not None and facts.title == "Parka"
    assert [offer.price_cents for offer in facts.offers] == [19900]


def test_a_same_named_product_without_an_address_does_not_lend_its_price() -> None:
    facts = read_page(page(product("Parka", offer("9.99")), PARKA_PRODUCT), PARKA)
    assert facts is not None
    assert [offer.price_cents for offer in facts.offers] == [19900]


def test_the_shapes_of_tchibos_and_giessweins_pages_are_read() -> None:
    # Invented products in the shapes both shops' real pages had on 2026-11-02.
    jacket = "https://shop.example/products/1/regenjacke"
    tchibo = product(
        "Regenjacke",
        [
            offer("49.99", url="/products/1/regenjacke?article_id=7"),
            offer("49.99", "OutOfStock", url="/products/1/regenjacke?article_id=8"),
        ],
        url="/products/1/regenjacke?article_id=7",
    )
    for asked in (jacket, f"{jacket}?article_id=8"):
        facts = read_page(page(tchibo, UNADDRESSED_SOCKS), asked)
        assert facts is not None and facts.title == "Regenjacke" and len(facts.offers) == 2
        assert [offer.in_stock for offer in facts.offers] == [True, False]

    sneaker = "https://shop.example/products/wool-sneaker"
    giesswein = product(
        "Wool Sneaker",
        [
            {
                **offer("119.95", url="/products/wool-sneaker?variant=9"),
                "availability": "http://schema.org/InStock",
            }
        ],
        url=sneaker,
    )
    facts = read_page(page(giesswein, SOCKS), sneaker)
    assert facts is not None and facts.title == "Wool Sneaker" and facts.offers[0].in_stock


@pytest.mark.parametrize(
    ("availability", "in_stock"),
    [
        ("https://schema.org/InStock", True),
        ("http://schema.org/InStock", True),
        ("http://www.schema.org/InStock", True),
        ("schema:InStock", True),
        ("InStock", True),
        ("https://schema.org/NotInStock", False),
        ("https://schema.org/BackInStock", False),
        ("https://shop.example/InStock", False),
        ("https://schema.org/OutOfStock", False),
        ("https://schema.org/PreOrder", False),
        ("instock", False),
        ("", False),
        (None, False),
    ],
)
def test_only_the_exact_in_stock_value_means_in_stock(
    availability: str | None, in_stock: bool
) -> None:
    facts = read_page(page(product(offers={**offer(), "availability": availability})))
    assert facts is not None and facts.offers[0].in_stock is in_stock


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
        ("User-agent: aura\nAllow: /p/\nUser-agent: *\nDisallow: /p/\n", "/p/x", False),
        ("User-agent: full\nAllow: /p/\nUser-agent: *\nDisallow: /p/\n", "/p/x", False),
        ("User-agent:\nAllow: /p/\nUser-agent: *\nDisallow: /p/\n", "/p/x", False),
        ("User-agent: fullaura\nDisallow: /p/\nUser-agent: full\nAllow: /p/\n", "/p/x", False),
        ("User-agent: FullAura/0.1\nDisallow: /p/\nUser-agent: *\nDisallow:\n", "/p/x", False),
        (
            "User-agent: fullaura\nDisallow: /a/\nUser-agent: fullaura\nDisallow: /p/\n",
            "/p/x",
            False,
        ),
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
    assert [found.url for found in index.search(["jacke"], "any", [], 15000).products] == [inside]
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


def test_a_redirected_page_is_named_by_the_address_it_ended_at() -> None:
    moved = "https://shop.example/p/neu"
    all_routes = {
        **routes(),
        JACKET: httpx2.Response(301, headers={"location": "/p/neu"}),
        moved: httpx2.Response(200, text=page(product("Jacke", url=moved), SOCKS)),
    }
    found = ShopIndex((SHOP,), serve(all_routes)).search(["jacke"], "men", [], 15000)
    assert [(product.url, product.title) for product in found.products] == [(JACKET, "Jacke")]


SOLD_OUT = page(product("Jacke", offer("50.00", "OutOfStock")))
ON_SALE = page(product("Jacke", offer("50.00")))


def listing(base: str, pages: list[str | int], word: str = "jacke") -> dict[str, httpx2.Response]:
    """A shop whose sitemap lists pages jacke-0, jacke-1 and so on, each this text or status."""
    addresses = [f"{base}/p/{word}-{i}" for i in range(len(pages))]
    sitemap = "".join(f"<url><loc>{address}</loc></url>" for address in addresses)
    answers = {
        address: httpx2.Response(body) if isinstance(body, int) else httpx2.Response(200, text=body)
        for address, body in zip(addresses, pages, strict=True)
    }
    robots = f"User-agent: *\nSitemap: {base}/sitemap.xml"
    return {
        f"{base}/robots.txt": httpx2.Response(200, text=robots),
        f"{base}/sitemap.xml": httpx2.Response(200, text=f"<urlset>{sitemap}</urlset>"),
        **answers,
    }


def page_reads(seen: list[str]) -> list[str]:
    return [url for url in seen if "/p/" in url]


def test_six_sold_out_pages_do_not_hide_the_seventh() -> None:
    seen: list[str] = []
    all_routes = listing("https://shop.example", [*[SOLD_OUT] * 6, ON_SALE])
    found = ShopIndex((SHOP,), serve(all_routes, seen)).search(["jacke"], "any", [], 15000)
    assert [product.url for product in found.products] == ["https://shop.example/p/jacke-6"]
    assert (found.read, found.failed) == (7, 0)


def test_six_pages_over_the_budget_do_not_hide_the_seventh() -> None:
    expensive = page(product("Jacke", offer("500.00")))
    all_routes = listing("https://shop.example", [*[expensive] * 6, ON_SALE])
    found = ShopIndex((SHOP,), serve(all_routes)).search(["jacke"], "any", [], 15000)
    assert [product.url for product in found.products] == ["https://shop.example/p/jacke-6"]


def test_a_search_never_walks_the_whole_shop() -> None:
    seen: list[str] = []
    all_routes = listing("https://shop.example", [SOLD_OUT] * 60)
    found = ShopIndex((SHOP,), serve(all_routes, seen)).search(["jacke"], "any", [], 15000)
    assert found.products == []
    assert len(page_reads(seen)) == MAX_PAGES_PER_SEARCH == found.read


def test_the_page_limit_holds_across_all_shops() -> None:
    seen: list[str] = []
    shops = tuple(Shop(f"Shop {i}", f"https://s{i}.example", "/p/") for i in range(3))
    all_routes = {
        key: value for shop in shops for key, value in listing(shop.base, [SOLD_OUT] * 40).items()
    }
    ShopIndex(shops, serve(all_routes, seen)).search(["jacke"], "any", [], 15000)
    reads = page_reads(seen)
    assert len(reads) == MAX_PAGES_PER_SEARCH
    # Each shop gets its turn, so none spends the limit alone.
    assert all(sum(shop.base in url for url in reads) == 6 for shop in shops)


def test_reading_stops_once_a_shop_has_enough_products() -> None:
    seen: list[str] = []
    all_routes = listing("https://shop.example", [ON_SALE] * 60)
    found = ShopIndex((SHOP,), serve(all_routes, seen)).search(["jacke"], "any", [], 15000)
    assert len(found.products) == PRODUCTS_PER_SHOP
    assert len(page_reads(seen)) == PRODUCTS_PER_SHOP


def test_a_shop_with_enough_products_leaves_the_limit_to_the_other() -> None:
    plenty = Shop("Plenty", "https://plenty.example", "/p/")
    all_routes = {
        **listing("https://plenty.example", [ON_SALE] * 40),
        **listing("https://shop.example", [*[SOLD_OUT] * 10, ON_SALE]),
    }
    found = ShopIndex((plenty, SHOP), serve(all_routes)).search(["jacke"], "any", [], 15000)
    assert "https://shop.example/p/jacke-10" in [product.url for product in found.products]


def test_the_time_limit_ends_the_search_between_portions() -> None:
    now = [0.0]
    seen: list[str] = []
    all_routes = listing("https://shop.example", [SOLD_OUT] * 60)

    def answer(request: httpx2.Request) -> httpx2.Response:
        if "/p/" in str(request.url):
            seen.append(str(request.url))
            now[0] += SEARCH_BUDGET_S
        return all_routes[str(request.url)]

    client = httpx2.Client(transport=httpx2.MockTransport(answer))
    found = ShopIndex((SHOP,), client, clock=lambda: now[0]).search(["jacke"], "any", [], 15000)
    assert found.products == []
    assert len(seen) == READERS


def test_a_shop_that_keeps_failing_is_not_read_further() -> None:
    now = [0.0]
    seen: list[str] = []
    all_routes = listing("https://shop.example", [503] * 40)
    index = ShopIndex((SHOP,), serve(all_routes, seen), clock=lambda: now[0])
    assert index.pages_down() is False
    found = index.search(["jacke"], "any", [], 15000)
    assert (found.products, found.read) == ([], 0)
    assert found.failed == len(page_reads(seen))
    assert FAILURES_TO_STOP <= found.failed <= FAILURES_TO_STOP + READERS
    assert index.pages_down() is True
    now[0] += RETRY_FAILED_PAGES_S + 1
    assert index.pages_down() is False


def test_one_failing_shop_leaves_the_other_shop_to_be_read() -> None:
    down = Shop("Down", "https://down.example", "/p/")
    all_routes = {
        **listing("https://down.example", [503] * 20),
        **listing("https://shop.example", [*[SOLD_OUT] * 6, ON_SALE]),
    }
    index = ShopIndex((down, SHOP), serve(all_routes))
    found = index.search(["jacke"], "any", [], 15000)
    assert [product.url for product in found.products] == ["https://shop.example/p/jacke-6"]
    assert found.failed >= FAILURES_TO_STOP and found.read == 7
    assert index.pages_down() is False


def test_a_failing_shop_does_not_stop_searches_that_other_shops_can_answer() -> None:
    now = [0.0]
    seen: list[str] = []
    down = Shop("Down", "https://down.example", "/p/")
    shoe = page(product("Schuh", offer("50.00")))
    all_routes = {
        **listing("https://down.example", [503] * 20),
        **listing("https://shop.example", [shoe], word="schuh"),
    }
    index = ShopIndex((down, SHOP), serve(all_routes, seen), clock=lambda: now[0])

    jackets = index.search(["jacke"], "any", [], 15000)
    assert (jackets.products, jackets.read) == ([], 0) and jackets.failed >= FAILURES_TO_STOP
    assert index.pages_down() is False

    shoes = index.search(["schuh"], "any", [], 15000)
    assert [found.url for found in shoes.products] == ["https://shop.example/p/schuh-0"]
    assert shoes.failed == 0  # the failing shop lists no such address, so nothing was missed

    # The failing shop is left alone meanwhile, and what that misses is not "nothing on sale".
    seen.clear()
    again = index.search(["jacke"], "any", [], 15000)
    assert (again.products, again.read, again.failed) == ([], 0, 1)
    assert page_reads(seen) == []

    now[0] += RETRY_FAILED_PAGES_S + 1
    index.search(["jacke"], "any", [], 15000)
    assert page_reads(seen) != []


def test_only_every_shop_failing_means_the_pages_are_down() -> None:
    other = Shop("Other", "https://other.example", "/p/")
    all_routes = {
        **listing("https://shop.example", [503] * 5),
        **listing("https://other.example", [503] * 5),
    }
    index = ShopIndex((SHOP, other), serve(all_routes))
    index.search(["jacke"], "any", [], 15000)
    assert index.pages_down() is True


def test_a_few_bad_pages_do_not_mark_their_shop() -> None:
    all_routes = listing("https://shop.example", [503] * (FAILURES_TO_STOP - 1))
    index = ShopIndex((SHOP,), serve(all_routes))
    found = index.search(["jacke"], "any", [], 15000)
    assert (found.read, found.failed) == (0, FAILURES_TO_STOP - 1)
    assert index.pages_down() is False


def test_a_shop_that_answers_again_is_read_again() -> None:
    now = [0.0]
    all_routes = listing("https://shop.example", [503] * FAILURES_TO_STOP)
    index = ShopIndex((SHOP,), serve(all_routes), clock=lambda: now[0])
    index.search(["jacke"], "any", [], 15000)
    assert index.pages_down() is True
    for address in list(all_routes):
        if "/p/" in address:
            all_routes[address] = httpx2.Response(200, text=ON_SALE)
    now[0] += RETRY_FAILED_PAGES_S + 1
    assert index.search(["jacke"], "any", [], 15000).read == FAILURES_TO_STOP
    assert index.pages_down() is False


def test_a_search_that_finds_no_address_says_nothing_about_the_pages() -> None:
    index = ShopIndex((SHOP,), serve(routes()))
    found = index.search(["mantel"], "any", [], 15000)
    assert (found.products, found.read, found.failed) == ([], 0, 0)
    assert index.pages_down() is False


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


def test_a_shop_owns_no_other_port_and_no_address_with_credentials() -> None:
    assert SHOP.owns("https://shop.example/p/jacket")
    assert SHOP.owns("https://shop.example:443/p/jacket")
    assert not SHOP.owns("https://shop.example:8443/p/jacket")
    assert not SHOP.owns("https://shop.example:0/p/jacket")
    assert not SHOP.owns("https://user:secret@shop.example/p/jacket")
    nondefault = Shop("Local test", "https://shop.example:8443", "/p/")
    assert nondefault.owns("https://shop.example:8443/p/jacket")
    assert not nondefault.owns("https://shop.example/p/jacket")


@pytest.mark.parametrize(
    ("written", "address"),
    [
        ("/p/jacke?color=blue&amp;size=M", "/p/jacke?color=blue&size=M"),
        ("/p/jacke?color=&quot;blue&quot;", '/p/jacke?color="blue"'),
        # Not valid XML, but read as it stands, and HTML's escapes would make this ¤cy=.
        ("/p/jacke?color=blue&currency=EUR", "/p/jacke?color=blue&currency=EUR"),
    ],
)
def test_a_sitemap_reads_only_xml_escapes_in_a_product_address(written: str, address: str) -> None:
    all_routes = {
        "https://shop.example/robots.txt": httpx2.Response(200, text="User-agent: *\n"),
        "https://shop.example/sitemap.xml": httpx2.Response(
            200, text=f"<urlset><url><loc>https://shop.example{written}</loc></url></urlset>"
        ),
    }
    _, addresses = ShopIndex((SHOP,), serve(all_routes))._load_shop(SHOP)
    assert addresses == [f"https://shop.example{address}"]


def test_a_sitemap_index_follows_an_escaped_link_to_its_file() -> None:
    all_routes = {
        "https://shop.example/robots.txt": httpx2.Response(200, text="User-agent: *\n"),
        "https://shop.example/sitemap.xml": httpx2.Response(
            200,
            text=(
                "<sitemapindex><sitemap><loc>"
                "https://shop.example/sitemap_products_1.xml?from=1&amp;to=2"
                "</loc></sitemap></sitemapindex>"
            ),
        ),
        "https://shop.example/sitemap_products_1.xml?from=1&to=2": httpx2.Response(
            200, text="<urlset><url><loc>https://shop.example/p/jacke</loc></url></urlset>"
        ),
    }
    assert ShopIndex((SHOP,), serve(all_routes)).load() == 1


def test_a_truncated_gzip_sitemap_is_refused() -> None:
    xml = b"<urlset><url><loc>https://shop.example/p/jacke</loc></url></urlset>"
    all_routes = {
        "https://shop.example/robots.txt": httpx2.Response(200, text="User-agent: *\n"),
        "https://shop.example/sitemap.xml": httpx2.Response(200, content=gzip.compress(xml)[:-8]),
    }
    assert ShopIndex((SHOP,), serve(all_routes)).load() == 0
