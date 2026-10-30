import { formatCheckedDate, formatMoney, labelCategory, labelSlot } from "./format";
import { catalogItem, hasReplacementAlternative, selectedOffer } from "./plan";
import type { Catalog, Fit, FootwearChoice, ItemRef, Look, OwnedItem, OwnedNotice, Plan, Slot } from "./types";

interface ProductLineProps {
  catalog: Catalog;
  look: Look;
  plan: Plan;
  slot: Slot;
  item: ItemRef;
  ownedItems: OwnedItem[];
  ownedNotices: OwnedNotice[];
  fit: Fit | undefined;
  footwear: FootwearChoice;
  isLocked: boolean;
  lockDisabled: boolean;
  replaceDisabled: boolean;
  onToggleLock: (lookId: string, slot: Slot, item: ItemRef) => void;
  onReplaceItem: (lookId: string, slot: Slot, item: ItemRef) => void;
}

export function ProductLine({
  catalog,
  look,
  plan,
  slot,
  item,
  ownedItems,
  ownedNotices,
  fit,
  footwear,
  isLocked,
  lockDisabled,
  replaceDisabled,
  onToggleLock,
  onReplaceItem,
}: ProductLineProps) {
  const hasAlternative = hasReplacementAlternative(catalog, ownedItems, item, slot, fit, footwear);
  if (item.kind === "owned") {
    const owned = ownedItems.find((candidate) => candidate.id === item.id);
    const notices = ownedNotices.filter((notice) => notice.item.id === item.id);
    return (
      <li className="look-item">
        <div className="look-item-heading">
          <span className="slot-name">{labelSlot(slot)}</span>
          <div className="look-item-actions">
            <button
              aria-label={`Replace ${owned?.description ?? "owned item"} in ${look.id}`}
              className="secondary-button replace-button"
              disabled={replaceDisabled || isLocked || !hasAlternative}
              onClick={() => onReplaceItem(look.id, slot, item)}
              type="button"
            >
              Replace
            </button>
            <button
              aria-label={`${isLocked ? "Release" : "Keep"} ${owned?.description ?? "owned item"} in ${look.id}`}
              aria-pressed={isLocked}
              className={`lock-button${isLocked ? " is-locked" : ""}`}
              disabled={lockDisabled}
              onClick={() => onToggleLock(look.id, slot, item)}
              type="button"
            >
              {isLocked ? "Kept" : "Keep item"}
            </button>
          </div>
        </div>
        {!hasAlternative && <p className="item-facts">No other catalog or owned item can fill this position.</p>}
        <h3>{owned?.description ?? "Your item"}</h3>
        <p className="item-facts">
          {owned ? `${owned.color}, ${labelCategory(owned.category)}` : "Already owned"}
        </p>
        <p className="item-price">Already owned</p>
        {notices.map((notice) => (
          <p className="item-facts" key={`${notice.kind}-${notice.material}`}>
            {notice.kind === "owned_reported"
              ? `${notice.material} reported by you. It is excluded from new purchases.`
              : `${notice.material} is unconfirmed for this item.`}
          </p>
        ))}
      </li>
    );
  }

  const entry = catalogItem(catalog, item.id);
  if (!entry) {
    return (
      <li className="look-item">
        <span className="slot-name">{labelSlot(slot)}</span>
        <p>Catalog item unavailable</p>
      </li>
    );
  }

  const offer = selectedOffer(plan, entry.variant);
  const materials = [...new Set(entry.variant.composition.components.map((component) => component.material))];
  const composition =
    entry.variant.composition.status === "complete"
      ? `Materials: ${materials.join(", ")}`
      : entry.variant.composition.status === "partial"
        ? "Composition is incomplete"
        : entry.variant.composition.status === "conflicting"
          ? "Sources disagree on composition"
          : "Composition not reported";
  const facts = [
    entry.product.brand,
    entry.variant.color,
    entry.variant.size === null ? null : `Size ${entry.variant.size}`,
  ]
    .filter((value): value is string => value !== null)
    .join(", ");
  const offerLink = offer && /^https:\/\//i.test(offer.url) ? offer.url : null;

  return (
    <li className="look-item">
        <div className="look-item-heading">
          <span className="slot-name">{labelSlot(slot)}</span>
        <div className="look-item-actions">
          <button
            aria-label={`Replace ${entry.product.title} in ${look.id}`}
            className="secondary-button replace-button"
            disabled={replaceDisabled || isLocked || !hasAlternative}
            onClick={() => onReplaceItem(look.id, slot, item)}
            type="button"
          >
            Replace
          </button>
          <button
            aria-label={`${isLocked ? "Release" : "Keep"} ${entry.product.title} in ${look.id}`}
            aria-pressed={isLocked}
            className={`lock-button${isLocked ? " is-locked" : ""}`}
            disabled={lockDisabled}
            onClick={() => onToggleLock(look.id, slot, item)}
            type="button"
          >
            {isLocked ? "Kept" : "Keep item"}
          </button>
        </div>
      </div>
      {!hasAlternative && <p className="item-facts">No other catalog or owned item can fill this position.</p>}
      <h3>{entry.product.title}</h3>
      {facts && <p className="item-facts">{facts}</p>}
      <p className="item-facts">{composition}</p>
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
    </li>
  );
}
