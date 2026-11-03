import { isRecord, parseFindLine } from "./api";
import type { FindResponse, FindScope } from "./types";

/* The tab's last search, so that a reload does not lose what a request paid for. A phone can reload
 * a page by itself when the person comes back from a shop's tab. It holds the form and the result,
 * never photos or a try-on, and goes with the tab. Storage can be unavailable, as in a private
 * window, so every access tolerates failure. */
const KEY = "fullaura.last-search.v1";
const SCOPES: readonly string[] = ["look", "upper_body", "item"];
const LINES: readonly string[] = ["men", "women", "any"];

export type FindLine = "men" | "women" | "any";

export interface SearchForm {
  request: string;
  scope: FindScope;
  line: FindLine;
  sizes: string;
  budget: string;
}

export interface LastSearch {
  form: SearchForm;
  /** Set while the search may still be running, so the page can collect its answer. */
  operation_id: string | null;
  /** When that search was sent, in milliseconds since the epoch. */
  sent_at: number | null;
  result: FindResponse | null;
}

function readForm(value: unknown): SearchForm | null {
  if (
    isRecord(value) &&
    typeof value.request === "string" &&
    typeof value.sizes === "string" &&
    typeof value.budget === "string" &&
    typeof value.scope === "string" &&
    SCOPES.includes(value.scope) &&
    typeof value.line === "string" &&
    LINES.includes(value.line)
  ) {
    return {
      request: value.request,
      scope: value.scope as FindScope,
      line: value.line as FindLine,
      sizes: value.sizes,
      budget: value.budget,
    };
  }
  return null;
}

function readResult(value: unknown): FindResponse | null {
  const parsed = parseFindLine({ result: value });
  return parsed.kind === "result" ? parsed.result : null;
}

export function forgetLastSearch(): void {
  try {
    sessionStorage.removeItem(KEY);
  } catch {
    // Nothing to clear when storage is unavailable.
  }
}

/** The tab's last search when it was made under this invite. Another invite's is removed, as it
 * could be anyone's. What cannot be read counts as no search. */
export function loadLastSearch(scope: string | null): LastSearch | null {
  try {
    const text = sessionStorage.getItem(KEY);
    if (text === null) return null;
    const value: unknown = JSON.parse(text);
    if (!isRecord(value)) return null;
    if ((value.invite_scope ?? null) !== scope) {
      forgetLastSearch();
      return null;
    }
    const form = readForm(value.form);
    if (form === null) return null;
    return {
      form,
      operation_id: typeof value.operation_id === "string" ? value.operation_id : null,
      sent_at: typeof value.sent_at === "number" ? value.sent_at : null,
      result: value.result === null || value.result === undefined ? null : readResult(value.result),
    };
  } catch {
    return null;
  }
}

export function keepLastSearch(search: LastSearch, scope: string | null): void {
  try {
    sessionStorage.setItem(KEY, JSON.stringify({ ...search, invite_scope: scope }));
  } catch {
    // The search still works; only a reload would lose it.
  }
}
