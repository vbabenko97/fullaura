import { isRecord } from "./api";
import type { SavedPlan } from "./types";

/* Plans saved on this device. They hold the plan, the preferences it was checked against and
 * a copy of the shopping list; never photos or try-on images. Storage can be unavailable, as in
 * a private window, so every access tolerates failure. */
const SAVED_KEY = "fullaura.saved-plans.v1";
const CURRENT_KEY = "fullaura.current-plan.v1";
export const MAX_SAVED = 10;

function read(storage: () => Storage, key: string): unknown {
  try {
    const text = storage().getItem(key);
    return text === null ? null : JSON.parse(text);
  } catch {
    return null;
  }
}

function write(storage: () => Storage, key: string, value: unknown): boolean {
  try {
    storage().setItem(key, JSON.stringify(value));
    return true;
  } catch {
    return false;
  }
}

function isSavedPlan(value: unknown): value is SavedPlan {
  return (
    isRecord(value) &&
    typeof value.id === "string" &&
    typeof value.saved_at === "string" &&
    typeof value.name === "string" &&
    isRecord(value.brief) &&
    isRecord(value.state) &&
    isRecord(value.state.plan) &&
    typeof value.catalog_id === "string" &&
    Array.isArray(value.shopping_list)
  );
}

export function loadSavedPlans(): SavedPlan[] {
  const value = read(() => localStorage, SAVED_KEY);
  return Array.isArray(value) ? value.filter(isSavedPlan) : [];
}

/** Keeps the newest plans first; false when the device refused to store them. */
export function savePlan(plan: SavedPlan): boolean {
  const plans = [plan, ...loadSavedPlans().filter((saved) => saved.id !== plan.id)];
  return write(() => localStorage, SAVED_KEY, plans.slice(0, MAX_SAVED));
}

/** The name to save a plan under. Plans with one style goal would read the same in the list, so a
 * later one takes a number; a plan saved again keeps the name it has. */
export function savedName(plan: SavedPlan, saved: SavedPlan[]): string {
  const same = saved.find((entry) => entry.id === plan.id);
  if (same) return same.name;
  const taken = new Set(saved.map((entry) => entry.name));
  let name = plan.name;
  for (let number = 2; taken.has(name); number += 1) name = `${plan.name} (${number})`;
  return name;
}

export function deleteSavedPlan(id: string): boolean {
  return write(
    () => localStorage,
    SAVED_KEY,
    loadSavedPlans().filter((saved) => saved.id !== id),
  );
}

/** The working plan of this tab, so a reload can restore it. */
export function loadCurrentPlan(): SavedPlan | null {
  const value = read(() => sessionStorage, CURRENT_KEY);
  return isSavedPlan(value) ? value : null;
}

export function keepCurrentPlan(plan: SavedPlan | null): void {
  if (plan === null) {
    try {
      sessionStorage.removeItem(CURRENT_KEY);
    } catch {
      // Nothing to clear when storage is unavailable.
    }
    return;
  }
  write(() => sessionStorage, CURRENT_KEY, plan);
}
