import { useState } from "react";
import type { FormEvent } from "react";
import { errorMessage, postJson } from "./api";

interface FeedbackFormProps {
  feedbackIds: string[];
  onChanged: () => void;
}

const SCALE = [1, 2, 3, 4, 5] as const;

export function FeedbackForm({ feedbackIds, onChanged }: FeedbackFormProps) {
  const [wouldWear, setWouldWear] = useState<"" | "yes" | "no">("");
  const [usefulness, setUsefulness] = useState("");
  const [tryOn, setTryOn] = useState("");
  const [catalogProblem, setCatalogProblem] = useState("");
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ text: string; error: boolean } | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!wouldWear || !usefulness || !tryOn) return;
    setBusy(true);
    try {
      await postJson("/api/feedback", {
        would_wear: wouldWear === "yes",
        usefulness: Number(usefulness),
        try_on: tryOn === "cannot_assess" ? tryOn : Number(tryOn),
        catalog_problem: catalogProblem.trim(),
        comment: comment.trim(),
      });
      setMessage({ text: "Thank you. Your feedback is saved.", error: false });
      setWouldWear("");
      setUsefulness("");
      setTryOn("");
      setCatalogProblem("");
      setComment("");
      onChanged();
    } catch (error) {
      setMessage({ text: errorMessage(error), error: true });
    } finally {
      setBusy(false);
    }
  }

  async function remove(id: string) {
    setBusy(true);
    try {
      const response = await fetch(`/api/feedback/${encodeURIComponent(id)}`, { method: "DELETE" });
      if (!response.ok && response.status !== 404) throw new Error(String(response.status));
      setMessage({ text: "Your feedback was deleted.", error: false });
      onChanged();
    } catch {
      setMessage({ text: "The feedback could not be deleted. Try again later.", error: true });
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="feedback-panel no-print" aria-labelledby="feedback-title">
      <div className="panel-heading">
        <h2 id="feedback-title">Tell us how it went</h2>
        <p>Four short answers. Feedback is kept without your plan, preferences or photos, and is deleted 30 days after the beta.</p>
      </div>
      <form className="feedback-form" onSubmit={(event) => void submit(event)}>
        <fieldset className="budget-mode choice-row">
          <legend>Would you wear at least one of the looks?</legend>
          {(["yes", "no"] as const).map((answer) => (
            <label className="radio-choice" key={answer}>
              <input checked={wouldWear === answer} name="would-wear" onChange={() => setWouldWear(answer)} required type="radio" />
              <span>{answer === "yes" ? "Yes" : "No"}</span>
            </label>
          ))}
        </fieldset>
        <fieldset className="budget-mode choice-row">
          <legend>How useful was FullAura overall? (1 not at all, 5 very)</legend>
          {SCALE.map((score) => (
            <label className="radio-choice" key={score}>
              <input checked={usefulness === String(score)} name="usefulness" onChange={() => setUsefulness(String(score))} required type="radio" />
              <span>{score}</span>
            </label>
          ))}
        </fieldset>
        <fieldset className="budget-mode choice-row">
          <legend>How good was the try-on? (1 poor, 5 very good)</legend>
          {SCALE.map((score) => (
            <label className="radio-choice" key={score}>
              <input checked={tryOn === String(score)} name="try-on" onChange={() => setTryOn(String(score))} required type="radio" />
              <span>{score}</span>
            </label>
          ))}
          <label className="radio-choice">
            <input checked={tryOn === "cannot_assess"} name="try-on" onChange={() => setTryOn("cannot_assess")} required type="radio" />
            <span>I can't judge it</span>
          </label>
        </fieldset>
        <div className="form-field">
          <label htmlFor="catalog-problem">A problem with the products, if any</label>
          <input
            id="catalog-problem"
            maxLength={300}
            onChange={(event) => setCatalogProblem(event.target.value)}
            placeholder="For example, no closed shoes in my size"
            type="text"
            value={catalogProblem}
          />
        </div>
        <div className="form-field">
          <label htmlFor="feedback-comment">Anything else (optional)</label>
          <textarea id="feedback-comment" maxLength={500} onChange={(event) => setComment(event.target.value)} rows={2} value={comment} />
          <p className="field-help">Please do not include names, addresses or other personal details.</p>
        </div>
        <button className="secondary-button" disabled={busy || !wouldWear || !usefulness || !tryOn} type="submit">
          Send feedback
        </button>
      </form>
      {message && (
        <p className={`preview-status${message.error ? " is-error" : ""}`} role={message.error ? "alert" : "status"}>
          {message.text}
        </p>
      )}
      {feedbackIds.length > 0 && (
        <div className="feedback-sent">
          <p className="field-help">You have sent {feedbackIds.length === 1 ? "one answer" : `${feedbackIds.length} answers`}.</p>
          {feedbackIds.map((id, index) => (
            <button className="text-button" disabled={busy} key={id} onClick={() => void remove(id)} type="button">
              Delete answer {index + 1}
            </button>
          ))}
        </div>
      )}
    </section>
  );
}
