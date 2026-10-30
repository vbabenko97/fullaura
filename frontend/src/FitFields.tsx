import type { Dispatch, SetStateAction } from "react";
import { FIT_LINES, GARMENT_TYPES, LETTER_SIZES, MAX_FIT_SIZES } from "./types";
import type { FitDraft, FitLine, GarmentType } from "./types";

const LINE_LABELS: Record<FitLine, string> = { men: "Men's", women: "Women's" };
const GARMENT_TYPE_LABELS: Record<GarmentType, string> = {
  trousers: "Trousers",
  jeans: "Jeans",
  shorts: "Shorts",
  skirt: "Skirts",
  leggings: "Leggings",
  dress: "Dresses",
};

// Keeps the order of choices, so the same selection always produces the same request.
function toggled<T>(choices: readonly T[], selected: readonly T[], value: T): T[] {
  return choices.filter((choice) =>
    choice === value ? !selected.includes(choice) : selected.includes(choice),
  );
}

interface FitFieldsProps {
  fitDraft: FitDraft;
  setFitDraft: Dispatch<SetStateAction<FitDraft>>;
}

export function FitFields({ fitDraft, setFitDraft }: FitFieldsProps) {
  return (
    <section className="fit-fields" aria-labelledby="fit-title">
      <h3 id="fit-title">Who the looks are for</h3>
      <fieldset className="fit-choices">
        <legend>Clothing lines</legend>
        <div className="choice-grid">
          {FIT_LINES.map((line) => (
            <label className="check-choice" key={line}>
              <input
                checked={fitDraft.lines.includes(line)}
                onChange={() =>
                  setFitDraft((current) => ({
                    ...current,
                    lines: toggled(FIT_LINES, current.lines, line),
                  }))
                }
                type="checkbox"
                value={line}
              />
              <span>{LINE_LABELS[line]}</span>
            </label>
          ))}
        </div>
        <p className="field-help">None checked shows every line. Unisex items always count.</p>
      </fieldset>

      <fieldset className="fit-choices">
        <legend>Bottoms and one-pieces</legend>
        <div className="choice-grid">
          {GARMENT_TYPES.map((garmentType) => (
            <label className="check-choice" key={garmentType}>
              <input
                checked={fitDraft.garmentTypes.includes(garmentType)}
                onChange={() =>
                  setFitDraft((current) => ({
                    ...current,
                    garmentTypes: toggled(GARMENT_TYPES, current.garmentTypes, garmentType),
                  }))
                }
                type="checkbox"
                value={garmentType}
              />
              <span>{GARMENT_TYPE_LABELS[garmentType]}</span>
            </label>
          ))}
        </div>
        <p className="field-help">None checked allows all.</p>
      </fieldset>

      <div className="form-field">
        <label htmlFor="fit-letter-size">Letter size</label>
        <select
          id="fit-letter-size"
          onChange={(event) => {
            const letterSize =
              LETTER_SIZES.find((size) => size === event.target.value) ?? "";
            setFitDraft((current) => ({ ...current, letterSize }));
          }}
          value={fitDraft.letterSize}
        >
          <option value="">Not set</option>
          {LETTER_SIZES.map((size) => (
            <option key={size} value={size}>{size}</option>
          ))}
        </select>
      </div>

      <div className="form-field">
        <label htmlFor="fit-waist-sizes">Waist sizes</label>
        <input
          autoComplete="off"
          id="fit-waist-sizes"
          maxLength={100}
          onChange={(event) => {
            const waistSizes = event.target.value;
            setFitDraft((current) => ({ ...current, waistSizes }));
          }}
          placeholder="30, 30/34"
          type="text"
          value={fitDraft.waistSizes}
        />
        <p className="field-help">
          Separate sizes with commas. A waist without a length, such as 33, matches every length.
        </p>
      </div>

      <div className="form-field">
        <label htmlFor="fit-shoe-sizes">EU shoe sizes</label>
        <input
          autoComplete="off"
          id="fit-shoe-sizes"
          maxLength={100}
          onChange={(event) => {
            const shoeSizes = event.target.value;
            setFitDraft((current) => ({ ...current, shoeSizes }));
          }}
          placeholder="39.5, 40"
          type="text"
          value={fitDraft.shoeSizes}
        />
        <p className="field-help">
          Separate sizes with commas. Half sizes are rare, so you can add the next whole size too.
        </p>
      </div>
      <p className="field-help">Up to {MAX_FIT_SIZES} sizes in total.</p>
    </section>
  );
}
