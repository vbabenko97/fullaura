import type { Dispatch, FormEvent, SetStateAction } from "react";
import { readCategory } from "./api";
import { FitFields } from "./FitFields";
import { budgetMeaning, conceptBudgetMeaning } from "./format";
import { OwnedItemsFields } from "./OwnedItemsFields";
import type { Preferences } from "./plan";
import { FOOTWEAR_CHOICES, MATERIALS, OCCASIONS, SEASONS } from "./types";
import type { FitDraft, FootwearChoice, Material, Occasion, OwnedItem, Season } from "./types";

const OCCASION_LABELS: Record<Occasion, string> = {
  office: "Office",
  casual: "Everyday casual",
  evening: "Evening out",
  travel: "Travel",
  special_event: "Special event",
};
const SEASON_LABELS: Record<Season, string> = {
  spring: "Spring",
  summer: "Summer",
  autumn: "Autumn",
  winter: "Winter",
};
const FOOTWEAR_LABELS: Record<FootwearChoice, string> = {
  closed: "Closed shoes",
  open: "Open shoes",
  any: "Any shoes",
};

interface PreferencesFormProps {
  preferences: Preferences;
  setPreferences: Dispatch<SetStateAction<Preferences>>;
  onRemoveOwnedItem: (id: string) => void;
  onSubmit: () => void;
  canSubmit: boolean;
  loading: boolean;
  /** A plan is shown, or in Design my look a concept. */
  hasPlan: boolean;
  /** Design my look: one concept, without owned items. */
  design: boolean;
  quotaText: string | null;
  closedText: string | null;
}

