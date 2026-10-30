import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import {
  errorMessage,
  parseCatalog,
  parseDemoSession,
  parsePlanResponse,
  parsePreviewStatus,
  readCategory,
  readJson,
} from "./api";
import { FitFields } from "./FitFields";
import { formatMoney } from "./format";
import { LookCard } from "./LookCard";
import { OwnedItemsFields } from "./OwnedItemsFields";
import {
  isExactReplacement,
  locksForReplacement,
  parseFit,
  preferenceKey,
  sameItem,
  totalLookCosts,
  wholePlanCost,
} from "./plan";
import { PreviewPanel, previewOptionsForPlan } from "./PreviewPanel";
import { EMPTY_FIT_DRAFT, MATERIALS } from "./types";
import type {
  BudgetMode,
  Catalog,
  DemoSession,
  Fit,
  FitDraft,
  ItemRef,
  LockedPlacement,
  Material,
  OwnedItem,
  OwnedNotice,
  Plan,
  PlanRequest,
  PreviewStatus,
  ReplacementTarget,
  RequestState,
  Slot,
} from "./types";

type OwnedItemDraft = OwnedItem;

type CatalogState = "loading" | "ready" | "error" | "disabled";
type SessionState = "loading" | "ready" | "error";
type PreviewStatusState = "loading" | "ready" | "error";

