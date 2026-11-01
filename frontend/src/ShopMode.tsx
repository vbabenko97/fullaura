import { useRef, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { errorMessage, newOperationId, parseFindLine, postLines } from "./api";
import { formatMoney } from "./format";
import { TryOnPanel } from "./TryOnPanel";
import type { FindResponse, FindScope, Session } from "./types";

const SCOPES: { value: FindScope; label: string }[] = [
  { value: "look", label: "Whole outfit" },
  { value: "upper_body", label: "Upper body" },
  { value: "item", label: "One item" },
];
const LINES = [
  { value: "men", label: "Men's" },
  { value: "women", label: "Women's" },
  { value: "any", label: "Either" },
] as const;
const SLOT_ORDER = ["top", "mid_layer", "outer_layer", "bottom", "one_piece", "footwear", "accessory"];
const SLOT_LABELS: Record<string, string> = {
  top: "Top",
  mid_layer: "Mid layer",
  outer_layer: "Outer layer",
  bottom: "Bottom",
  one_piece: "One-piece",
  footwear: "Footwear",
  accessory: "Accessory",
};

interface ShopModeProps {
  session: Session;
  closedText: string | null;
  remaining: number;
  onConsent: (consent: boolean) => Promise<string | null>;
  onFinished: () => void;
  /** Hidden while the catalog is shown; only the panel on view may hold the try-on's fields. */
  visible: boolean;
  children: ReactNode;
}

/** Say what you need: the agent chooses among real products that code read from the shops' pages. */
export function ShopMode({ session, closedText, remaining, onConsent, onFinished, visible, children }: ShopModeProps) {
  const [request, setRequest] = useState("");
  const [scope, setScope] = useState<FindScope>("look");
  const [line, setLine] = useState<(typeof LINES)[number]["value"]>("any");
  const [sizes, setSizes] = useState("");
  const [budget, setBudget] = useState("");
  const [running, setRunning] = useState(false);
  const [steps, setSteps] = useState<string[]>([]);
  const [result, setResult] = useState<FindResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);

  const canSubmit = session.find_open && !running;
  const unchecked = result?.finds.filter((find) => !find.size_checked).length ?? 0;

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canSubmit) return;
    const budgetCents = Math.round(Number(budget) * 100);
    if (!Number.isSafeInteger(budgetCents) || budgetCents <= 0) {
      setError("Enter a budget greater than zero.");
      return;
    }
    if (!request.trim()) {
      setError("Say what you need.");
      return;
    }
    setRunning(true);
    setSteps([]);
    setResult(null);
    setError(null);
    // The form can be long, so the steps may be out of view from its button.
    requestAnimationFrame(() => heading.current?.scrollIntoView?.({ block: "start" }));
    try {
      let answer: FindResponse | null = null;
      await postLines(
        "/api/finds",
        {
          request: request.trim(),
          scope,
          line,
          sizes: sizes.split(",").map((size) => size.trim()).filter(Boolean).slice(0, 6),
          budget_cents: budgetCents,
        },
        { "X-Operation-Id": newOperationId() },
        (value) => {
          const parsed = parseFindLine(value);
          if (parsed.kind === "step") setSteps((current) => [...current, parsed.step]);
          else answer = parsed.result;
        },
      );
      if (answer) setResult(answer);
      else setError("The search ended without a result. Try again.");
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setRunning(false);
      onFinished();
      requestAnimationFrame(() => {
        if (document.activeElement === document.body) heading.current?.focus();
        const top = heading.current?.getBoundingClientRect().top ?? 0;
        if (top < 0 || top > window.innerHeight) heading.current?.scrollIntoView?.({ block: "start" });
      });
    }
  }

  return (
    <div className="shop-mode">
      <div className="workspace-heading no-print">
        <div>
          <h1>Say what you need. Get real products.</h1>
          <p className="page-description">An agent looks through real shops, and code checks every price and stock on the shop's own page.</p>
        </div>
      </div>
      <div className="workspace-grid">
        <section className="preferences-panel no-print" aria-labelledby="shop-form-title">
          <div className="panel-heading">
            <h2 id="shop-form-title">What you need</h2>
          </div>
          <form className="preferences-form" onSubmit={(event) => void submit(event)}>
            <div className="form-field">
              <label htmlFor="shop-request">What do you need?</label>
              <textarea
                autoComplete="off"
                id="shop-request"
                maxLength={300}
                onChange={(event) => setRequest(event.target.value)}
                placeholder="For example, a warm jacket for walking in winter"
                required
                rows={3}
                value={request}
              />
            </div>
            <fieldset className="budget-mode choice-row">
              <legend>How much of an outfit</legend>
              {SCOPES.map((option) => (
                <label className="radio-choice" key={option.value}>
                  <input checked={scope === option.value} name="shop-scope" onChange={() => setScope(option.value)} type="radio" />
                  <span>{option.label}</span>
                </label>
              ))}
            </fieldset>
            <fieldset className="budget-mode choice-row">
              <legend>Clothing line</legend>
              {LINES.map((option) => (
                <label className="radio-choice" key={option.value}>
                  <input checked={line === option.value} name="shop-line" onChange={() => setLine(option.value)} type="radio" />
                  <span>{option.label}</span>
                </label>
              ))}
            </fieldset>
            <div className="field-pair">
              <div className="form-field">
                <label htmlFor="shop-budget">Spend up to (EUR)</label>
                <input
                  id="shop-budget"
                  inputMode="decimal"
                  min="0.01"
                  onChange={(event) => setBudget(event.target.value)}
                  required
                  step="0.01"
                  type="number"
                  value={budget}
                />
              </div>
              <div className="form-field">
                <label htmlFor="shop-sizes">Your sizes (optional)</label>
                <input
                  autoComplete="off"
                  id="shop-sizes"
                  maxLength={60}
                  onChange={(event) => setSizes(event.target.value)}
                  placeholder="L, 32/32, 43"
                  type="text"
                  value={sizes}
                />
              </div>
            </div>
            <div className="form-submit">
              <button aria-busy={running} className={`primary-button${running ? " is-loading" : ""}`} disabled={!canSubmit} type="submit">
                {running ? "Looking…" : "Find it"}
              </button>
              {closedText ? (
                <p className="submit-help" role="status">{closedText}</p>
              ) : (
                <p className="submit-help">Uses one of {remaining} requests left for this invite.</p>
              )}
            </div>
          </form>
        </section>

        <section className="results-panel" aria-labelledby="find-title">
          <div className="results-heading">
            <div>
              <h2 id="find-title" ref={heading} tabIndex={-1}>What the agent found</h2>
            </div>
            {result && result.finds.length > 0 && (
              <p className="plan-total">
                <span>Items total</span>
                <strong>{formatMoney(result.total_cents)}</strong>
                <span>Your budget {formatMoney(result.budget_cents)}</span>
              </p>
            )}
          </div>

          {running && (
            <div className="agent-steps" role="status">
              <p className="agent-working"><span aria-hidden="true" className="spinner" /> Working. This usually takes under a minute.</p>
              <ol>
                {steps.map((step, index) => <li key={`${index}-${step}`}>{step}</li>)}
              </ol>
            </div>
          )}

          {error && <p className="request-message is-error" role="alert">{error}</p>}

          {result && result.finds.length === 0 && (
            <p className="request-message is-error" role="alert">
              {result.message ?? "Nothing suitable was found."} This request still counts against your invite.
            </p>
          )}

          {result && result.finds.length > 0 && (
            <>
              <p className="request-message is-success" role="status">Code read every price and stock below from the shop's page just now.</p>
              {unchecked > 0 && (
                <p className="plan-unchecked">
                  Size not checked for {unchecked === result.finds.length ? "these items" : `${unchecked} item${unchecked === 1 ? "" : "s"}`}: the shop's page does not list sizes. Check yours there before you buy.
                </p>
              )}
              <ul className="find-list">
                {[...result.finds]
                  .sort((left, right) => SLOT_ORDER.indexOf(left.slot) - SLOT_ORDER.indexOf(right.slot))
                  .map((find) => (
                  <li className="look-card find-card" key={find.url}>
                    <p className="find-slot">{SLOT_LABELS[find.slot] ?? find.slot}</p>
                    <h3>{find.title}</h3>
                    <p className="find-price">
                      <strong>{formatMoney(find.price_cents)}</strong>{" "}
                      <a href={find.url} rel="noreferrer" target="_blank">{find.shop}</a>
                    </p>
                    {find.why && <p className="item-facts">{find.why}</p>}
                    {find.material && <p className="item-facts">{find.material}</p>}
                  </li>
                ))}
              </ul>
              {visible && result.find_token && (
                <TryOnPanel
                  key="find-try-on"
                  onConsent={onConsent}
                  onFinished={onFinished}
                  session={session}
                  target={{ kind: "find", token: result.find_token }}
                />
              )}
            </>
          )}

          {!running && !result && !error && (
            <div className="empty-state">
              <h3>The products will appear here</h3>
              <p>Real products that ship to Austria, each with a link to its shop.</p>
            </div>
          )}
          {children}
        </section>
      </div>
    </div>
  );
}
