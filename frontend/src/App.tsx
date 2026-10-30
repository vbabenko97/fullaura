import { useEffect, useState } from "react";
import {
  errorMessage,
  newOperationId,
  parseCatalog,
  parsePlanResponse,
  parseSession,
  postJson,
  readJson,
  reasonOf,
} from "./api";
import { AccessGate } from "./AccessGate";
import { FeedbackForm } from "./FeedbackForm";
import { formatMoney } from "./format";
import { LookCard } from "./LookCard";
import {
  fitDraftFrom,
  isExactReplacement,
  locksForReplacement,
  parseFit,
  preferenceKey,
  sameItem,
  shoppingList,
  totalLookCosts,
  wholePlanCost,
} from "./plan";
import type { Preferences } from "./plan";
import { PreferencesForm } from "./PreferencesForm";
import { PrivacyNotice } from "./PrivacyNotice";
import { deleteSavedPlan, keepCurrentPlan, loadCurrentPlan, loadSavedPlans, savePlan } from "./planStorage";
import { SavedPlans } from "./SavedPlans";
import { ShoppingList } from "./ShoppingList";
import { TryOnPanel } from "./TryOnPanel";
import { EMPTY_FIT_DRAFT, OCCASIONS, SEASONS } from "./types";
import type {
  Brief,
  Catalog,
  ItemRef,
  LockedPlacement,
  OwnedNotice,
  Plan,
  PlanRequest,
  PlanResponse,
  ReplacementTarget,
  SavedPlan,
  Session,
  Slot,
  StateInput,
} from "./types";

type LoadState = "loading" | "ready" | "error";
type RequestState = "idle" | "loading" | "success" | "error";

/** The plan the server last checked for this browser session, and what it was checked against. */
interface CurrentPlan {
  plan: Plan;
  token: string;
  catalogId: string;
  brief: Brief;
  state: StateInput;
  notices: OwnedNotice[];
  preferenceKey: string;
}

const INITIAL_PREFERENCES: Preferences = {
  styleGoal: "",
  colors: [],
  occasion: "",
  season: "",
  budgetAmount: "",
  budgetMode: "per_look",
  footwear: "closed",
  exclusions: [],
  fit: EMPTY_FIT_DRAFT,
  ownedItems: [],
};

class PlanLostError extends Error {
  constructor() {
    super("Your plan expired on the server and no longer passes today's checks, so it was cleared. Generate a new plan; your saved plans are below.");
  }
}

