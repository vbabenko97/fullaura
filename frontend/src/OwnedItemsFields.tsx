import { useEffect, useRef } from "react";
import { labelCategory } from "./format";
import { CATEGORIES } from "./types";
import type { OwnedItem } from "./types";

interface OwnedItemsFieldsProps {
  ownedItems: OwnedItem[];
  onAddItem: () => void;
  onUpdateItem: (id: string, field: keyof OwnedItem, value: string) => void;
  onRemoveItem: (id: string) => void;
}

export function OwnedItemsFields({
  ownedItems,
  onAddItem,
  onUpdateItem,
  onRemoveItem,
}: OwnedItemsFieldsProps) {
  // A removed item takes its Remove button with it, so the keyboard goes on from "Add item".
  const addButton = useRef<HTMLButtonElement>(null);
  const removed = useRef(false);
  useEffect(() => {
    if (!removed.current) return;
    removed.current = false;
    if (document.activeElement === document.body || document.activeElement === null) addButton.current?.focus();
  }, [ownedItems.length]);

  return (
    <section className="owned-items" aria-labelledby="owned-title">
      <div className="owned-heading">
        <div>
          <h3 id="owned-title">Items you already own</h3>
          <p className="field-help">Optional. Add up to three by description.</p>
        </div>
        <button
          className="secondary-button"
          disabled={ownedItems.length >= 3}
          onClick={onAddItem}
          ref={addButton}
          type="button"
        >
          Add item
        </button>
      </div>

      {ownedItems.map((item, index) => (
        <fieldset className="owned-entry" key={item.id}>
          <legend>Item {index + 1}</legend>
          <button
            aria-label={`Remove item ${index + 1}`}
            className="remove-button"
            onClick={() => {
              removed.current = true;
              onRemoveItem(item.id);
            }}
            type="button"
          >
            Remove
          </button>
          <label htmlFor={`${item.id}-category`}>Category</label>
          <select
            id={`${item.id}-category`}
            onChange={(event) => onUpdateItem(item.id, "category", event.target.value)}
            value={item.category}
          >
            {CATEGORIES.map((category) => (
              <option key={category} value={category}>{labelCategory(category)}</option>
            ))}
          </select>
          <label htmlFor={`${item.id}-color`}>Color</label>
          <input
            id={`${item.id}-color`}
            maxLength={40}
            onChange={(event) => onUpdateItem(item.id, "color", event.target.value)}
            required
            value={item.color}
          />
          <label htmlFor={`${item.id}-description`}>Description</label>
          <input
            id={`${item.id}-description`}
            maxLength={200}
            onChange={(event) => onUpdateItem(item.id, "description", event.target.value)}
            required
            value={item.description}
          />
        </fieldset>
      ))}
    </section>
  );
}