export function App() {
  const [demoSession, setDemoSession] = useState<DemoSession | null>(null);
  const [sessionState, setSessionState] = useState<SessionState>("loading");
  const [over18Checked, setOver18Checked] = useState(false);
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [catalogState, setCatalogState] = useState<CatalogState>("loading");
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [styleGoal, setStyleGoal] = useState("");
  const [preferredColors, setPreferredColors] = useState("");
  const [budgetAmount, setBudgetAmount] = useState("");
  const [budgetMode, setBudgetMode] = useState<BudgetMode>("per_look");
  const [exclusions, setExclusions] = useState<Material[]>([]);
  const [fitDraft, setFitDraft] = useState<FitDraft>(EMPTY_FIT_DRAFT);
  const [ownedItems, setOwnedItems] = useState<OwnedItemDraft[]>([]);
  const [locks, setLocks] = useState<LockedPlacement[]>([]);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [planOwnedItems, setPlanOwnedItems] = useState<OwnedItem[]>([]);
  const [planFit, setPlanFit] = useState<Fit>();
  const [planNotices, setPlanNotices] = useState<OwnedNotice[]>([]);
  const [successfulPreferenceKey, setSuccessfulPreferenceKey] = useState<string | null>(null);
  const [requestState, setRequestState] = useState<RequestState>("idle");
  const [requestMessage, setRequestMessage] = useState<string | null>(null);
  const [previewStatus, setPreviewStatus] = useState<PreviewStatus>({ enabled: false, detail: null });
  const [previewStatusState, setPreviewStatusState] = useState<PreviewStatusState>("loading");

  useEffect(() => {
    const controller = new AbortController();
    async function loadDemoAndCatalog() {
      try {
        const sessionResponse = await fetch("/api/session", { signal: controller.signal });
        const session = parseDemoSession(await readJson(sessionResponse));
        setDemoSession(session);
        setSessionState("ready");
        if (!session.enabled) {
          setCatalogState("disabled");
          return;
        }
        const response = await fetch("/api/catalog", { signal: controller.signal });
        const loadedCatalog = parseCatalog(await readJson(response));
        setCatalog(loadedCatalog);
        setCatalogState("ready");
      } catch (error) {
        if (controller.signal.aborted) return;
        setCatalogError(errorMessage(error));
        setSessionState("error");
        setCatalogState("error");
      }
    }
    void loadDemoAndCatalog();
    return () => controller.abort();
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    async function loadPreviewStatus() {
      try {
        const response = await fetch("/api/previews/status", { signal: controller.signal });
        setPreviewStatus(parsePreviewStatus(await readJson(response)));
        setPreviewStatusState("ready");
      } catch {
        if (controller.signal.aborted) return;
        setPreviewStatus({ enabled: false, detail: "Preview availability could not be checked." });
        setPreviewStatusState("error");
      }
    }
    void loadPreviewStatus();
    return () => controller.abort();
  }, []);

  function toggleExclusion(material: Material) {
    setExclusions((current) =>
      current.includes(material)
        ? current.filter((item) => item !== material)
        : [...current, material],
    );
  }

  function addOwnedItem() {
    if (ownedItems.length >= 3) return;
    const nextNumber =
      Math.max(0, ...ownedItems.map((item) => Number(item.id.replace("owned-", "")))) + 1;
    setOwnedItems((current) => [
      ...current,
      {
        id: `owned-${nextNumber}`,
        category: "top",
        color: "",
        description: "",
      },
    ]);
  }

  function updateOwnedItem(id: string, field: keyof OwnedItem, value: string) {
    setOwnedItems((current) =>
      current.map((item) =>
        item.id === id
          ? { ...item, [field]: field === "category" ? readCategory(value) : value }
          : item,
      ),
    );
  }

  function removeOwnedItem(id: string) {
    setOwnedItems((current) => current.filter((entry) => entry.id !== id));
    setLocks((current) =>
      current.filter(
        (lock) => !(lock.item.kind === "owned" && lock.item.id === id),
      ),
    );
  }

  async function refreshDemoSession(): Promise<DemoSession> {
    const response = await fetch("/api/session");
    const session = parseDemoSession(await readJson(response));
    setDemoSession(session);
    setOver18Checked(session.age_confirmed);
    setSessionState("ready");
    return session;
  }

  function toggleLock(lookId: string, slot: Slot, item: ItemRef) {
    if (requestState === "loading") return;
    setLocks((current) => {
      const exists = current.some(
        (lock) =>
          lock.look_id === lookId &&
          lock.slot === slot &&
          lock.item.id === item.id &&
          lock.item.kind === item.kind,
      );
      return exists
        ? current.filter(
            (lock) =>
              !(
                lock.look_id === lookId &&
                lock.slot === slot &&
                lock.item.id === item.id &&
                lock.item.kind === item.kind
              ),
          )
        : [...current, { look_id: lookId, slot, item }];
    });
  }

  async function requestPlan(replacement: ReplacementTarget | null = null) {
    if (!catalog || requestState === "loading") return;
    if (!demoSession?.enabled || sessionState !== "ready") return;
    if (demoSession.plan_requests_remaining <= 0) {
      setRequestMessage("This demo session has reached its three planning requests.");
      setRequestState("error");
      return;
    }
    if (!demoSession.age_confirmed && !over18Checked) {
      setRequestMessage("Confirm that you are 18 or older before requesting a plan.");
      setRequestState("error");
      return;
    }
    if (replacement) {
      const look = plan?.looks.find((candidate) => candidate.id === replacement.look_id);
      const targetIsLocked = locks.some(
        (lock) => lock.look_id === replacement.look_id && lock.slot === replacement.slot,
      );
      if (!planCurrent || !look || !sameItem(look.items[replacement.slot], replacement.item) || targetIsLocked) {
        return;
      }
    }
    const colors = preferredColors
      .split(",")
      .map((color) => color.trim())
      .filter(Boolean);
    const amount = Number(budgetAmount);
    const amountCents = Math.round(amount * 100);
    if (colors.length > 5 || colors.some((color) => color.length > 30)) {
      setRequestMessage("Enter up to five colors, with no more than 30 characters each.");
      setRequestState("error");
      return;
    }
    if (!Number.isSafeInteger(amountCents) || amountCents <= 0) {
      setRequestMessage("Enter a budget greater than zero.");
      setRequestState("error");
      return;
    }
    const parsedFit = parseFit(fitDraft);
    if (!parsedFit.ok) {
      setRequestMessage(parsedFit.error);
      setRequestState("error");
      return;
    }
    const fit = parsedFit.fit;

    const request: PlanRequest = {
      brief: { style_goal: styleGoal.trim(), preferred_colors: colors },
      replacement,
      state: {
        budget: { amount_cents: amountCents, mode: budgetMode },
        material_exclusions: exclusions,
        fit,
        owned_items: ownedItems,
        locks: replacement && plan ? locksForReplacement(plan, replacement, locks) : locks,
        plan,
      },
    };
    const submittedPreferenceKey = preferenceKey(
      request.brief.style_goal,
      request.brief.preferred_colors,
      budgetAmount,
      budgetMode,
      exclusions,
      fitDraft,
      ownedItems,
    );
    setRequestState("loading");
    setRequestMessage(
      replacement
        ? "Replacing one item and checking all three looks."
        : plan
          ? "Updating the plan. Kept items stay in place."
          : "Requesting three looks from the catalog.",
    );
    try {
      if (!demoSession.age_confirmed) {
        const confirmation = await fetch("/api/session/confirm-age", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ over_18: true }),
        });
        setDemoSession(parseDemoSession(await readJson(confirmation)));
        setOver18Checked(true);
      }
      const response = await fetch("/api/plans", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(request),
      });
      const remainingHeader = response.headers.get("X-Plan-Requests-Remaining");
      const remaining = remainingHeader === null ? null : Number(remainingHeader);
      if (
        remaining !== null &&
        Number.isInteger(remaining) &&
        remaining >= 0 &&
        remaining <= demoSession.plan_requests_limit
      ) {
        setDemoSession((current) =>
          current
            ? {
                ...current,
                plan_requests_used: current.plan_requests_limit - remaining,
                plan_requests_remaining: remaining,
              }
            : current,
        );
      }
      const result = parsePlanResponse(await readJson(response));
      if (result.outcome === "success" && result.plan) {
        if (replacement && plan && !isExactReplacement(plan, result.plan, replacement)) {
          setRequestState("error");
          setRequestMessage("The planner did not replace only this item.");
          return;
        }
        setPlan(result.plan);
        setPlanOwnedItems(ownedItems);
        setPlanFit(fit);
        setPlanNotices(result.notices);
        setSuccessfulPreferenceKey(submittedPreferenceKey);
        setRequestState("success");
        setRequestMessage(
          replacement
            ? "The item was replaced. All three looks passed the catalog and budget checks."
            : "The three looks passed the catalog and budget checks.",
        );
      } else if (result.outcome === "infeasible") {
        setRequestState("error");
        setRequestMessage(
          replacement
            ? result.conflicts.length
              ? `Keeping the other items conflicts with these constraints: ${result.conflicts.map((item) => item.detail).join("; ")}`
              : "No replacement can keep the other items under the current constraints."
            : result.conflicts.length
              ? `${
                  // A look_composition conflict means no item can complete a look at all, kept or not.
                  request.state.locks.length && result.conflicts.some((item) => item.rule !== "look_composition")
                    ? "Kept items conflict with"
                    : "No plan can meet"
                } these constraints: ${result.conflicts.map((item) => item.detail).join("; ")}`
              : "No plan can meet the kept items and current constraints.",
        );
      } else if (result.outcome === "provider_timeout") {
        setRequestState("error");
        setRequestMessage(
          replacement
            ? "The replacement request timed out."
            : "The planning request timed out. Try again with the same or fewer constraints.",
        );
      } else if (result.outcome === "provider_error") {
        setRequestState("error");
        setRequestMessage(
          replacement
            ? "The planning provider could not complete this replacement."
            : "The planning provider could not complete this request.",
        );
      } else {
        setRequestState("error");
        const summary = replacement
          ? "No replacement passed the catalog checks."
          : "No plan passed the catalog checks. Adjust your preferences and try again.";
        // Fit violations come first, so a stated fit that no item meets is always explained.
        const violations = [
          ...result.violations.filter((item) => item.rule === "fit"),
          ...result.violations.filter((item) => item.rule !== "fit"),
        ]
          .slice(0, 3)
          .map((item) => item.detail)
          .join(" ");
        const diagnostic = violations || result.error?.slice(0, 300) || "";
        setRequestMessage(diagnostic ? `${summary} ${diagnostic}` : summary);
      }
    } catch (error) {
      setRequestState("error");
      setRequestMessage(errorMessage(error));
      try {
        await refreshDemoSession();
      } catch {
        // Keep the last server-confirmed session state if a refresh also fails.
      }
    }
  }

  function submitPlan(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void requestPlan();
  }

  function replacePlanItem(lookId: string, slot: Slot, item: ItemRef) {
    void requestPlan({ look_id: lookId, slot, item });
  }

  const canSubmit =
    catalogState === "ready" &&
    requestState !== "loading" &&
    demoSession?.enabled === true &&
    demoSession.plan_requests_remaining > 0 &&
    (demoSession.age_confirmed || over18Checked);
  const preferredColorList = preferredColors
    .split(",")
    .map((color) => color.trim())
    .filter(Boolean);
  const currentPreferenceKey = preferenceKey(
    styleGoal,
    preferredColorList,
    budgetAmount,
    budgetMode,
    exclusions,
    fitDraft,
    ownedItems,
  );
  const planCurrent = Boolean(plan && successfulPreferenceKey === currentPreferenceKey);
  const planTotal =
    plan && catalog
      ? budgetMode === "whole_plan"
        ? wholePlanCost(plan, catalog)
        : totalLookCosts(plan, catalog)
      : null;
  const previewOptions =
    plan && catalog ? previewOptionsForPlan(plan, catalog, planOwnedItems) : [];
  const previewSourceKey = previewOptions
    .map((option) => `${option.id}:${option.sourceKey}`)
    .join("|");

  return (
    <main className="app-shell">
      <header className="topbar">
        <a className="wordmark" href="/" aria-label="FullAura home">FullAura</a>
        <p className="environment-label">Style planning</p>
      </header>

      <div className="workspace">
        <div className="workspace-heading">
          <div>
            <h1>Three looks. Rules checked.</h1>
            <p className="page-description">
              Set your preferences. Each look uses catalog products that pass the rules you choose.
            </p>
          </div>
          <p className="catalog-status" aria-live="polite">
            {catalogState === "loading" && "Loading catalog"}
            {catalogState === "ready" && `${catalog?.products.length ?? 0} catalog products`}
            {catalogState === "disabled" && "Demo closed"}
            {catalogState === "error" && "Catalog unavailable"}
          </p>
        </div>

        <div className="workspace-grid">
          <section className="preferences-panel" aria-labelledby="preferences-title">
            <div className="panel-heading">
              <h2 id="preferences-title">Your preferences</h2>
              <p>FullAura checks the budget, fit, delivery, stock and material exclusions in code.</p>
            </div>

            {catalogState === "error" ? (
              <div className="notice notice-error" role="alert">{catalogError}</div>
            ) : catalogState === "disabled" ? (
              <div className="empty-state">
                <h3>This demo is closed</h3>
                <p>The planning catalog and model are available only when the pilot is enabled.</p>
              </div>
            ) : (
              <form className="preferences-form" onSubmit={submitPlan}>
                <div className="form-field">
                  <label htmlFor="style-goal">What should the looks work for?</label>
                  <textarea
                    autoComplete="off"
                    id="style-goal"
                    maxLength={300}
                    onChange={(event) => setStyleGoal(event.target.value)}
                    placeholder="For example, relaxed outfits for the office"
                    required
                    rows={3}
                    value={styleGoal}
                  />
                  <p className="field-help">Use up to 300 characters.</p>
                </div>

                <div className="form-field">
                  <label htmlFor="preferred-colors">Colors you prefer</label>
                  <input
                    autoComplete="off"
                    id="preferred-colors"
                    maxLength={164}
                    onChange={(event) => setPreferredColors(event.target.value)}
                    placeholder="Navy, cream, olive"
                    type="text"
                    value={preferredColors}
                  />
                  <p className="field-help">Separate up to five colors with commas.</p>
                </div>

                <div className="form-field">
                  <label htmlFor="budget-amount">Budget in EUR</label>
                  <input
                    id="budget-amount"
                    inputMode="decimal"
                    min="0.01"
                    onChange={(event) => setBudgetAmount(event.target.value)}
                    required
                    step="0.01"
                    type="number"
                    value={budgetAmount}
                  />
                </div>

                <fieldset className="budget-mode">
                  <legend>Budget applies to</legend>
                  <label className="radio-choice">
                    <input
                      checked={budgetMode === "per_look"}
                      name="budget-mode"
                      onChange={() => setBudgetMode("per_look")}
                      type="radio"
                    />
                    <span>Each look</span>
                  </label>
                  <label className="radio-choice">
                    <input
                      checked={budgetMode === "whole_plan"}
                      name="budget-mode"
                      onChange={() => setBudgetMode("whole_plan")}
                      type="radio"
                    />
                    <span>All three looks together</span>
                  </label>
                </fieldset>

                <FitFields fitDraft={fitDraft} setFitDraft={setFitDraft} />

                <details className="exclusions-disclosure">
                  <summary>
                    <span>Avoid materials</span>
                    <span className="selection-count">{exclusions.length} selected</span>
                  </summary>
                  <p className="field-help">
                    Exclusions apply to new purchases. Only materials named in a product's catalog record are checked; unnamed or vague parts are not checked. A selection also removes products whose composition cannot be checked.
                  </p>
                  <div className="material-list">
                    {MATERIALS.map((material) => (
                      <label className="check-choice" key={material}>
                        <input
                          checked={exclusions.includes(material)}
                          onChange={() => toggleExclusion(material)}
                          type="checkbox"
                          value={material}
                        />
                        <span>{material}</span>
                      </label>
                    ))}
                  </div>
                </details>

                <OwnedItemsFields
                  ownedItems={ownedItems}
                  onAddItem={addOwnedItem}
                  onUpdateItem={updateOwnedItem}
                  onRemoveItem={removeOwnedItem}
                />

                <div className="demo-consent">
                  <label className="check-choice" htmlFor="confirm-over-18">
                    <input
                      checked={demoSession?.age_confirmed === true || over18Checked}
                      disabled={demoSession?.age_confirmed === true}
                      id="confirm-over-18"
                      onChange={(event) => setOver18Checked(event.target.checked)}
                      required={demoSession?.age_confirmed !== true}
                      type="checkbox"
                    />
                    <span>I confirm that I am 18 or older.</span>
                  </label>
                  <p className="field-help">
                    {demoSession?.age_confirmed
                      ? "Age confirmation is recorded for this browser session."
                      : "Planning sends your style preferences, budget and owned-item descriptions to Baseten. This session allows up to three plan requests, including updates and replacements."}
                  </p>
                  <p className="field-help">
                    Photo previews are disabled pending T-02. If enabled later, two JPEGs will be sent to Meta after metadata removal. FullAura keeps photos in memory only for the request; Meta’s current terms govern provider retention.
                  </p>
                  {demoSession && (
                    <p className="session-quota" role="status">
                      {demoSession.plan_requests_remaining} of {demoSession.plan_requests_limit} planning requests remain in this session.
                    </p>
                  )}
                </div>

                <button
                  aria-busy={requestState === "loading"}
                  className={`primary-button${requestState === "loading" ? " is-loading" : requestState === "error" ? " is-error" : requestState === "success" && planCurrent ? " is-success" : ""}`}
                  disabled={!canSubmit}
                  type="submit"
                >
                  {requestState === "loading" ? "Planning looks…" : plan ? "Update three looks" : "Generate three looks"}
                </button>
                <p className="submit-help">
                  This uses one planning request. Provider quota or cost may apply.
                </p>
              </form>
            )}
          </section>

          <section className="results-panel" aria-labelledby="results-title">
            <div className="results-heading">
              <div>
                <h2 id="results-title">Three looks</h2>
                <p>
                  Each item links to its catalog source. Keep pieces to retain them, or replace one
                  item; other positions stay in place and all three looks are checked again.
                  Replacements send a planning request, so provider quota or cost may apply.
                </p>
              </div>
              {plan && catalog && (
                <p className="plan-total">
                  {budgetMode === "whole_plan" ? "Combined budget" : "Total if each look is bought"}
                  <strong>{formatMoney(planTotal)}</strong>
                </p>
              )}
            </div>

            {catalogState === "loading" ? (
              <div className="loading-state" role="status">
                <span className="visually-hidden">Loading catalog and planning area</span>
                {["first", "second", "third"].map((look) => (
                  <div aria-hidden="true" className="look-skeleton" key={look}>
                    <span className="skeleton-title" />
                    <span className="skeleton-row" />
                    <span className="skeleton-row" />
                  </div>
                ))}
              </div>
            ) : catalogState === "disabled" ? (
              <div className="empty-state">
                <h3>Planning is unavailable</h3>
                <p>The pilot is not open for requests yet.</p>
              </div>
            ) : catalogState === "error" ? (
              <div className="empty-state" role="status">{catalogError}</div>
            ) : requestState === "loading" && !plan ? (
              <div className="loading-state" role="status">
                <span className="visually-hidden">Planning three looks</span>
                {["first", "second", "third"].map((look) => (
                  <div aria-hidden="true" className="look-skeleton" key={look}>
                    <span className="skeleton-title" />
                    <span className="skeleton-row" />
                    <span className="skeleton-row" />
                  </div>
                ))}
              </div>
            ) : plan && catalog ? (
              <>
                {requestMessage && (
                  <p
                    className={`request-message${requestState === "error" ? " is-error" : ""}`}
                    role={requestState === "error" ? "alert" : "status"}
                  >
                    {requestState === "loading" && requestMessage}
                    {requestState === "error" && requestMessage}
                    {requestState === "success" && planCurrent && requestMessage}
                    {requestState === "success" && !planCurrent && "Preferences changed. These looks are from the previous successful request."}
                    {requestState === "error" && " The displayed looks are from the previous successful request."}
                  </p>
                )}
                <div className="look-grid">
                  {plan.looks.map((look) => (
                    <LookCard
                      key={look.id}
                      catalog={catalog}
                      lockDisabled={requestState === "loading"}
                      replaceDisabled={!planCurrent || !canSubmit}
                      locks={locks}
                      look={look}
                      onToggleLock={toggleLock}
                      onReplaceItem={replacePlanItem}
                      ownedItems={planOwnedItems}
                      ownedNotices={planNotices}
                      fit={planFit}
                      plan={plan}
                    />
                  ))}
                </div>
                <PreviewPanel
                  key={previewSourceKey}
                  enabled={previewStatus.enabled && planCurrent}
                  detail={
                    !planCurrent
                      ? "Update the plan before generating a preview."
                      : previewStatus.detail
                  }
                  loadingStatus={previewStatusState === "loading"}
                  options={previewOptions}
                />
              </>
            ) : requestState === "error" && requestMessage ? (
              <div className="empty-state is-error" role="alert">{requestMessage}</div>
            ) : (
              <div className="empty-state">
                <h3>Your plan will appear here</h3>
                <p>Enter a style goal and budget, then generate three looks from the catalog.</p>
                <p>Product photos and personal photo previews are not part of this development screen.</p>
              </div>
            )}
          </section>
        </div>
      </div>
    </main>
  );
}
