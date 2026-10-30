import { useState } from "react";
import { formatCheckedDate, formatMoney } from "./format";
import { shoppingListText, shoppingTotal } from "./plan";
import type { ShoppingLine } from "./types";

interface ShoppingListProps {
  lines: ShoppingLine[];
  title?: string;
}

export function ShoppingList({ lines, title = "Shopping list" }: ShoppingListProps) {
  const [copyState, setCopyState] = useState<string | null>(null);
  const total = shoppingTotal(lines);

  async function copy() {
    try {
      await navigator.clipboard.writeText(shoppingListText(lines, formatMoney));
      setCopyState("Copied the list.");
    } catch {
      setCopyState("Copying is blocked in this browser. Select the list and copy it instead.");
    }
  }

  return (
    <section className="shopping-panel" aria-labelledby="shopping-title">
      <div className="results-heading">
        <div>
          <h2 id="shopping-title">{title}</h2>
          <p>Each product once, however many looks use it. Your own items cost nothing.</p>
        </div>
        <p className="plan-total">
          Items total
          <strong>{formatMoney(total)}</strong>
        </p>
      </div>
      <div className="table-scroll">
        <table className="shopping-table">
          <thead>
            <tr>
              <th scope="col">Item</th>
              <th scope="col">In</th>
              <th scope="col">Price</th>
              <th scope="col">Shop</th>
            </tr>
          </thead>
          <tbody>
            {lines.map((line) => (
              <tr key={line.key}>
                <td>
                  <span className="shopping-name">{line.name}</span>
                  <span className="item-facts">{line.details}</span>
                </td>
                <td>{line.looks.join(", ")}</td>
                <td className="numeric">
                  {line.kind === "owned" ? "Already owned" : formatMoney(line.price_cents)}
                </td>
                <td>
                  {line.url ? (
                    <a href={line.url} rel="noreferrer" target="_blank">{line.retailer ?? "Shop"}</a>
                  ) : line.kind === "owned" ? (
                    "None"
                  ) : (
                    "Link unavailable"
                  )}
                  {line.checked_at && <span className="source-date">{formatCheckedDate(line.checked_at)}</span>}
                  {line.kind === "catalog" && line.stock !== "in_stock" && (
                    <span className="source-date">{line.stock === "out_of_stock" ? "Out of stock" : "Stock not reported"}</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="field-help">
        Prices are the shops' item prices on the date shown. Delivery, returns and stock can differ when you buy, so this is not a final cost.
      </p>
      <div className="shopping-actions no-print">
        <button className="secondary-button" onClick={() => void copy()} type="button">Copy list</button>
        <button className="secondary-button" onClick={() => window.print()} type="button">Print</button>
        {copyState && <p className="field-help" role="status">{copyState}</p>}
      </div>
    </section>
  );
}
