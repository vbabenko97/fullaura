import { formatMoney, lookLabel } from "./format";
import { lookCost } from "./plan";
import { ProductLine } from "./ProductLine";
import { SLOTS } from "./types";
import type {
  Catalog,
  Fit,
  FootwearChoice,
  ItemRef,
  LockedPlacement,
  Look,
  OwnedItem,
  OwnedNotice,
  Plan,
  Slot,
} from "./types";

interface LookCardProps {
  catalog: Catalog;
  look: Look;
  plan: Plan;
  ownedItems: OwnedItem[];
  ownedNotices: OwnedNotice[];
  fit: Fit | undefined;
  footwear: FootwearChoice;
  locks: LockedPlacement[];
  lockDisabled: boolean;
  replaceDisabled: boolean;
  preferencesChanged: boolean;
  onToggleLock: (lookId: string, slot: Slot, item: ItemRef) => void;
  onReplaceItem: (lookId: string, slot: Slot, item: ItemRef) => void;
  onTryOn: ((lookId: string) => void) | null;
}

export function LookCard({
  catalog,
  look,
  plan,
  ownedItems,
  ownedNotices,
  fit,
  footwear,
  locks,
  lockDisabled,
  replaceDisabled,
  preferencesChanged,
  onToggleLock,
  onReplaceItem,
  onTryOn,
}: LookCardProps) {
  const items = SLOTS.flatMap((slot) => {
    const item = look.items[slot];
    return item ? [{ slot, item }] : [];
  });

  return (
    <article className="look-card" aria-labelledby={`${look.id}-title`}>
      <header className="look-card-header">
        <h2 id={`${look.id}-title`}>{lookLabel(look.id)}</h2>
        <p className="look-total">{formatMoney(lookCost(look, plan, catalog))}</p>
      </header>
      {onTryOn && (
        <button className="text-button no-print" onClick={() => onTryOn(look.id)} type="button">
          Try this look on
        </button>
      )}
      {preferencesChanged && (
        <p className="item-facts replace-off no-print">
          Replace is off until you select "Update three looks", because your preferences changed.
        </p>
      )}
      <ul className="look-items">
        {items.map(({ slot, item }) => {
          const isLocked = locks.some(
            (lock) =>
              lock.look_id === look.id &&
              lock.slot === slot &&
              lock.item.id === item.id &&
              lock.item.kind === item.kind,
          );
          return (
            <ProductLine
              key={`${look.id}-${slot}`}
              catalog={catalog}
              fit={fit}
              footwear={footwear}
              isLocked={isLocked}
              item={item}
              lockDisabled={lockDisabled}
              replaceDisabled={replaceDisabled}
              look={look}
              onToggleLock={onToggleLock}
              onReplaceItem={onReplaceItem}
              ownedItems={ownedItems}
              ownedNotices={ownedNotices}
              plan={plan}
              slot={slot}
            />
          );
        })}
      </ul>
    </article>
  );
}
