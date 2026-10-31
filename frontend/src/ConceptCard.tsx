import { compositionText, formatCheckedDate, formatMoney, labelSlot, variantFacts } from "./format";
import { catalogItem } from "./plan";
import type { Catalog, Concept, ConceptItem, Shoppable } from "./types";

interface ConceptCardProps {
  catalog: Catalog;
  concept: Concept;
  shoppable: Shoppable;
  matchingFailed: boolean;
}

/** The concept's own words for an item: its color and kind, then material, fit and details. */
function describe(item: ConceptItem): { name: string; facts: string } {
  const name = `${item.color} ${item.kind}`;
  return {
    name: name.charAt(0).toUpperCase() + name.slice(1),
    facts: [item.material, item.fit && `${item.fit} fit`, item.details].filter(Boolean).join(", "),
  };
}

/** The catalog's facts for the product a match names, from the catalog this page loaded. */
function MatchedProduct({ catalog, variantId, offerId }: { catalog: Catalog; variantId: string; offerId: string }) {
  const entry = catalogItem(catalog, variantId);
  if (!entry) return <p className="item-facts">Catalog item unavailable</p>;
  const offer = entry.variant.offers.find((candidate) => candidate.id === offerId) ?? null;
  const facts = variantFacts(entry.product, entry.variant);
  const offerLink = offer && /^https:\/\//i.test(offer.url) ? offer.url : null;
  return (
    <div className="concept-product">
      <p className="concept-product-title">{entry.product.title}</p>
      {facts && <p className="item-facts">{facts}</p>}
      <p className="item-facts">{compositionText(entry.variant)}</p>
      {offer?.stock === null && <p className="item-facts">Stock not reported</p>}
      <div className="item-source">
        <span className="item-price">{formatMoney(offer?.price_cents ?? null)}</span>
        {offerLink ? (
          <a href={offerLink} rel="noreferrer" target="_blank">
            {offer?.retailer ?? "Source"}
          </a>
        ) : (
          <span>Source unavailable</span>
        )}
      </div>
      {offer && <p className="source-date">{formatCheckedDate(offer.source.checked_at)}</p>}
    </div>
  );
}

/** One Design my look concept: a description, with the closest real product code found for each
 * item. An alternative is never shown as the item itself. */
export function ConceptCard({ catalog, concept, shoppable, matchingFailed }: ConceptCardProps) {
  return (
    <article aria-labelledby="concept-name" className="look-card concept-card">
      <p className="concept-banner">
        A concept, not a product list. For each item FullAura looked for the closest real product it can recommend:
        a close match, whose kind and color, and the fit and material the concept names, the catalog confirms, but
        which is still not the garment drawn; an alternative, which differs or which the catalog does not confirm;
        or no close match.
      </p>
      <h3 id="concept-name">{concept.name}</h3>
      {concept.why && <p className="concept-why">{concept.why}</p>}
      <ul className="look-items">
        {concept.items.map((item) => {
          const { name, facts } = describe(item);
          const { match } = item;
          return (
            <li className="look-item" key={item.slot}>
              <span className="slot-name">{labelSlot(item.slot)}</span>
              <h4>{name}</h4>
              {facts && <p className="item-facts">{facts}</p>}
              {/* After a failed search, "no close match" would claim more than code checked. */}
              {matchingFailed ? null : match.status === "none" ? (
                <p className="match-status">No close match in our catalog</p>
              ) : (
                <>
                  {match.status === "close" ? (
                    <p className="match-status">Close match</p>
                  ) : (
                    <>
                      <p className="match-status">Alternative, not a confirmed match:</p>
                      <ul className="match-differences">
                        {match.differences.map((difference) => (
                          <li key={difference}>{difference}</li>
                        ))}
                      </ul>
                    </>
                  )}
                  <MatchedProduct catalog={catalog} offerId={match.offer_id} variantId={match.variant_id} />
                </>
              )}
            </li>
          );
        })}
      </ul>
      <div className="concept-summary">
        {matchingFailed ? (
          <p>The product search did not finish, so no products are shown.</p>
        ) : shoppable.complete && shoppable.passes_hard_checks ? (
          <p>
            Every item has a close match, and these products pass the budget, size, shoe, material, delivery and
            stock checks.
          </p>
        ) : shoppable.complete ? (
          <>
            <p>Every item has a close match, but:</p>
            <ul>
              {shoppable.violations.map((violation) => (
                <li key={violation}>{violation}</li>
              ))}
            </ul>
          </>
        ) : concept.items.every((item) => item.match.status === "none") ? (
          <p>No item has a close match or an alternative in our catalog.</p>
        ) : (
          <>
            <p>
              Close matches and alternatives together: {formatMoney(shoppable.matched_total_cents)}, each product
              once. Not every item can be bought as shown.
            </p>
            {/* The hard checks and the closeness to the concept are separate answers. */}
            {shoppable.passes_hard_checks ? (
              <p>
                These products together pass the budget, size, shoe, material, delivery and stock checks, but they
                differ from the concept as listed above.
              </p>
            ) : shoppable.violations.length > 0 ? (
              <>
                <p>These products together fail:</p>
                <ul>
                  {shoppable.violations.map((violation) => (
                    <li key={violation}>{violation}</li>
                  ))}
                </ul>
              </>
            ) : null}
          </>
        )}
      </div>
    </article>
  );
}
