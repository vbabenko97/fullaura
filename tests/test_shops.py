"""Reading real shops' pages: what code takes as fact, and what it refuses to assume."""

from __future__ import annotations

import gzip
import json

import httpx2

from backend.app.shops import AGENT, Shop, ShopIndex, buyable, read_page


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
    found = index.search(["jacke"], "men", [], 15000)
    assert [product.url for product in found] == [JACKET]
    # Robots.txt excludes one address, so it is never read, and a page of another line is not read.
    assert HIDDEN not in seen and WOMEN not in seen and CATEGORY not in seen
    assert index.search(["jacke"], "men", [], 10000) == []  # over the budget
    assert [product.url for product in index.search(["jacke"], "women", [], 15000)] == [WOMEN]
    assert index.search(["ab"], "men", [], 15000) == []  # too short to mean anything


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
    assert [found.url for found in index.search(["jacke"], "women", [], 15000)] == [blumen]


def test_a_shop_that_does_not_answer_leaves_the_others_working() -> None:
    down = Shop("Down Shop", "https://down.example", "/p/")

    def answer(request: httpx2.Request) -> httpx2.Response:
        if "down.example" in str(request.url):
            raise httpx2.ConnectError("no route")
        return routes().get(str(request.url), httpx2.Response(404))

    client = httpx2.Client(transport=httpx2.MockTransport(answer), follow_redirects=True)
    index = ShopIndex((down, SHOP), client)
    assert [product.url for product in index.search(["jacke"], "men", [], 15000)] == [JACKET]


def test_the_user_agent_is_named_for_robots_rules() -> None:
    assert AGENT.startswith("FullAura/")
