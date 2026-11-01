import { useState } from "react";
import { formatMoney } from "./format";
import { shoppingTotal } from "./plan";
import { ShoppingList } from "./ShoppingList";
import type { SavedPlan } from "./types";

interface SavedPlansProps {
  saved: SavedPlan[];
  busy: boolean;
  message: string | null;
  onDelete: (id: string) => void;
  /** Left out where no plan can be saved or checked, as once the beta has closed. */
  canSave?: boolean;
  onSave?: () => void;
  onRestore?: (plan: SavedPlan) => void;
}

function savedDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "Unknown date"
    : new Intl.DateTimeFormat("en-GB", { dateStyle: "medium", timeStyle: "short" }).format(date);
}

export function SavedPlans({ saved, canSave = false, busy, message, onSave, onRestore, onDelete }: SavedPlansProps) {
  const [confirming, setConfirming] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const opened = saved.find((plan) => plan.id === open);

  return (
    <section className="saved-panel no-print" aria-labelledby="saved-title">
      <div className="saved-heading">
        <div>
          <h2 id="saved-title">Saved on this device</h2>
          <p className="field-help">Plans and shopping lists only; photos and try-ons are never saved.</p>
        </div>
        {onSave && (
          <button className="secondary-button" disabled={!canSave || busy} onClick={onSave} type="button">
            Save this plan
          </button>
        )}
      </div>
      {message && <p className="field-help" role="status">{message}</p>}
      {saved.length === 0 ? (
        <p className="field-help">Nothing saved yet. Saved plans stay in this browser until you delete them.</p>
      ) : (
        <ul className="saved-list">
          {saved.map((plan) => (
            <li className="saved-item" key={plan.id}>
              <div>
                <span className="shopping-name">{plan.name}</span>
                <span className="item-facts">
                  {savedDate(plan.saved_at)}, items {formatMoney(shoppingTotal(plan.shopping_list))}
                </span>
              </div>
              <div className="look-item-actions">
                {onRestore && (
                  <button className="secondary-button" disabled={busy} onClick={() => onRestore(plan)} type="button">
                    Restore
                  </button>
                )}
                <button
                  aria-expanded={open === plan.id}
                  className="secondary-button"
                  onClick={() => setOpen(open === plan.id ? null : plan.id)}
                  type="button"
                >
                  {open === plan.id ? "Hide list" : "Show list"}
                </button>
                {confirming === plan.id ? (
                  <button
                    className="secondary-button is-danger"
                    onClick={() => {
                      setConfirming(null);
                      if (open === plan.id) setOpen(null);
                      onDelete(plan.id);
                    }}
                    type="button"
                  >
                    Confirm delete
                  </button>
                ) : (
                  <button className="text-button" onClick={() => setConfirming(plan.id)} type="button">
                    Delete
                  </button>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
      {opened && <ShoppingList lines={opened.shopping_list} title={`Saved list: ${opened.name}`} />}
    </section>
  );
}
