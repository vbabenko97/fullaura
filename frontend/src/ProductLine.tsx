import { useState } from "react";
import {
  compositionText,
  formatCheckedDate,
  formatMoney,
  labelCategory,
  labelSlot,
  lookLabel,
  sentenceCase,
  stockText,
  variantFacts,
} from "./format";
import { catalogItem, hasReplacementAlternative, selectedOffer, sizeCompared } from "./plan";
import type {
  Catalog,
  Fit,
  FootwearChoice,
  ItemRef,
  Look,
  OwnedItem,
  OwnedNotice,
  Plan,
  Slot,
  VariantImage,
} from "./types";

/** The variant's own photo, in a box sized before it loads; a failed load keeps the box. */
function ItemImage({ image }: { image: VariantImage }) {
  const [failed, setFailed] = useState(false);
  return (
    <figure className="item-image">
      {failed ? (
        <div
          aria-label={`${image.alt}: image unavailable`}
          className="item-image-missing"
          role="img"
          style={{ aspectRatio: `${image.width} / ${image.height}` }}
        >
          Image unavailable
        </div>
      ) : (
        <img
          alt={image.alt}
          decoding="async"
          height={image.height}
          loading="lazy"
          onError={() => setFailed(true)}
          referrerPolicy="no-referrer"
          src={image.url}
          width={image.width}
        />
      )}
      {image.attribution && <figcaption>{image.attribution}</figcaption>}
    </figure>
  );
}

interface ProductLineProps {
  catalog: Catalog;
  look: Look;
  plan: Plan;
  slot: Slot;
  item: ItemRef;
  ownedItems: OwnedItem[];
  ownedItemsInForm: string[];
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
  ownedItemsInForm,
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
    // Removed from the form since this plan was made, so the next update cannot send it as kept.
    const removed = !ownedItemsInForm.includes(item.id);
    return (
      <li className="look-item">
        <div className="look-item-heading">
          <span className="slot-name">{labelSlot(slot)}</span>
          <div className="look-item-actions">
            <button
              aria-label={`Replace ${owned?.description ?? "owned item"} in ${lookLabel(look.id)}`}
              className="secondary-button replace-button"
              disabled={replaceDisabled || isLocked || !hasAlternative}
              onClick={() => onReplaceItem(look.id, slot, item)}
              type="button"
            >
              Replace
            </button>
            <button
              aria-label={`${isLocked ? "Release" : "Keep"} ${owned?.description ?? "owned item"} in ${lookLabel(look.id)}`}
              aria-pressed={isLocked}
              className={`lock-button${isLocked ? " is-locked" : ""}`}
              disabled={lockDisabled || (removed && !isLocked)}
              onClick={() => onToggleLock(look.id, slot, item)}
              type="button"
            >
              {isLocked ? "Kept" : "Keep item"}
            </button>
          </div>
        </div>
        {!hasAlternative && <p className="item-facts">No other catalog or owned item can fill this position.</p>}
        {removed && (
          <p className="item-facts no-print">You removed this item from your items, so it cannot be kept.</p>
        )}
        <h3>{owned?.description ?? "Your item"}</h3>
        <p className="item-facts">
          {owned ? `${owned.color}, ${labelCategory(owned.category)}` : "Already owned"}
        </p>
        <p className="item-price">Already owned</p>
        {notices.map((notice) => (
          <p className="item-facts" key={`${notice.kind}-${notice.material}`}>
            {sentenceCase(notice.material)}
            {notice.kind === "owned_reported"
              ? " reported by you. It is excluded from new purchases."
              : " is unconfirmed for this item."}
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
  const composition = compositionText(entry.variant);
  const facts = variantFacts(entry.product, entry.variant);
  const offerLink = offer && /^https:\/\//i.test(offer.url) ? offer.url : null;
  const image = entry.variant.image;

  return (
    <li className="look-item">
        <div className="look-item-heading">
          <span className="slot-name">{labelSlot(slot)}</span>
        <div className="look-item-actions">
          <button
            aria-label={`Replace ${entry.product.title} in ${lookLabel(look.id)}`}
            className="secondary-button replace-button"
            disabled={replaceDisabled || isLocked || !hasAlternative}
            onClick={() => onReplaceItem(look.id, slot, item)}
            type="button"
          >
            Replace
          </button>
          <button
            aria-label={`${isLocked ? "Release" : "Keep"} ${entry.product.title} in ${lookLabel(look.id)}`}
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
      <div className={`item-body${image ? " has-image" : ""}`}>
        {image && <ItemImage image={image} key={image.url} />}
        <div className="item-details">
          <h3>{entry.product.title}</h3>
          {facts && <p className="item-facts">{facts}</p>}
          {!sizeCompared(entry.product, entry.variant, fit) && <p className="item-facts">Size not checked against yours</p>}
          <p className="item-facts">{composition}</p>
          {offer && <p className="item-facts">{stockText(offer.stock)}</p>}
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
      </div>
    </li>
  );
}
