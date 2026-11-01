import { useEffect, useRef, useState } from "react";
import { flushSync } from "react-dom";
import {
  errorMessage,
  newOperationId,
  parseCatalog,
  parseConceptResponse,
  parsePlanResponse,
  parseSession,
  postJson,
  readJson,
  reasonOf,
} from "./api";
import { AccessGate } from "./AccessGate";
import { ConceptCard } from "./ConceptCard";
import { FeedbackForm } from "./FeedbackForm";
import { budgetKept, formatCheckedRange, formatMoney, lookLabel } from "./format";
import { LookCard } from "./LookCard";
import {
  fitDraftFrom,
  isExactReplacement,
  locksForReplacement,
  parseFit,
  passedChecksText,
  preferenceKey,
  readableDetail,
  sameItem,
  sameWork,
  shoppingList,
  shoppingTotal,
  uncheckedText,
} from "./plan";
import type { Preferences } from "./plan";
import { PreferencesForm } from "./PreferencesForm";
import { PrivacyNotice } from "./PrivacyNotice";
import { deleteSavedPlan, keepCurrentPlan, loadCurrentPlan, loadSavedPlans, MAX_SAVED, savePlan } from "./planStorage";
import { SavedPlans } from "./SavedPlans";
import { ShoppingList } from "./ShoppingList";
import { TryOnPanel } from "./TryOnPanel";
import { EMPTY_FIT_DRAFT, OCCASIONS, SEASONS } from "./types";
import type {
  Brief,
  Catalog,
  Concept,
  ConceptRequest,
  ConceptResponse,
  ItemRef,
  LockedPlacement,
  OwnedNotice,
  Plan,
  PlanRequest,
  PlanResponse,
  ReplacementTarget,
  SavedPlan,
  Session,
  Shoppable,
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

/** A restore's result: the plan it made current, the server's answer, or the error that stopped it. */
interface Restored {
  plan: CurrentPlan | null;
  response: PlanResponse | null;
  error: unknown;
}

/** The Design my look concept the server last made for this page; its token names it for a try-on. */
interface CurrentConcept {
  concept: Concept;
  token: string;
  shoppable: Shoppable;
  matchingFailed: boolean;
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

/** The invite code in the address, which then leaves the address. A damaged link stays as it is,
 * for the server to refuse. */
function takeInviteFromLocation(): string | null {
  const match = /(?:^#|&)invite=([^&]+)/.exec(window.location.hash);
  if (!match) return null;
  window.history.replaceState(null, "", window.location.pathname + window.location.search);
  try {
    return decodeURIComponent(match[1]);
  } catch {
    return match[1];
  }
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

/** When the catalog's prices were checked against their shops. */
function catalogChecks(catalog: Catalog): string {
  return formatCheckedRange(
    catalog.products.flatMap((product) =>
      product.variants.flatMap((variant) => variant.offers.map((offer) => offer.source.checked_at)),
    ),
  );
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
  // The request state as last shown, for an answer that arrives while another request runs.
  const shownRequestState = useRef<RequestState>("idle");
  useEffect(() => {
    shownRequestState.current = requestState;
  }, [requestState]);
  const [saved, setSaved] = useState<SavedPlan[]>(() => loadSavedPlans());
  const [savedMessage, setSavedMessage] = useState<string | null>(null);
  const [tryOnLook, setTryOnLook] = useState("look_1");
  const [mode, setMode] = useState<"plan" | "design">("plan");
  const [concept, setConcept] = useState<CurrentConcept | null>(null);
  const [designState, setDesignState] = useState<RequestState>("idle");
  const [designMessage, setDesignMessage] = useState<string | null>(null);

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
  ): Promise<Restored> {
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

  async function restoreCurrent(): Promise<Restored> {
    if (!current) return { plan: null, response: null, error: null };
    const result = await restore({
      brief: current.brief,
      state: { ...current.state, locks, plan: current.plan },
      catalog_id: current.catalogId,
    });
    if (!result.plan && result.response) {
      // The plan no longer passes today's checks, so it cannot be named again; start afresh, and
      // say so whichever request found out, an update or a try-on.
      setCurrent(null);
      setLocks([]);
      keepCurrentPlan(null);
      // A request still running, such as the update that found out, says so once it answers.
      if (shownRequestState.current !== "loading") {
        setRequestState("error");
        setRequestMessage(new PlanLostError().message);
      }
    }
    return result;
  }

  /** Load the catalog for an open invite, then bring back this tab's working plan. A failed load
   * keeps the session and shows a retry. */
  async function openWorkspace(stopped: () => boolean = () => false) {
    setCatalogState("loading");
    let loaded: Catalog;
    try {
      loaded = parseCatalog(await readJson(await fetch("/api/catalog")));
    } catch {
      if (!stopped()) setCatalogState("error");
      return;
    }
    if (stopped()) return;
    setCatalog(loaded);
    setCatalogState("ready");
    const working = loadCurrentPlan();
    if (!working) return;
    setPreferences(preferencesFrom(working));
    // A running request, like a restore of a saved plan: a plan generated meanwhile would be
    // replaced by this one when it answered later.
    setRequestState("loading");
    setRequestMessage("Checking your last plan against today's catalog.");
    const result = await restore(working, loaded);
    // Kept items belong to the plan they were kept in, so a plan that was not restored keeps none.
    setLocks(result.plan ? working.state.locks : []);
    if (!stopped() && result.plan) {
      setRequestState("idle");
      setRequestMessage(null);
    }
    if (!stopped() && !result.plan) {
      // Only the server's refusal ends the working plan; after a network error a reload tries again.
      if (result.response) keepCurrentPlan(null);
      setRequestState("error");
      setRequestMessage(
        result.response
          ? "Your last plan no longer passes today's checks. Generate a new plan, or open a saved one below."
          : errorMessage(result.error),
      );
    }
  }

  useEffect(() => {
    let cancelled = false;
    async function start() {
      const invite = takeInviteFromLocation();
      if (invite) {
        try {
          await postJson("/api/session/invite", { code: invite });
        } catch (error) {
          if (!cancelled) showInviteError(error);
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
      await openWorkspace(() => cancelled);
    }
    void start();
    function openPastedInvite() {
      const invite = takeInviteFromLocation();
      if (invite) void openInvite(invite);
    }
    window.addEventListener("hashchange", openPastedInvite);
    return () => {
      cancelled = true;
      window.removeEventListener("hashchange", openPastedInvite);
    };
  }, []);

  /** The access gate shows it until an invite opens, and the workspace after that. */
  function showInviteError(error: unknown) {
    const message = errorMessage(error);
    setGateMessage(message);
    setRequestState("error");
    setRequestMessage(message);
  }

  async function openInvite(code: string) {
    setGateBusy(true);
    setGateMessage(null);
    setRequestState("idle");
    setRequestMessage(null);
    let next: Session;
    try {
      next = parseSession(await postJson("/api/session/invite", { code }));
    } catch (error) {
      showInviteError(error);
      return;
    } finally {
      setGateBusy(false);
    }
    // The invite is open now; a catalog failure shows in the workspace, not as an invite error.
    setSession(next);
    await openWorkspace();
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

  /** Why the consent could not be saved, for the try-on panel to say; null once it is saved. It
   * leaves the planning request's state alone, which may be running meanwhile. */
  async function setPhotoConsent(consent: boolean): Promise<string | null> {
    try {
      setSession(parseSession(await postJson("/api/session/photo-consent", { consent })));
      return null;
    } catch (error) {
      return errorMessage(error);
    }
  }

  /** The kept items, also in the tab's working plan, so a reload keeps them. */
  function changeLocks(next: LockedPlacement[]) {
    setLocks(next);
    if (current && catalog) keepCurrentPlan(savedFrom(current, catalog, next));
  }

  function removeOwnedItem(id: string) {
    setPreferences((value) => ({ ...value, ownedItems: value.ownedItems.filter((item) => item.id !== id) }));
    changeLocks(locks.filter((lock) => !(lock.item.kind === "owned" && lock.item.id === id)));
  }

  function toggleLock(lookId: string, slot: Slot, item: ItemRef) {
    if (requestState === "loading") return;
    const matches = (lock: LockedPlacement) =>
      lock.look_id === lookId && lock.slot === slot && sameItem(lock.item, item);
    changeLocks(locks.some(matches) ? locks.filter((lock) => !matches(lock)) : [...locks, { look_id: lookId, slot, item }]);
  }

  /** The brief and the checked fields both modes send, or what to fix first. */
  function checkedPreferences(): ConceptRequest | string {
    // The field's required check accepts spaces alone, which the server refuses.
    if (!preferences.styleGoal.trim()) return "Describe what the looks should work for.";
    const colors = preferences.colors.map((color) => color.trim()).filter(Boolean);
    if (colors.length > 5 || colors.some((color) => color.length > 30)) {
      return "Enter up to five colors, with no more than 30 characters each.";
    }
    const amountCents = Math.round(Number(preferences.budgetAmount) * 100);
    if (!Number.isSafeInteger(amountCents) || amountCents <= 0) return "Enter a budget greater than zero.";
    const parsedFit = parseFit(preferences.fit);
    if (!parsedFit.ok) return parsedFit.error;
    return {
      brief: {
        style_goal: preferences.styleGoal.trim(),
        preferred_colors: colors,
        occasion: OCCASIONS.find((occasion) => occasion === preferences.occasion) ?? null,
        season: SEASONS.find((season) => season === preferences.season) ?? null,
      },
      state: {
        budget: { amount_cents: amountCents, mode: preferences.budgetMode },
        material_exclusions: preferences.exclusions,
        footwear: preferences.footwear,
        ...(parsedFit.fit ? { fit: parsedFit.fit } : {}),
      },
    };
  }

  function buildRequest(replacement: ReplacementTarget | null): { request: PlanRequest; key: string } | string {
    const checked = checkedPreferences();
    if (typeof checked === "string") return checked;
    // The server refuses a lock on an owned item the request does not send, so a kept item the
    // person has since removed from their items is not sent as kept.
    const listed = new Set(preferences.ownedItems.map((item) => item.id));
    const sendable = (lock: LockedPlacement) => lock.item.kind !== "owned" || listed.has(lock.item.id);
    const state: StateInput = {
      ...checked.state,
      owned_items: preferences.ownedItems,
      locks: (replacement && current ? locksForReplacement(current.plan, replacement, locks) : current ? locks : []).filter(
        sendable,
      ),
    };
    return {
      request: { brief: checked.brief, state, replacement, plan_token: current?.token ?? null },
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
      const restored = await restoreCurrent();
      // Only the server's refusal clears the plan; a failed connection leaves it for another try.
      if (!restored.plan) throw restored.response || !restored.error ? new PlanLostError() : restored.error;
      return parsePlanResponse(await send(restored.plan.token));
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
        // Keep the locks the request sent; a replacement's temporary locks hold only for that
        // request, so it keeps the person's own.
        adopt(result, request.brief, replacement ? { ...request.state, locks } : request.state, key);
        revealResults();
        setRequestState("success");
        // The plan changed, so an earlier "Saved" no longer describes what the page shows.
        setSavedMessage(null);
        setRequestMessage(
          replacement
            ? "The item was replaced. All three looks passed the catalog checks."
            : passedChecksText(result.plan, catalog, request.state.fit),
        );
      } else {
        setRequestState("error");
        // Only a request no plan can meet is refused before it is counted.
        setRequestMessage(
          failureMessage(result, replacement !== null, request.state.locks.length > 0, (check) =>
            readableDetail(check, catalog, request.state.owned_items),
          ) + (result.outcome === "infeasible" ? "" : " This request still counts against your invite."),
        );
      }
    } catch (error) {
      setRequestState("error");
      setRequestMessage(error instanceof PlanLostError ? error.message : errorMessage(error));
    } finally {
      // The submit button is disabled while planning, which drops keyboard focus; bring it to the
      // results so the next Tab does not start again from the top of the page.
      if (document.activeElement === document.body) document.getElementById("results-title")?.focus();
      void refreshSession();
    }
  }

  async function requestConcept() {
    if (!catalog || requestState === "loading" || designState === "loading" || !session?.design_open) return;
    const request = checkedPreferences();
    if (typeof request === "string") {
      setDesignState("error");
      setDesignMessage(request);
      return;
    }
    setDesignState("loading");
    setDesignMessage("Designing one look, then looking for the closest products in the catalog.");
    try {
      const result = parseConceptResponse(
        await postJson("/api/concepts", request, { "X-Operation-Id": newOperationId() }),
      );
      if (result.outcome === "success" && result.concept && result.concept_token && result.shoppable) {
        setConcept({
          concept: result.concept,
          token: result.concept_token,
          shoppable: result.shoppable,
          matchingFailed: result.matching_failed,
        });
        revealResults("concept-title");
        setDesignState("success");
        setDesignMessage("Your concept is ready.");
      } else {
        setDesignState("error");
        setDesignMessage(conceptFailureMessage(result));
      }
    } catch (error) {
      setDesignState("error");
      setDesignMessage(errorMessage(error));
    } finally {
      // As for plans: the disabled button drops focus, so bring it to the concept.
      if (document.activeElement === document.body) document.getElementById("concept-title")?.focus();
      void refreshSession();
    }
  }

  function saveCurrent() {
    if (!current || !catalog) return;
    const entry = savedFrom(current, catalog, locks);
    const before = loadSavedPlans();
    if (!savePlan(entry)) {
      setSavedMessage("This browser did not allow saving.");
      return;
    }
    const after = loadSavedPlans();
    const removed = before.find((plan) => !after.some((kept) => kept.id === plan.id));
    setSavedMessage(
      `Saved "${entry.name}" on this device.` +
        (removed ? ` It keeps ${MAX_SAVED} plans, so "${removed.name}" was removed.` : ""),
    );
    setSaved(after);
  }

  async function restoreSaved(entry: SavedPlan) {
    // A restore makes the plan current on the server, and showing it needs the catalog.
    if (!catalog || requestState === "loading") return;
    setRequestState("loading");
    setRequestMessage(`Checking "${entry.name}" against today's catalog.`);
    setSavedMessage(`Checking "${entry.name}" against today's catalog.`);
    const result = await restore(entry);
    if (result.plan) {
      setPreferences(preferencesFrom(entry));
      setLocks(entry.state.locks);
      setRequestState("success");
      setRequestMessage(
        result.response?.catalog_changed
          ? "The saved plan still passes the catalog checks. The catalog changed since you saved it, so prices and offers were checked again."
          : "The saved plan still passes the catalog checks.",
      );
      setSavedMessage(`Restored "${entry.name}". Its looks are now under "Three looks" above.`);
    } else {
      const reason = result.response
        ? result.response.violations
            .slice(0, 3)
            .map((violation) => readableDetail(violation, catalog, entry.state.owned_items))
            .join(" ")
        : errorMessage(result.error);
      setRequestState("error");
      setRequestMessage(
        result.response
          ? `The saved plan no longer passes today's checks: ${reason} Its saved shopping list is still available below.`
          : reason,
      );
      setSavedMessage(`"${entry.name}" was not restored: ${reason}`);
    }
    // The buttons that started this are disabled or gone, so the answer takes the focus.
    if (document.activeElement === document.body) document.getElementById("saved-message")?.focus();
  }

  function deleteSaved(id: string) {
    const message = deleteSavedPlan(id) ? "Deleted from this device." : "This browser did not allow deleting.";
    flushSync(() => {
      setSavedMessage(message);
      setSaved(loadSavedPlans());
    });
    // The deleted plan's buttons are gone, so the answer takes the focus, as after a restore.
    if (document.activeElement === document.body) document.getElementById("saved-message")?.focus();
  }

  const loading = requestState === "loading";
  const designLoading = designState === "loading";
  const design = mode === "design";
  const currentKey = preferenceKey(preferences);
  const planCurrent = Boolean(current && current.preferenceKey === currentKey);
  // While the store cannot be read, the server cannot say the age was confirmed. Model calls are
  // closed then, so the page stays and plans remain available, as docs/SPEC.md promises.
  const ready = session !== null && session.beta && session.invited && (session.age_confirmed || !session.accounting);
  // One model request at a time per invite, so neither mode submits while the other runs.
  const canSubmit = ready && catalogState === "ready" && !loading && !designLoading && session.planning_open;
  const canDesign = canSubmit && session.design_open;
  const lines = current && catalog ? shoppingList(current.plan, catalog, current.state.owned_items, current.state.fit) : [];
  const unchecked = current && catalog ? uncheckedText(current.plan, catalog, current.state.fit) : null;
  // A restore replaces the plan, its kept items and the form, so it asks while any of them would be lost.
  const working = current && catalog ? savedFrom(current, catalog, locks) : null;
  const restoreWarning = [
    working && !saved.some((entry) => sameWork(entry, working))
      ? "Your current plan and its kept items are not saved, and restoring replaces them. Save them first if you want to keep them."
      : null,
    (current ? !planCurrent : currentKey !== preferenceKey(INITIAL_PREFERENCES))
      ? "Your changes to the preferences are not applied or saved, and restoring replaces them."
      : null,
  ]
    .filter(Boolean)
    .join(" ");
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
  const designClosedText =
    closedText ?? (session && !session.design_open ? "Design my look is not available right now." : null);

  return (
    <main className="app-shell">
      <header className="topbar no-print">
        <a className="wordmark" href="/" aria-label="FullAura home">FullAura</a>
        <p className="environment-label">Closed beta</p>
        {ready && session.accounting && !session.pilot_ended && (
          <p className="environment-label">
            {remainingPlans} of {session.plan_requests_limit} planning requests left
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
          {/* A saved plan stays on this device, so it can be read and deleted without the beta. */}
          {saved.length > 0 && <SavedPlans busy={false} message={savedMessage} onDelete={deleteSaved} saved={saved} />}
        </div>
      ) : (
        <div className="workspace">
          <div className="workspace-heading no-print">
            <div>
              {design ? (
                <>
                  <h1>Your look first. Then real products.</h1>
                  <p className="page-description">
                    An experimental mode: a look designed from your wishes, tried on you, and for each item the closest
                    real product that ships to Austria, marked as a close match, an alternative or no match.
                  </p>
                </>
              ) : (
                <>
                  <h1>Three looks. Rules checked.</h1>
                  <p className="page-description">
                    Real products that ship to Austria, checked against your budget, sizes, shoes and materials. Keep
                    what you like, replace the rest, then try a look on.
                  </p>
                </>
              )}
            </div>
            <p className="catalog-status" aria-live="polite">
              {catalogState === "loading" && "Loading catalog"}
              {catalogState === "ready" && catalog && `${catalog.products.length} products. ${catalogChecks(catalog)}`}
              {catalogState === "error" && "Catalog unavailable"}
            </p>
          </div>

          <div className="workspace-grid">
            <section className="preferences-panel no-print" aria-labelledby="preferences-title">
              <div className="panel-heading">
                <h2 id="preferences-title">Your preferences</h2>
                <p>You set your sizes and limits; FullAura never reads them from photos.</p>
              </div>
              {/* Stays while a concept is shown, so a closed design budget does not strand the page. */}
              {(session.design_open || design) && (
                <fieldset className="budget-mode mode-switch">
                  <legend>Mode</legend>
                  <label className="radio-choice">
                    <input checked={!design} name="mode" onChange={() => setMode("plan")} type="radio" />
                    <span>Three looks from real products</span>
                  </label>
                  <label className="radio-choice">
                    <input checked={design} name="mode" onChange={() => setMode("design")} type="radio" />
                    <span>Design my look (experimental)</span>
                  </label>
                </fieldset>
              )}
              <PreferencesForm
                canSubmit={design ? canDesign : canSubmit}
                closedText={design ? designClosedText : closedText}
                design={design}
                hasPlan={design ? concept !== null : current !== null}
                loading={design ? designLoading : loading}
                onRemoveOwnedItem={removeOwnedItem}
                onSubmit={() => void (design ? requestConcept() : requestPlan())}
                preferences={preferences}
                quotaText={`Uses one of ${remainingPlans} planning requests left for this invite.`}
                setPreferences={setPreferences}
              />
            </section>

            <section className="results-panel" aria-labelledby={design ? "concept-title" : "results-title"}>
              {design ? (
                <>
                  <div className="results-heading">
                    <div>
                      <h2 id="concept-title" tabIndex={-1}>Your concept</h2>
                      <p className="no-print">
                        One look the stylist model describes from your preferences, and for each item the closest
                        real product in the catalog.
                      </p>
                    </div>
                  </div>

                  {designMessage && (concept || designState !== "error") && (
                    <p
                      className={`request-message no-print${designState === "error" ? " is-error" : ""}`}
                      role={designState === "error" ? "alert" : "status"}
                    >
                      {designMessage}
                      {designState === "error" && concept && " The concept below is from the last successful request."}
                    </p>
                  )}

                  {catalogState === "loading" || (designLoading && !concept) ? (
                    <div className="loading-state is-single" role="status">
                      <span className="visually-hidden">{designLoading ? "Designing a look" : "Loading the catalog"}</span>
                      <div aria-hidden="true" className="look-skeleton">
                        <span className="skeleton-title" />
                        <span className="skeleton-row" />
                        <span className="skeleton-row" />
                      </div>
                    </div>
                  ) : catalogState === "error" ? (
                    <div className="empty-state is-error" role="alert">
                      <h3>The catalog could not be loaded</h3>
                      <p>Your invite is open. Try again in a moment.</p>
                      <button className="secondary-button" onClick={() => void openWorkspace()} type="button">
                        Load the catalog again
                      </button>
                    </div>
                  ) : concept && catalog ? (
                    <>
                      <ConceptCard
                        catalog={catalog}
                        concept={concept.concept}
                        matchingFailed={concept.matchingFailed}
                        shoppable={concept.shoppable}
                      />
                      <TryOnPanel
                        key="concept-try-on"
                        onConsent={setPhotoConsent}
                        onFinished={() => void refreshSession()}
                        session={session}
                        target={{ kind: "concept", token: concept.token }}
                      />
                    </>
                  ) : designState === "error" && designMessage ? (
                    <div className="empty-state is-error" role="alert">
                      <h3>No concept yet</h3>
                      <p>{designMessage}</p>
                    </div>
                  ) : (
                    <div className="empty-state">
                      <h3>Your concept will appear here</h3>
                      <p>Describe what the look is for and set a budget, then design a look.</p>
                    </div>
                  )}
                </>
              ) : (
                <>
                  <div className="results-heading">
                    <div>
                      <h2 id="results-title" tabIndex={-1}>Three looks</h2>
                      <p className="no-print">
                        Keep items to hold them in place through an update, or replace one item; every other position
                        stays and all three looks are checked again.
                      </p>
                    </div>
                    {current && (
                      <p className="plan-total">
                        Items total, each once
                        <strong>{formatMoney(shoppingTotal(lines))}</strong>
                        <span className="plan-budget">
                          {budgetKept(current.state.budget.amount_cents, current.state.budget.mode)}
                        </span>
                      </p>
                    )}
                  </div>

                  {requestMessage && (current || requestState !== "error") && (
                    <p
                      className={`request-message no-print${requestState === "error" ? " is-error" : ""}`}
                      role={requestState === "error" ? "alert" : "status"}
                    >
                      {requestMessage}
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
                      <p>Your invite is open. Try again in a moment; your saved plans below are still available.</p>
                      <button className="secondary-button" onClick={() => void openWorkspace()} type="button">
                        Load the catalog again
                      </button>
                    </div>
                  ) : current && catalog ? (
                    <>
                      {unchecked && <p className="plan-unchecked">{unchecked}</p>}
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
                              const heading = document.getElementById("tryon-title");
                              heading?.scrollIntoView?.({ block: "start" });
                              heading?.focus({ preventScroll: true });
                            }}
                            ownedItems={current.state.owned_items}
                            ownedItemsInForm={preferences.ownedItems.map((item) => item.id)}
                            ownedNotices={current.notices}
                            plan={current.plan}
                            preferencesChanged={!planCurrent && canSubmit}
                            replaceDisabled={!planCurrent || !canSubmit}
                          />
                        ))}
                      </div>
                      <TryOnPanel
                        key="plan-try-on"
                        onConsent={setPhotoConsent}
                        onFinished={() => void refreshSession()}
                        session={session}
                        target={{
                          kind: "plan",
                          token: current.token,
                          lookId: current.plan.looks.some((look) => look.id === tryOnLook)
                            ? tryOnLook
                            : current.plan.looks[0].id,
                          lookIds: current.plan.looks.map((look) => look.id),
                          onRestore: async () => (await restoreCurrent()).plan?.token ?? null,
                          onSelectLook: setTryOnLook,
                        }}
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
                </>
              )}

              {!design && (
                <SavedPlans
                  busy={loading || !catalog}
                  canSave={current !== null}
                  message={savedMessage}
                  onDelete={deleteSaved}
                  onRestore={(entry) => void restoreSaved(entry)}
                  onSave={saveCurrent}
                  restoreWarning={restoreWarning}
                  saved={saved}
                />
              )}
              <FeedbackForm
                feedbackIds={session.feedback_ids}
                onChanged={() => void refreshSession()}
                recordsPurgeAt={session.records_purge_at}
              />
            </section>
          </div>
        </div>
      )}
      <PrivacyNotice designOpen={session?.design_open ?? false} recordsPurgeAt={session?.records_purge_at ?? null} />
    </main>
  );
}

function failureMessage(
  result: PlanResponse,
  replacement: boolean,
  hadLocks: boolean,
  readable: (check: { rule: string | null; detail: string }) => string,
): string {
  const details = (items: { rule: string | null; detail: string }[]) => items.map(readable).join("; ");
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
    : "No plan passed the catalog checks. Try a higher budget or fewer limits.";
  // Fit violations come first, so a stated fit that no item meets is always explained.
  const violations = [
    ...result.violations.filter((item) => item.rule === "fit"),
    ...result.violations.filter((item) => item.rule !== "fit"),
  ]
    .slice(0, 3)
    .map(readable)
    .join(" ");
  return violations ? `${summary} ${violations}` : summary;
}

function conceptFailureMessage(result: ConceptResponse): string {
  if (result.outcome === "provider_timeout") {
    return "The design request timed out. Try again with the same or fewer constraints.";
  }
  if (result.outcome === "provider_error") return "The planning provider could not complete this design request.";
  const summary = "No concept passed the checks. Adjust your preferences and try again.";
  const diagnostic = result.error?.slice(0, 300) ?? "";
  return diagnostic ? `${summary} ${diagnostic}` : summary;
}

/** On a narrow screen the results sit below the form; bring them into view once they change. */
function revealResults(headingId = "results-title") {
  const heading = document.getElementById(headingId);
  if (!heading) return;
  const top = heading.getBoundingClientRect().top;
  if (top < 0 || top > window.innerHeight) heading.scrollIntoView?.({ block: "start" });
}

function savedFrom(plan: CurrentPlan, catalog: Catalog, locks: LockedPlacement[] = plan.state.locks): SavedPlan {
  const looks = plan.plan.looks.map((look) => lookLabel(look.id));
  const name = plan.brief.style_goal.slice(0, 60) || `Plan with ${looks.length} looks`;
  return {
    id: plan.token.slice(0, 16),
    saved_at: new Date().toISOString(),
    name,
    brief: plan.brief,
    state: { ...plan.state, locks, plan: plan.plan },
    catalog_id: plan.catalogId,
    shopping_list: shoppingList(plan.plan, catalog, plan.state.owned_items, plan.state.fit),
  };
}
