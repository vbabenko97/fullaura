import { useEffect, useRef, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { ApiResponseError, collectFind, errorMessage, newOperationId, parseFindLine, postLines } from "./api";
import { keepLastSearch, loadLastSearch } from "./findStorage";
import type { FindLine, SearchForm } from "./findStorage";
import { formatMoney } from "./format";
import { TryOnPanel } from "./TryOnPanel";
import type { FindResponse, FindScope, Session } from "./types";

const COLLECT_EVERY_MS = 3000;
// The server counts a search as interrupted after this long, so its answer will not come later.
const COLLECT_FOR_MS = 300_000;
const COLLECTING = "Your search kept running during the reload. Waiting for its answer.";

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
  children: ReactNode;
}

/** Say what you need: the agent chooses among real products that code read from the shops' pages. */
export function ShopMode({ session, closedText, remaining, onConsent, onFinished, children }: ShopModeProps) {
  // A reload brings back the tab's last search; one that was still running is collected below.
  const [remembered] = useState(() => loadLastSearch(session.invite_scope));
  const collecting = remembered !== null && remembered.operation_id !== null && remembered.result === null;
  const [request, setRequest] = useState(remembered?.form.request ?? "");
  const [scope, setScope] = useState<FindScope>(remembered?.form.scope ?? "look");
  const [line, setLine] = useState<FindLine>(remembered?.form.line ?? "any");
  const [sizes, setSizes] = useState(remembered?.form.sizes ?? "");
  const [budget, setBudget] = useState(remembered?.form.budget ?? "");
  const [running, setRunning] = useState(collecting);
  const [steps, setSteps] = useState<string[]>(collecting ? [COLLECTING] : []);
  const [result, setResult] = useState<FindResponse | null>(remembered?.result ?? null);
  const [error, setError] = useState<string | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);

  const canSubmit = session.find_open && !running;
  const unchecked = result?.finds.filter((find) => !find.size_checked).length ?? 0;
  const kindUnchecked = result?.finds.filter((find) => !find.slot_checked).length ?? 0;

  function settled() {
    setRunning(false);
    onFinished();
    requestAnimationFrame(() => {
      if (document.activeElement === document.body) heading.current?.focus();
      const top = heading.current?.getBoundingClientRect().top ?? 0;
      if (top < 0 || top > window.innerHeight) heading.current?.scrollIntoView?.({ block: "start" });
    });
  }

  // Asking where a search stands is free and starts nothing, so a reload never pays for another.
  useEffect(() => {
    if (remembered === null || remembered.operation_id === null || remembered.result !== null) return;
    const { form, operation_id: operationId } = remembered;
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const giveUpAt = Date.now() + COLLECT_FOR_MS;
    const end = (answer: FindResponse | null, message: string | null) => {
      keepLastSearch({ form, operation_id: null, result: answer }, session.invite_scope);
      setResult(answer);
      setError(message);
      setSteps([]);
      settled();
    };
    const ask = async () => {
      try {
        const answer = await collectFind(operationId);
        if (!active) return;
        if (answer) {
          end(answer, null);
          return;
        }
      } catch (failure) {
        if (!active) return;
        if (failure instanceof ApiResponseError) {
          end(null, errorMessage(failure));
          return;
        }
      }
      if (Date.now() >= giveUpAt) {
        end(null, errorMessage(new ApiResponseError(404, null, "find_unknown")));
        return;
      }
      timer = setTimeout(() => void ask(), COLLECT_EVERY_MS);
    };
    void ask();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [remembered]);

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
    const wantedSizes = sizes.split(",").map((size) => size.trim()).filter(Boolean);
    if (wantedSizes.length > 6 || wantedSizes.some((size) => size.length > 12)) {
      setError("Enter up to six sizes, each of at most 12 characters.");
      return;
    }
    setRunning(true);
    setSteps([]);
    setError(null);
    // The form can be long, so the steps may be out of view from its button.
    requestAnimationFrame(() => heading.current?.scrollIntoView?.({ block: "start" }));
    const form: SearchForm = { request: request.trim(), scope, line, sizes, budget };
    const operationId = newOperationId();
    try {
      let answer: FindResponse | null = null;
      let accepted = false;
      await postLines(
        "/api/finds",
        {
          request: form.request,
          scope,
          line,
          sizes: wantedSizes,
          budget_cents: budgetCents,
        },
        { "X-Operation-Id": operationId },
        (value) => {
          // The earlier results stay until the server accepts this search: a refusal, such as a
          // try-on of them still running, must not take them or that try-on away.
          if (!accepted) {
            accepted = true;
            setResult(null);
            keepLastSearch({ form, operation_id: operationId, result: null }, session.invite_scope);
          }
          const parsed = parseFindLine(value);
          if (parsed.kind === "step") setSteps((current) => [...current, parsed.step]);
          else answer = parsed.result;
        },
      );
      if (answer) setResult(answer);
      else setError("The search ended without a result. Try again.");
      keepLastSearch({ form, operation_id: null, result: answer }, session.invite_scope);
    } catch (failure) {
      // A lost connection leaves the kept search open, so a reload can still collect its answer.
      setError(errorMessage(failure));
    } finally {
      settled();
    }
  }

  return (
    <div className="shop-mode">
      <div className="workspace-heading no-print">
        <div>
          <h1>Say what you need. Get real products.</h1>
          <p className="page-description">An agent searches Tom Tailor, Tchibo and Giesswein by words in their product addresses. Code reads each price and stock on the shop's own page and checks the links and your budget. Warmth, whether items go together and style are the agent's judgment, which code cannot check.</p>
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
              {scope === "upper_body" && (
                <p className="field-help">Upper body limits what the agent chooses. A try-on still needs a full-body photo and draws you from head to toe.</p>
              )}
            </fieldset>
            <fieldset className="budget-mode choice-row">
              <legend>Clothing line</legend>
              {LINES.map((option) => (
                <label className="radio-choice" key={option.value}>
                  <input checked={line === option.value} name="shop-line" onChange={() => setLine(option.value)} type="radio" />
                  <span>{option.label}</span>
                </label>
              ))}
              <p className="field-help">Men's and Women's show only products the shop names that way, or unisex. Either also shows products that name no line, which can be of any line.</p>
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
              {result.message ?? "Nothing suitable was found."}{" "}
              {result.counted ? "This request still counts against your invite." : "This request did not count against your invite."}
            </p>
          )}

          {result && result.finds.length > 0 && (
            <>
              <p className="request-message is-success" role="status">Code read every price and stock below from the shop's own page within the hour before this search. It did not check warmth, whether the items go together or your style: the reasons below are the agent's.</p>
              {unchecked > 0 && (
                <p className="plan-unchecked">
                  Size not checked for {unchecked === result.finds.length ? "these items" : `${unchecked} item${unchecked === 1 ? "" : "s"}`}. Check yours on the shop's page before you buy.
                </p>
              )}
              {kindUnchecked > 0 && (
                <p className="plan-unchecked">
                  Kind of item not confirmed for {kindUnchecked === result.finds.length ? "these items" : `${kindUnchecked} item${kindUnchecked === 1 ? "" : "s"}`}: the shop's title does not state it, so the position shown is the agent's reading.
                </p>
              )}
              <ul className="find-list">
                {[...result.finds]
                  .sort((left, right) => SLOT_ORDER.indexOf(left.slot) - SLOT_ORDER.indexOf(right.slot))
                  .map((find) => (
                  <li className="look-card find-card" key={find.url}>
                    <p className="find-slot">{SLOT_LABELS[find.slot] ?? find.slot}{find.slot_checked ? "" : " (not confirmed)"}</p>
                    <h3>{find.title}</h3>
                    <p className="find-price">
                      <strong>{formatMoney(find.price_cents)}</strong>{" "}
                      <a href={find.url} rel="noreferrer" target="_blank">{find.shop}</a>
                    </p>
                    {find.why && <p className="item-facts">Agent's reason: {find.why}</p>}
                    {find.material && <p className="item-facts">{find.material}</p>}
                  </li>
                ))}
              </ul>
              {result.find_token && (
                <TryOnPanel
                  idPrefix="find-tryon"
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
              <p>Products from these three shops, each with a link to its page. Other shops are not searched.</p>
            </div>
          )}
          {/* Hidden, not removed, so a started answer survives the next search. */}
          <div hidden={running || (!result && !error)}>{children}</div>
        </section>
      </div>
    </div>
  );
}
