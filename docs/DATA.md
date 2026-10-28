# Data and demo assets

No personal photographs or generated personal previews are included in this repository. `backend/data/catalog.json` holds product facts, without images, from the sources in the register below. The landing page uses local system fonts and CSS, with no third-party imagery.

The tests use `tests/fixtures/synthetic_catalog.json`, a catalog invented for testing and marked `"synthetic": true`. Its products, prices, retailer and links are fictional, and its links use the reserved test domain `synthetic.example`. It is not a catalog source: it stays out of the container image and must not appear in the interface or a demo as real products.

Keep private inputs and outputs in ignored `data/private/` while testing. Obtain the subject's permission for model processing separately from permission to publish a demo image.

## Photo handling for upload support

This accepted policy is defined by [D-31](DECISIONS.md#d-31-no-persistent-server-copies-of-personal-photos). The application has no photo upload or server storage yet.

The upload implementation will remove JPEG metadata before sending a photo to a provider. It will reject a JPEG whose visible orientation depends on EXIF orientation metadata, because removing that tag without rotating the encoded pixels would change how the photo appears. It may keep the JFIF header, ICC color profile and JPEG coding tables. It must remove Exif, XMP, IPTC, comments and data appended after the primary JPEG image.

FullAura will keep no persistent server copy of an original photograph, generated preview or provider response. During one request, it may hold the original, stripped input and preview bytes only in memory. It must not write them to logs, caches, backups or a database. The response returns the preview to the browser without server-side storage. This rule cannot establish or shorten a model provider's retention; the photo-processing notice must describe the provider's current terms separately.

The upload path must clean up in this order on success and failure:

1. Discard the original and metadata-stripped bytes after the provider request ends.
2. Discard a preview if returning it to the browser fails, image validation fails, or the provider returns an error or times out.
3. Delete every request-specific temporary file in `finally`, including partial uploads and partially written previews. Error logs may retain a request ID, error class and timing, but never image bytes, image URLs or a full personal profile.

The intended implementation uses memory only. If a future implementation requires temporary files, it must isolate each request in a private temporary directory, delete that directory before responding, and remove leftovers from interrupted earlier processes during startup before accepting uploads. If either cleanup or the startup sweep fails, photo processing stays blocked until cleanup succeeds. Tests for that implementation must cover each success and failure path, including provider errors, timeouts, interrupted writes, cleanup failures and startup-sweep failures.

For each catalog source, record the permission or license, source URL, retrieval time, allowed image use, and any required attribution. Public accessibility alone does not establish reuse rights. Product facts and product images need different rights ([D-25](DECISIONS.md#d-25-product-facts-and-product-images-need-different-rights)). For facts such as price, composition and delivery, record what the source's terms of use allow and the date you checked them; record facts for a few products by hand rather than copying the site's catalog. For images, record the license, the feed whose terms permit display, or that FullAura took the photo. Each variant and offer must retain its source and the time its data was last checked; missing size, composition, or stock data remains unknown.

A catalog file follows the record format in `backend/app/catalog.py` and lives under `backend/`, because the container image leaves out `data/`. Every field is present, and a value the source does not give is written as `null`. Keep the composition as the source shows it, word for word, in `source_text`. Name each component's material from the list in `backend/app/catalog.py`, which also accepts other common names such as PU; a file that uses any other material name is refused. Mark a composition `complete` only when the source names the material of every labelled part, each from the list ([D-27](DECISIONS.md#d-27-a-composition-is-complete-when-it-names-every-labelled-part)); a labelled part that is missing or named only vaguely, such as "Synthetik", makes it `partial`. When the source names a trim's material, such as metal buttons, record it as a component; a trim named vaguely, such as a zip "aus Plastik", stays in `source_text` only. Where the source gives percentages for every component of a part, they must sum to 100 ([D-23](DECISIONS.md#d-23-materials-are-compared-by-names-from-a-fixed-list)).

Add public demo assets only after completing this register:

| Asset or dataset | Source URL | Permission or license | Allowed image use | Attribution | Retrieved |
| --- | --- | --- | --- | --- | --- |
| Product facts for 11 Armedangels variants in `backend/data/catalog.json` | <https://www.armedangels.com/de-at/> | Facts only. The [terms of use](https://www.armedangels.com/at-de/agb), checked on 2026-10-28, have no clause on using site content, and robots.txt allows crawlers on product pages. | None; no images are used | None required | 2026-10-28 |
| Product facts for 4 Giesswein footwear variants in `backend/data/catalog.json` | <https://www.giesswein.com/> | Facts only. The [terms of use](https://www.giesswein.com/pages/agb) and the [imprint](https://www.giesswein.com/pages/impressum), checked on 2026-10-28, have no clause on using site content, and robots.txt allows crawlers on product pages. | None; no images are used | None required | 2026-10-28 |

Model output shown in a recording should include enough run metadata to reproduce the process without exposing a person's original photographs or credentials. Label cached examples and fixtures in the UI and demo.
