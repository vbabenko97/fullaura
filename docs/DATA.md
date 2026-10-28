# Data and demo assets

No personal photographs, generated personal previews, or product catalog that FullAura may show are included in this repository. The landing page uses local system fonts and CSS, with no third-party imagery.

The tests use `tests/fixtures/synthetic_catalog.json`, a catalog invented for testing and marked `"synthetic": true`. Its products, prices, retailer and links are fictional, and its links use the reserved test domain `synthetic.example`. It is not a catalog source: it stays out of the container image and must not appear in the interface or a demo as real products.

Keep private inputs and outputs in ignored `data/private/` while testing. Obtain the subject's permission for model processing separately from permission to publish a demo image. Remove image metadata before adding upload support, and define retention and deletion behavior.

For each catalog source, record the permission or license, source URL, retrieval time, allowed image use, and any required attribution. Public accessibility alone does not establish reuse rights. Product facts and product images need different rights ([D-25](DECISIONS.md#d-25-product-facts-and-product-images-need-different-rights)). For facts such as price, composition and delivery, record what the source's terms of use allow and the date you checked them; record facts for a few products by hand rather than copying the site's catalog. For images, record the license, the feed whose terms permit display, or that FullAura took the photo. Each variant and offer must retain its source and the time its data was last checked; missing size, composition, or stock data remains unknown.

A catalog file follows the record format in `backend/app/catalog.py` and lives under `backend/`, because the container image leaves out `data/`. Every field is present, and a value the source does not give is written as `null`. Name each material from the list in `backend/app/catalog.py`, which also accepts other common names such as PU; a file that uses any other material name is refused. When the source's words name no listed material, such as "textile/synthetic", keep them in `source_text` and mark the composition `partial`. Mark a composition `complete` only when the source lists every component of the item. Where the source gives percentages for every component of a part, they must sum to 100 ([D-23](DECISIONS.md#d-23-materials-are-compared-by-names-from-a-fixed-list)).

Add public demo assets only after completing this register:

| Asset or dataset | Source URL | Permission or license | Allowed image use | Attribution | Retrieved |
| --- | --- | --- | --- | --- | --- |

Model output shown in a recording should include enough run metadata to reproduce the process without exposing a person's original photographs or credentials. Label cached examples and fixtures in the UI and demo.
