import { useRef, useState } from "react";
import type { FormEvent } from "react";
import { errorMessage, postJson } from "./api";
import { recordsDeletedText } from "./format";
import { TRY_ON_ISSUES } from "./types";
import type { TryOnIssue } from "./types";

interface FeedbackFormProps {
  feedbackIds: string[];
  recordsPurgeAt: string | null;
  onChanged: () => void;
}

const SCALE = [1, 2, 3, 4, 5] as const;
const ISSUE_LABELS: Record<TryOnIssue, string> = {
  likeness: "It did not look like me",
  body_proportions: "Body proportions looked off",
  clothes_or_colors: "Clothes or colors differ from the look",
  background: "The background looked wrong",
  other: "Something else",
  none_noticed: "I noticed no problems",
};

/** "No problems" and a problem exclude each other, so choosing one clears the other kind. */
function toggleIssue(current: TryOnIssue[], issue: TryOnIssue): TryOnIssue[] {
  if (current.includes(issue)) return current.filter((item) => item !== issue);
  if (issue === "none_noticed") return [issue];
  return [...current.filter((item) => item !== "none_noticed"), issue];
}

export function FeedbackForm({ feedbackIds, recordsPurgeAt, onChanged }: FeedbackFormProps) {
  const [wouldWear, setWouldWear] = useState<"" | "yes" | "no">("");
  const [usefulness, setUsefulness] = useState("");
  const [tryOn, setTryOn] = useState("");
  const [tryOnIssues, setTryOnIssues] = useState<TryOnIssue[]>([]);
  const [catalogProblem, setCatalogProblem] = useState("");
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ text: string; error: boolean } | null>(null);
  // Sending disables the button and deleting removes it, so focus moves to the message instead.
  const focusMessage = useRef(false);
  const scored = tryOn !== "" && tryOn !== "cannot_assess";

  function report(text: string, error: boolean) {
    focusMessage.current = true;
    setMessage({ text, error });
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!wouldWear || !usefulness || !tryOn) return;
    setBusy(true);
    try {
      await postJson("/api/feedback", {
        would_wear: wouldWear === "yes",
        usefulness: Number(usefulness),
        try_on: tryOn === "cannot_assess" ? tryOn : Number(tryOn),
        // null keeps "skipped" apart from "no problems noticed"
        try_on_issues: scored && tryOnIssues.length > 0 ? tryOnIssues : null,
        catalog_problem: catalogProblem.trim(),
        comment: comment.trim(),
      });
      report("Thank you. Your feedback is saved.", false);
      setWouldWear("");
      setUsefulness("");
      setTryOn("");
      setTryOnIssues([]);
      setCatalogProblem("");
      setComment("");
      onChanged();
    } catch (error) {
      report(errorMessage(error), true);
    } finally {
      setBusy(false);
    }
  }

  async function remove(id: string) {
    setBusy(true);
    try {
      const response = await fetch(`/api/feedback/${encodeURIComponent(id)}`, { method: "DELETE" });
      if (!response.ok && response.status !== 404) throw new Error(String(response.status));
      report("Your feedback was deleted.", false);
      onChanged();
    } catch {
      report("The feedback could not be deleted. Try again later.", true);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="feedback-panel no-print" aria-labelledby="feedback-title">
      <div className="panel-heading">
        <h2 id="feedback-title">Tell us how it went</h2>
        <p>
          A few short answers. Feedback is kept without your plan, preferences or photos, and is deleted{" "}
          {recordsDeletedText(recordsPurgeAt)}.
        </p>
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
            <input
              checked={tryOn === "cannot_assess"}
              name="try-on"
              onChange={() => {
                setTryOn("cannot_assess");
                setTryOnIssues([]);
              }}
              required
              type="radio"
            />
            <span>I can't judge it</span>
          </label>
          <p className="field-help">Choose "I can't judge it" if you did not try a look on.</p>
        </fieldset>
        <fieldset
          aria-describedby="try-on-issues-help"
          className="budget-mode feedback-issues"
          disabled={!scored}
        >
          <legend>What did you notice in the try-on? (optional)</legend>
          <p className="field-help" id="try-on-issues-help">
            {scored
              ? "Choose any that apply. This is about the drawing only; it does not show how the products would fit you."
              : "Asked once you give the try-on a score from 1 to 5."}
          </p>
          <div className="choice-grid">
            {TRY_ON_ISSUES.map((issue) => (
              <label className="check-choice" key={issue}>
                <input
                  checked={tryOnIssues.includes(issue)}
                  onChange={() => setTryOnIssues((current) => toggleIssue(current, issue))}
                  type="checkbox"
                  value={issue}
                />
                <span>{ISSUE_LABELS[issue]}</span>
              </label>
            ))}
          </div>
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
        <p
          className={`preview-status${message.error ? " is-error" : ""}`}
          ref={(node) => {
            if (node && focusMessage.current) {
              focusMessage.current = false;
              node.focus();
            }
          }}
          role={message.error ? "alert" : "status"}
          tabIndex={-1}
        >
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