function inviteFromLocation(): string | null {
  const match = /(?:^#|&)invite=([^&]+)/.exec(window.location.hash);
  return match ? decodeURIComponent(match[1]) : null;
}

function preferencesFrom(saved: { brief: Brief; state: StateInput }): Preferences {
  return {
    styleGoal: saved.brief.style_goal,
    colors: saved.brief.preferred_colors,
    occasion: saved.brief.occasion ?? "",
    season: saved.brief.season ?? "",
    budgetAmount: String(saved.state.budget.amount_cents / 100),
    budgetMode: saved.state.budget.mode,
    footwear: saved.state.footwear,
    exclusions: saved.state.material_exclusions,
    fit: fitDraftFrom(saved.state.fit),
    ownedItems: saved.state.owned_items,
  };
}

function lookLabel(lookId: string): string {
  return lookId.replaceAll("_", " ");
}

export function App() {
  const [session, setSession] = useState<Session | null>(null);
  const [sessionState, setSessionState] = useState<LoadState>("loading");
  const [gateBusy, setGateBusy] = useState(false);
  const [gateMessage, setGateMessage] = useState<string | null>(null);
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [catalogState, setCatalogState] = useState<LoadState>("loading");
  const [preferences, setPreferences] = useState<Preferences>(INITIAL_PREFERENCES);
  const [locks, setLocks] = useState<LockedPlacement[]>([]);
  const [current, setCurrent] = useState<CurrentPlan | null>(null);
  const [requestState, setRequestState] = useState<RequestState>("idle");
  const [requestMessage, setRequestMessage] = useState<string | null>(null);
  const [saved, setSaved] = useState<SavedPlan[]>(() => loadSavedPlans());
  const [savedMessage, setSavedMessage] = useState<string | null>(null);
  const [tryOnLook, setTryOnLook] = useState("look_1");

  async function refreshSession(): Promise<Session | null> {
    try {
      const next = parseSession(await readJson(await fetch("/api/session")));
      setSession(next);
      setSessionState("ready");
      return next;
    } catch {
      setSessionState((state) => (state === "loading" ? "error" : state));
      return null;
    }
  }

  function adopt(response: PlanResponse, brief: Brief, state: StateInput, key: string): CurrentPlan | null {
    if (response.outcome !== "success" || !response.plan || !response.plan_token || !catalog) return null;
    const next: CurrentPlan = {
      plan: response.plan,
      token: response.plan_token,
      catalogId: response.catalog_id ?? catalog.id,
      brief,
      state,
      notices: response.notices,
      preferenceKey: key,
    };
    setCurrent(next);
    keepCurrentPlan(savedFrom(next, catalog));
    return next;
  }

  async function restore(
    saved: { brief: Brief; state: StateInput & { plan: Plan }; catalog_id: string },
    loadedCatalog: Catalog | null = catalog,
  ): Promise<{ plan: CurrentPlan | null; response: PlanResponse | null; error: unknown }> {
    try {
      const body = { brief: saved.brief, state: saved.state, catalog_id: saved.catalog_id };
      const response = parsePlanResponse(await postJson("/api/plans/restore", body));
      if (response.outcome !== "success" || !response.plan || !response.plan_token || !loadedCatalog) {
        return { plan: null, response, error: null };
      }
      const { budget, material_exclusions, fit, footwear, owned_items, locks: kept } = saved.state;
      const state: StateInput = {
        budget,
        material_exclusions,
        footwear,
        owned_items,
        locks: kept,
        ...(fit ? { fit } : {}),
      };
      const next: CurrentPlan = {
        plan: response.plan,
        token: response.plan_token,
        catalogId: response.catalog_id ?? loadedCatalog.id,
        brief: saved.brief,
        state,
        notices: response.notices,
        preferenceKey: preferenceKey(preferencesFrom(saved)),
      };
      setCurrent(next);
      keepCurrentPlan(savedFrom(next, loadedCatalog));
      return { plan: next, response, error: null };
    } catch (error) {
      return { plan: null, response: null, error };
    }
  }

  async function restoreCurrent(): Promise<string | null> {
    if (!current) return null;
    const result = await restore({
      brief: current.brief,
      state: { ...current.state, plan: current.plan },
      catalog_id: current.catalogId,
    });
    if (!result.plan && result.response) {
      // The plan no longer passes today's checks, so it cannot be named again; start afresh.
      setCurrent(null);
      setLocks([]);
      keepCurrentPlan(null);
    }
    return result.plan?.token ?? null;
  }

  useEffect(() => {
    let cancelled = false;
    async function start() {
      const invite = inviteFromLocation();
      if (invite) {
        window.history.replaceState(null, "", window.location.pathname + window.location.search);
        try {
          await postJson("/api/session/invite", { code: invite });
        } catch (error) {
          if (!cancelled) setGateMessage(errorMessage(error));
        }
      }
      let next: Session;
      try {
        next = parseSession(await readJson(await fetch("/api/session")));
      } catch {
        if (!cancelled) setSessionState("error");
        return;
      }
      if (cancelled) return;
      setSession(next);
      setSessionState("ready");
      if (!next.beta || !next.invited) return;
      let loaded: Catalog;
      try {
        loaded = parseCatalog(await readJson(await fetch("/api/catalog")));
      } catch {
        if (!cancelled) setCatalogState("error");
        return;
      }
      if (cancelled) return;
      setCatalog(loaded);
      setCatalogState("ready");
      const working = loadCurrentPlan();
      if (working) {
        setPreferences(preferencesFrom(working));
        setLocks(working.state.locks);
        const result = await restore(working, loaded);
        if (!cancelled && !result.plan) {
          keepCurrentPlan(null);
          setRequestState("error");
          setRequestMessage(
            result.response
              ? "Your last plan no longer passes today's checks. Generate a new plan, or open a saved one below."
              : errorMessage(result.error),
          );
        }
      }
    }
    void start();
    return () => {
      cancelled = true;
    };
  }, []);

  async function openInvite(code: string) {
    setGateBusy(true);
    setGateMessage(null);
    try {
      const next = parseSession(await postJson("/api/session/invite", { code }));
      setSession(next);
      const loaded = parseCatalog(await readJson(await fetch("/api/catalog")));
      setCatalog(loaded);
      setCatalogState("ready");
    } catch (error) {
      setGateMessage(errorMessage(error));
    } finally {
      setGateBusy(false);
    }
  }

  async function confirmAge() {
    setGateBusy(true);
    setGateMessage(null);
    try {
      setSession(parseSession(await postJson("/api/session/confirm-age", { over_18: true })));
    } catch (error) {
      setGateMessage(errorMessage(error));
    } finally {
      setGateBusy(false);
    }
  }

  async function setPhotoConsent(consent: boolean) {
    try {
      setSession(parseSession(await postJson("/api/session/photo-consent", { consent })));
    } catch (error) {
      setRequestState("error");
      setRequestMessage(errorMessage(error));
    }
  }

  function removeOwnedItem(id: string) {
    setPreferences((value) => ({ ...value, ownedItems: value.ownedItems.filter((item) => item.id !== id) }));
    setLocks((value) => value.filter((lock) => !(lock.item.kind === "owned" && lock.item.id === id)));
  }

  function toggleLock(lookId: string, slot: Slot, item: ItemRef) {
    if (requestState === "loading") return;
    setLocks((value) => {
      const matches = (lock: LockedPlacement) =>
        lock.look_id === lookId && lock.slot === slot && sameItem(lock.item, item);
      return value.some(matches) ? value.filter((lock) => !matches(lock)) : [...value, { look_id: lookId, slot, item }];
    });
  }

  function buildRequest(replacement: ReplacementTarget | null): { request: PlanRequest; key: string } | string {
    const colors = preferences.colors.map((color) => color.trim()).filter(Boolean);
    if (colors.length > 5 || colors.some((color) => color.length > 30)) {
      return "Enter up to five colors, with no more than 30 characters each.";
    }
    const amountCents = Math.round(Number(preferences.budgetAmount) * 100);
    if (!Number.isSafeInteger(amountCents) || amountCents <= 0) return "Enter a budget greater than zero.";
    const parsedFit = parseFit(preferences.fit);
    if (!parsedFit.ok) return parsedFit.error;
    const state: StateInput = {
      budget: { amount_cents: amountCents, mode: preferences.budgetMode },
      material_exclusions: preferences.exclusions,
      footwear: preferences.footwear,
      owned_items: preferences.ownedItems,
      locks: replacement && current ? locksForReplacement(current.plan, replacement, locks) : current ? locks : [],
      ...(parsedFit.fit ? { fit: parsedFit.fit } : {}),
    };
    const brief: Brief = {
      style_goal: preferences.styleGoal.trim(),
      preferred_colors: colors,
      occasion: OCCASIONS.find((occasion) => occasion === preferences.occasion) ?? null,
      season: SEASONS.find((season) => season === preferences.season) ?? null,
    };
    return {
      request: { brief, state, replacement, plan_token: current?.token ?? null },
      key: preferenceKey(preferences),
    };
  }

  async function sendPlan(request: PlanRequest, operationId: string): Promise<PlanResponse> {
    const send = (token: string | null) =>
      postJson("/api/plans", { ...request, plan_token: token }, { "X-Operation-Id": operationId });
    try {
      return parsePlanResponse(await send(request.plan_token));
    } catch (error) {
      if (reasonOf(error) !== "plan_expired") throw error;
      const token = await restoreCurrent();
      if (!token) throw new PlanLostError();
      return parsePlanResponse(await send(token));
    }
  }

  async function requestPlan(replacement: ReplacementTarget | null = null) {
    if (!catalog || requestState === "loading" || !session?.planning_open) return;
    if (replacement) {
      const look = current?.plan.looks.find((candidate) => candidate.id === replacement.look_id);
      const locked = locks.some((lock) => lock.look_id === replacement.look_id && lock.slot === replacement.slot);
      if (!planCurrent || !look || !sameItem(look.items[replacement.slot], replacement.item) || locked) return;
    }
    const built = buildRequest(replacement);
    if (typeof built === "string") {
      setRequestState("error");
      setRequestMessage(built);
      return;
    }
    const { request, key } = built;
    setRequestState("loading");
    setRequestMessage(
      replacement
        ? "Replacing one item and checking all three looks."
        : current
          ? "Updating the plan. Kept items stay in place."
          : "Requesting three looks from the catalog.",
    );
    const previous = current;
    try {
      const result = await sendPlan(request, newOperationId());
      if (result.outcome === "success" && result.plan) {
        if (replacement && previous && !isExactReplacement(previous.plan, result.plan, replacement)) {
          setRequestState("error");
          setRequestMessage("The planner did not replace only this item.");
          return;
        }
        // Keep the person's own locks; a replacement's temporary locks hold only for that request.
        adopt(result, request.brief, { ...request.state, locks }, key);
        revealResults();
        setRequestState("success");
        setRequestMessage(
          replacement
            ? "The item was replaced. All three looks passed the catalog checks."
            : "The three looks passed the catalog checks: budget, sizes, shoes, materials, delivery and stock.",
        );
      } else {
        setRequestState("error");
        setRequestMessage(failureMessage(result, replacement !== null, request.state.locks.length > 0));
      }
    } catch (error) {
      setRequestState("error");
      setRequestMessage(error instanceof PlanLostError ? error.message : errorMessage(error));
    } finally {
      void refreshSession();
    }
  }

  function saveCurrent() {
    if (!current || !catalog) return;
    const entry = savedFrom(current, catalog);
    setSavedMessage(savePlan(entry) ? `Saved "${entry.name}" on this device.` : "This browser did not allow saving.");
    setSaved(loadSavedPlans());
  }

  async function restoreSaved(entry: SavedPlan) {
    setRequestState("loading");
    setRequestMessage(`Checking "${entry.name}" against today's catalog.`);
    const result = await restore(entry);
    if (result.plan) {
      setPreferences(preferencesFrom(entry));
      setLocks(entry.state.locks);
      setRequestState("success");
      setRequestMessage(
        result.response?.catalog_changed
          ? "The saved plan still passes every check. The catalog changed since you saved it, so prices and offers were checked again."
          : "The saved plan still passes every check.",
      );
      return;
    }
    setRequestState("error");
    setRequestMessage(
      result.response
        ? `The saved plan no longer passes today's checks: ${result.response.violations
            .slice(0, 3)
            .map((violation) => violation.detail)
            .join(" ")} Its saved shopping list is still available below.`
        : errorMessage(result.error),
    );
  }

  function deleteSaved(id: string) {
    setSavedMessage(deleteSavedPlan(id) ? "Deleted from this device." : "This browser did not allow deleting.");
    setSaved(loadSavedPlans());
  }

  const loading = requestState === "loading";
  const currentKey = preferenceKey(preferences);
  const planCurrent = Boolean(current && current.preferenceKey === currentKey);
  const ready = session !== null && session.beta && session.invited && session.age_confirmed;
  const canSubmit = ready && catalogState === "ready" && !loading && session.planning_open;
  const planTotal =
    current && catalog
      ? current.state.budget.mode === "whole_plan"
        ? wholePlanCost(current.plan, catalog)
        : totalLookCosts(current.plan, catalog)
      : null;
  const lines = current && catalog ? shoppingList(current.plan, catalog, current.state.owned_items) : [];
  const remainingPlans = session ? Math.max(0, session.plan_requests_limit - session.plan_requests_used) : 0;
  const closedText = !session
    ? null
    : session.pilot_ended
      ? "The beta has ended. Saved plans and shopping lists stay available."
      : !session.accounting
        ? "Spending cannot be recorded right now, so planning is paused. Your plan stays available."
        : remainingPlans === 0
          ? "This invite has used all its planning requests."
          : !session.planning_open
            ? "Planning is paused because the beta's shared budget is used up, or the model is not configured."
            : null;

  return (
    <main className="app-shell">
      <header className="topbar no-print">
        <a className="wordmark" href="/" aria-label="FullAura home">FullAura</a>
        <p className="environment-label">Closed beta</p>
        {ready && session.accounting && (
          <p className="environment-label">
            {remainingPlans} of {session.plan_requests_limit} plans left
          </p>
        )}
      </header>

      {sessionState === "loading" ? (
        <div className="workspace">
          <p className="loading-line" role="status">Opening FullAura…</p>
        </div>
      ) : sessionState === "error" || !session ? (
        <div className="workspace">
          <div className="empty-state is-error" role="alert">
            <h3>FullAura could not be reached</h3>
            <p>Check your connection and reload the page.</p>
          </div>
        </div>
      ) : !ready ? (
        <div className="workspace">
          <AccessGate
            busy={gateBusy}
            message={gateMessage}
            onConfirmAge={() => void confirmAge()}
            onInvite={(code) => void openInvite(code)}
            session={session}
          />
        </div>
      ) : (
        <div className="workspace">
          <div className="workspace-heading no-print">
            <div>
              <h1>Three looks. Rules checked.</h1>
              <p className="page-description">
                Real products that ship to Austria, checked against your budget, sizes, shoes and materials. Keep what
                you like, replace the rest, then try a look on.
              </p>
            </div>
            <p className="catalog-status" aria-live="polite">
              {catalogState === "loading" && "Loading catalog"}
              {catalogState === "ready" && catalog && `${catalog.products.length} products, checked ${catalog.id.slice(-10)}`}
              {catalogState === "error" && "Catalog unavailable"}
            </p>
          </div>

          <div className="workspace-grid">
            <section className="preferences-panel no-print" aria-labelledby="preferences-title">
              <div className="panel-heading">
                <h2 id="preferences-title">Your preferences</h2>
                <p>You set your sizes and limits; FullAura never reads them from photos.</p>
              </div>
              <PreferencesForm
                canSubmit={canSubmit}
                closedText={closedText}
                hasPlan={current !== null}
                loading={loading}
                onRemoveOwnedItem={removeOwnedItem}
                onSubmit={() => void requestPlan()}
                preferences={preferences}
                quotaText={`Uses one of ${remainingPlans} planning requests left for this invite.`}
                setPreferences={setPreferences}
              />
            </section>

            <section className="results-panel" aria-labelledby="results-title">
              <div className="results-heading">
                <div>
                  <h2 id="results-title">Three looks</h2>
                  <p className="no-print">
                    Keep items to hold them in place through an update, or replace one item; every other position
                    stays and all three looks are checked again.
                  </p>
                </div>
                {current && (
                  <p className="plan-total">
                    {current.state.budget.mode === "whole_plan" ? "All three looks" : "If each look is bought"}
                    <strong>{formatMoney(planTotal)}</strong>
                  </p>
                )}
              </div>

              {requestMessage && (current || requestState !== "error") && (
                <p
                  className={`request-message no-print${requestState === "error" ? " is-error" : ""}`}
                  role={requestState === "error" ? "alert" : "status"}
                >
                  {requestMessage}
                  {requestState === "success" && current && !planCurrent && " Preferences changed since; update to apply them."}
                  {requestState === "error" && current && " The looks below are from the last successful request."}
                </p>
              )}

              {catalogState === "loading" || (loading && !current) ? (
                <div className="loading-state" role="status">
                  <span className="visually-hidden">{loading ? "Planning three looks" : "Loading the catalog"}</span>
                  {["first", "second", "third"].map((look) => (
                    <div aria-hidden="true" className="look-skeleton" key={look}>
                      <span className="skeleton-title" />
                      <span className="skeleton-row" />
                      <span className="skeleton-row" />
                    </div>
                  ))}
                </div>
              ) : catalogState === "error" ? (
                <div className="empty-state is-error" role="alert">
                  <h3>The catalog could not be loaded</h3>
                  <p>Reload the page. Your saved plans below are still available.</p>
                </div>
              ) : current && catalog ? (
                <>
                  <div className="look-grid">
                    {current.plan.looks.map((look) => (
                      <LookCard
                        catalog={catalog}
                        fit={current.state.fit}
                        footwear={current.state.footwear}
                        key={look.id}
                        lockDisabled={loading}
                        locks={locks}
                        look={look}
                        onReplaceItem={(lookId, slot, item) => void requestPlan({ look_id: lookId, slot, item })}
                        onToggleLock={toggleLock}
                        onTryOn={(lookId) => {
                          setTryOnLook(lookId);
                          document.getElementById("tryon-title")?.scrollIntoView?.({ block: "start" });
                        }}
                        ownedItems={current.state.owned_items}
                        ownedNotices={current.notices}
                        plan={current.plan}
                        replaceDisabled={!planCurrent || !canSubmit}
                      />
                    ))}
                  </div>
                  <TryOnPanel
                    lookId={current.plan.looks.some((look) => look.id === tryOnLook) ? tryOnLook : current.plan.looks[0].id}
                    lookIds={current.plan.looks.map((look) => look.id)}
                    onConsent={setPhotoConsent}
                    onFinished={() => void refreshSession()}
                    onRestore={restoreCurrent}
                    onSelectLook={setTryOnLook}
                    planToken={current.token}
                    session={session}
                  />
                  <ShoppingList lines={lines} />
                </>
              ) : requestState === "error" && requestMessage ? (
                <div className="empty-state is-error" role="alert">
                  <h3>No plan yet</h3>
                  <p>{requestMessage}</p>
                </div>
              ) : (
                <div className="empty-state">
                  <h3>Your plan will appear here</h3>
                  <p>Describe what the looks are for, set a budget and your sizes, then generate three looks.</p>
                </div>
              )}

              <SavedPlans
                busy={loading}
                canSave={current !== null}
                message={savedMessage}
                onDelete={deleteSaved}
                onRestore={(entry) => void restoreSaved(entry)}
                onSave={saveCurrent}
                saved={saved}
              />
              <FeedbackForm feedbackIds={session.feedback_ids} onChanged={() => void refreshSession()} />
            </section>
          </div>
        </div>
      )}
      <PrivacyNotice />
    </main>
  );
}

function failureMessage(result: PlanResponse, replacement: boolean, hadLocks: boolean): string {
  const details = (items: { detail: string }[]) => items.map((item) => item.detail).join("; ");
  if (result.outcome === "infeasible") {
    if (replacement) {
      return result.conflicts.length
        ? `Keeping the other items conflicts with these constraints: ${details(result.conflicts)}`
        : "No replacement can keep the other items under the current constraints.";
    }
    // A look_composition conflict means no item can complete a look at all, kept or not.
    const lockConflict = hadLocks && result.conflicts.some((item) => item.rule !== "look_composition");
    return result.conflicts.length
      ? `${lockConflict ? "Kept items conflict with" : "No plan can meet"} these constraints: ${details(result.conflicts)}`
      : "No plan can meet the kept items and current constraints.";
  }
  if (result.outcome === "provider_timeout") {
    return replacement
      ? "The replacement request timed out."
      : "The planning request timed out. Try again with the same or fewer constraints.";
  }
  if (result.outcome === "provider_error") {
    return replacement
      ? "The planning provider could not complete this replacement."
      : "The planning provider could not complete this request.";
  }
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
  return diagnostic ? `${summary} ${diagnostic}` : summary;
}

/** On a narrow screen the looks sit below the form; bring them into view once they change. */
function revealResults() {
  const heading = document.getElementById("results-title");
  if (!heading) return;
  const top = heading.getBoundingClientRect().top;
  if (top < 0 || top > window.innerHeight) heading.scrollIntoView?.({ block: "start" });
}

function savedFrom(plan: CurrentPlan, catalog: Catalog): SavedPlan {
  const looks = plan.plan.looks.map((look) => lookLabel(look.id));
  const name = plan.brief.style_goal.slice(0, 60) || `Plan with ${looks.length} looks`;
  return {
    id: plan.token.slice(0, 16),
    saved_at: new Date().toISOString(),
    name,
    brief: plan.brief,
    state: { ...plan.state, locks: plan.state.locks, plan: plan.plan },
    catalog_id: plan.catalogId,
    shopping_list: shoppingList(plan.plan, catalog, plan.state.owned_items),
  };
}