export function PreferencesForm({
  preferences,
  setPreferences,
  onRemoveOwnedItem,
  onSubmit,
  canSubmit,
  loading,
  hasPlan,
  design,
  quotaText,
  closedText,
}: PreferencesFormProps) {
  function update<K extends keyof Preferences>(key: K, value: Preferences[K]) {
    setPreferences((current) => ({ ...current, [key]: value }));
  }

  function setFitDraft(next: SetStateAction<FitDraft>) {
    setPreferences((current) => ({
      ...current,
      fit: typeof next === "function" ? next(current.fit) : next,
    }));
  }

  function toggleExclusion(material: Material) {
    setPreferences((current) => ({
      ...current,
      exclusions: current.exclusions.includes(material)
        ? current.exclusions.filter((item) => item !== material)
        : [...current.exclusions, material],
    }));
  }

  function addOwnedItem() {
    setPreferences((current) => {
      if (current.ownedItems.length >= 3) return current;
      const next = Math.max(0, ...current.ownedItems.map((item) => Number(item.id.replace("owned-", "")))) + 1;
      return {
        ...current,
        ownedItems: [...current.ownedItems, { id: `owned-${next}`, category: "top", color: "", description: "" }],
      };
    });
  }

  function updateOwnedItem(id: string, field: keyof OwnedItem, value: string) {
    setPreferences((current) => ({
      ...current,
      ownedItems: current.ownedItems.map((item) =>
        item.id === id ? { ...item, [field]: field === "category" ? readCategory(value) : value } : item,
      ),
    }));
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    onSubmit();
  }

  /** The browser cannot focus a required field in a closed section, so it refuses the submit
   * without saying why. Opening the section lets it point at the field; WebKit still focuses
   * nothing, so the field takes the focus unless the browser gave it to an invalid field. */
  function openSectionOfInvalidField(event: FormEvent<HTMLFormElement>) {
    const field = event.target;
    if (!(field instanceof HTMLElement)) return;
    const section = field.closest("details");
    if (!section || section.open) return;
    section.open = true;
    setTimeout(() => {
      const focused = document.activeElement as Partial<HTMLInputElement> | null;
      if (focused?.validity?.valid !== false) field.focus();
    });
  }

  return (
    <form className="preferences-form" onInvalidCapture={openSectionOfInvalidField} onSubmit={submit}>
      <div className="field-pair">
        <div className="form-field">
          <label htmlFor="occasion">Occasion</label>
          <select id="occasion" onChange={(event) => update("occasion", event.target.value)} value={preferences.occasion}>
            <option value="">Not set</option>
            {OCCASIONS.map((occasion) => (
              <option key={occasion} value={occasion}>{OCCASION_LABELS[occasion]}</option>
            ))}
          </select>
        </div>
        <div className="form-field">
          <label htmlFor="season">Season</label>
          <select id="season" onChange={(event) => update("season", event.target.value)} value={preferences.season}>
            <option value="">Not set</option>
            {SEASONS.map((season) => (
              <option key={season} value={season}>{SEASON_LABELS[season]}</option>
            ))}
          </select>
        </div>
      </div>

      <div className="form-field">
        <label htmlFor="style-goal">What should the looks work for?</label>
        <textarea
          autoComplete="off"
          id="style-goal"
          maxLength={300}
          onChange={(event) => update("styleGoal", event.target.value)}
          placeholder="For example, relaxed outfits for autumn office days"
          required
          rows={3}
          value={preferences.styleGoal}
        />
      </div>

      <div className="form-field">
        <label htmlFor="preferred-colors">Colors you prefer</label>
        <input
          autoComplete="off"
          id="preferred-colors"
          maxLength={164}
          onChange={(event) => update("colors", event.target.value.split(","))}
          placeholder="Navy, cream, olive"
          type="text"
          value={preferences.colors.join(",")}
        />
        <p className="field-help">Up to five, separated by commas. Occasion, season and colors are wishes for the stylist model; code does not check them.</p>
      </div>

      <div className="field-pair">
        <div className="form-field">
          <label htmlFor="budget-amount">Budget in EUR</label>
          <input
            id="budget-amount"
            inputMode="decimal"
            min="0.01"
            onChange={(event) => update("budgetAmount", event.target.value)}
            required
            step="0.01"
            type="number"
            value={preferences.budgetAmount}
          />
        </div>
        {!design && (
          <fieldset className="budget-mode">
            <legend>Budget applies to</legend>
            <label className="radio-choice">
              <input
                checked={preferences.budgetMode === "per_look"}
                name="budget-mode"
                onChange={() => update("budgetMode", "per_look")}
                type="radio"
              />
              <span>Each look</span>
            </label>
            <label className="radio-choice">
              <input
                checked={preferences.budgetMode === "whole_plan"}
                name="budget-mode"
                onChange={() => update("budgetMode", "whole_plan")}
                type="radio"
              />
              <span>All three looks together</span>
            </label>
          </fieldset>
        )}
      </div>

      <fieldset className="budget-mode">
        <legend>Shoes</legend>
        {FOOTWEAR_CHOICES.map((choice) => (
          <label className="radio-choice" key={choice}>
            <input
              checked={preferences.footwear === choice}
              name="footwear"
              onChange={() => update("footwear", choice)}
              type="radio"
            />
            <span>{FOOTWEAR_LABELS[choice]}</span>
          </label>
        ))}
        <p className="field-help">
          With closed or open shoes, every look gets shoes of that kind, and shoes whose kind the shop does not state are left out.
        </p>
      </fieldset>

      <FitFields fitDraft={preferences.fit} setFitDraft={setFitDraft} />

      <details className="exclusions-disclosure">
        <summary>
          <span>Avoid materials</span>
          <span className="selection-count">{preferences.exclusions.length} selected</span>
        </summary>
        <p className="field-help">
          Exclusions apply to new purchases. Only materials named in a product's record are checked, so a selection also removes products whose composition is incomplete.
        </p>
        <div className="material-list">
          {MATERIALS.map((material) => (
            <label className="check-choice" key={material}>
              <input
                checked={preferences.exclusions.includes(material)}
                onChange={() => toggleExclusion(material)}
                type="checkbox"
                value={material}
              />
              <span>{material}</span>
            </label>
          ))}
        </div>
      </details>

      {design ? (
        <p className="field-help">Design my look does not use items you own.</p>
      ) : (
        <details className="exclusions-disclosure">
          <summary>
            <span>Items you already own</span>
            <span className="selection-count">{preferences.ownedItems.length} added</span>
          </summary>
          <OwnedItemsFields
            ownedItems={preferences.ownedItems}
            onAddItem={addOwnedItem}
            onUpdateItem={updateOwnedItem}
            onRemoveItem={onRemoveOwnedItem}
          />
        </details>
      )}

      <p className="field-help" id="budget-meaning">
        {(design ? conceptBudgetMeaning : budgetMeaning)(
          preferences.budgetAmount.trim() ? Math.round(Number(preferences.budgetAmount) * 100) : null,
          preferences.budgetMode,
        )}{" "}
        Prices are item prices, without delivery or returns.
      </p>
      <button
        aria-busy={loading}
        aria-describedby="budget-meaning"
        className={`primary-button${loading ? " is-loading" : ""}`}
        disabled={!canSubmit}
        type="submit"
      >
        {design
          ? loading
            ? "Designing a look…"
            : hasPlan
              ? "Design another"
              : "Design my look"
          : loading
            ? "Planning looks…"
            : hasPlan
              ? "Update three looks"
              : "Generate three looks"}
      </button>
      {closedText ? (
        <p className="submit-help" role="status">{closedText}</p>
      ) : (
        quotaText && <p className="submit-help">{quotaText}</p>
      )}
    </form>
  );
}
