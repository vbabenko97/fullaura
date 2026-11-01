import { useEffect, useRef, useState } from "react";
import { ApiResponseError, errorMessage, newOperationId, readJson } from "./api";
import { lookLabel } from "./format";
import { ACCEPTED_PHOTO_TYPES, normalizePhoto, PhotoError } from "./photos";
import type { Session } from "./types";

const META_TERMS = "https://dev.meta.ai/legal/terms-of-service";
const CLEARED = "Any try-on in progress was stopped, and your photos and result were removed from this page.";
// What a failed try-on did to the invite's quota: one the provider could not finish counts, and one
// that ran out of time counts once the server had sent it on, which the page cannot tell.
const COUNTED: Record<string, string> = {
  provider: " This try-on still counts against this invite.",
  timeout: " This try-on may still count against this invite.",
};

interface TryOnResult {
  url: string;
  token: string;
  lookId: string | null;
}

/** What a try-on draws: one look of the current plan, or the current Design my look concept. */
type TryOnTarget =
  | {
      kind: "plan";
      token: string;
      lookIds: string[];
      lookId: string;
      onSelectLook: (lookId: string) => void;
      onRestore: () => Promise<string | null>;
      /** A plan request is running, which may replace this plan. */
      planning: boolean;
    }
  | { kind: "concept"; token: string };

interface TryOnPanelProps {
  session: Session;
  target: TryOnTarget;
  /** Records the consent on the server; why that failed, or null once it is saved. */
  onConsent: (consent: boolean) => Promise<string | null>;
  onFinished: () => void;
}

type Photo = { blob: Blob; name: string } | null;
type PhotoInput = "portrait" | "full_body";

export function TryOnPanel({ session, target, onConsent, onFinished }: TryOnPanelProps) {
  const [portrait, setPortrait] = useState<Photo>(null);
  const [fullBody, setFullBody] = useState<Photo>(null);
  const [photoError, setPhotoError] = useState<string | null>(null);
  const [consentBusy, setConsentBusy] = useState(false);
  const [state, setState] = useState<"idle" | "preparing" | "loading" | "error">("idle");
  const [message, setMessage] = useState<string | null>(null);
  const [result, setResult] = useState<TryOnResult | null>(null);
  // Remounts the file inputs, so a withdrawal also forgets the files they hold.
  const [inputsKey, setInputsKey] = useState(0);
  const controller = useRef<AbortController | null>(null);
  // Each withdrawal starts a new epoch; work begun in an earlier one may not change the panel.
  const epoch = useRef(0);
  // Photos of this epoch still being prepared; the panel stays "preparing" until none is left.
  const preparing = useRef(0);
  // How many times each input was chosen or cleared, so a superseded choice is dropped.
  const choices = useRef<Record<PhotoInput, number>>({ portrait: 0, full_body: 0 });
  // Set when a try-on finishes, so only a new result moves focus, not coming back to a look.
  const revealResult = useRef(false);
  // The button that starts and cancels a try-on: one element, whichever it says.
  const actionButton = useRef<HTMLButtonElement>(null);
  // The consent button shown now, "I agree" or "Withdraw consent". Each replaces the other once the
  // server answers, so focus comes back to whichever is shown if the person was on the first one.
  const consentButton = useRef<HTMLButtonElement>(null);
  const refocusConsent = useRef(false);
  const consentWasBusy = useRef(false);

  useEffect(() => () => controller.current?.abort(), []);
  useEffect(() => {
    // Only the end of a consent request may use the flag: an effect still pending from the first
    // render runs when the button is pressed, after the flag is set, and would lose it.
    const finished = consentWasBusy.current && !consentBusy;
    consentWasBusy.current = consentBusy;
    if (!finished || !refocusConsent.current) return;
    refocusConsent.current = false;
    if (document.activeElement === document.body || document.activeElement === null) consentButton.current?.focus();
  }, [consentBusy]);
  useEffect(() => {
    return () => {
      if (result) URL.revokeObjectURL(result.url);
    };
  }, [result]);

  const lookId = target.kind === "plan" ? target.lookId : null;
  const label = lookId ? lookLabel(lookId) : "the concept";
  // A try-on belongs to the plan version and look, or the concept, it was made for; a changed
  // plan or a new concept hides it.
  const shown = result && result.token === target.token && result.lookId === lookId ? result : null;
  const remaining = Math.max(0, session.preview_requests_limit - session.preview_requests_used);

  async function choosePhoto(input: PhotoInput, file: File | undefined) {
    const started = epoch.current;
    // Only the latest choice for an input may fill it: an earlier, slower photo must not replace a
    // newer one, or a cleared input, and then be the one sent.
    const choice = ++choices.current[input];
    const setter = input === "portrait" ? setPortrait : setFullBody;
    setPhotoError(null);
    setResult(null);
    if (!file) {
      setter(null);
      return;
    }
    preparing.current += 1;
    setState("preparing");
    let photo: Photo = null;
    let error: unknown = null;
    try {
      photo = { blob: await normalizePhoto(file), name: file.name };
    } catch (caught) {
      error = caught;
    }
    if (epoch.current !== started) return;
    preparing.current -= 1;
    const latest = choices.current[input] === choice;
    if (latest) setter(photo);
    // The other photo may still be preparing; only the last one to finish allows a try-on.
    if (preparing.current === 0) setState((value) => (value === "preparing" ? "idle" : value));
    if (latest && error) setPhotoError(error instanceof PhotoError ? error.message : "This photo could not be prepared.");
  }

  /** Stop the try-on at once and forget its photos and result, before the server hears of it. */
  function forgetPhotos() {
    epoch.current += 1;
    preparing.current = 0;
    controller.current?.abort();
    controller.current = null;
    setPortrait(null);
    setFullBody(null);
    setResult(null); // the result effect revokes its object URL
    setPhotoError(null);
    setInputsKey((key) => key + 1);
    setState("idle");
    setMessage(CLEARED);
  }

  async function changeConsent(consent: boolean) {
    refocusConsent.current = document.activeElement === consentButton.current;
    if (!consent) forgetPhotos();
    else setMessage(null);
    setConsentBusy(true);
    try {
      const failure = await onConsent(consent);
      if (consent) {
        if (failure) setMessage(failure);
        return;
      }
      setMessage(
        failure === null
          ? `Consent withdrawn. ${CLEARED} Photos already sent to Meta for a try-on cannot be called back.`
          : `${CLEARED} The withdrawal could not be saved, so select "Withdraw consent" again.`,
      );
    } finally {
      setConsentBusy(false);
    }
  }

  async function send(token: string, operationId: string, signal: AbortSignal): Promise<Blob> {
    signal.throwIfAborted();
    if (!portrait || !fullBody) throw new Error("Choose both photos first.");
    const form = new FormData();
    form.append("portrait", portrait.blob, "portrait.jpg");
    form.append("full_body", fullBody.blob, "full-body.jpg");
    const subject: Record<string, string> =
      target.kind === "plan" ? { "X-Plan-Token": token, "X-Look-Id": target.lookId } : { "X-Concept-Token": token };
    const response = await fetch("/api/previews", {
      method: "POST",
      body: form,
      headers: { ...subject, "X-Operation-Id": operationId },
      signal,
    });
    if (!response.ok) await readJson(response);
    const image = await response.blob();
    signal.throwIfAborted();
    if (!["image/png", "image/jpeg", "image/webp"].includes(image.type) || image.size === 0) {
      throw new Error("The try-on response was not an image.");
    }
    return image;
  }

  async function generate() {
    if (!portrait || !fullBody || state === "loading") return;
    const started = epoch.current;
    const abort = new AbortController();
    controller.current = abort;
    setResult(null);
    setState("loading");
    setMessage(`Drawing ${label} on your photos. This can take up to two minutes.`);
    const operationId = newOperationId();
    try {
      let token = target.token;
      let image: Blob;
      try {
        image = await send(token, operationId, abort.signal);
      } catch (error) {
        // A replaced concept cannot be brought back; only a plan has a restore path.
        if (target.kind !== "plan" || !(error instanceof ApiResponseError && error.reason === "plan_expired")) {
          throw error;
        }
        const restored = await target.onRestore();
        if (!restored) throw error;
        token = restored;
        image = await send(token, operationId, abort.signal);
      }
      if (abort.signal.aborted) return;
      revealResult.current = true;
      setResult({ url: URL.createObjectURL(image), token, lookId });
      setState("idle");
      setMessage(null);
    } catch (error) {
      if (abort.signal.aborted) return;
      setState("error");
      setMessage(
        error instanceof ApiResponseError
          ? `${errorMessage(error)}${COUNTED[error.reason ?? ""] ?? ""}`
          : error instanceof Error
            ? error.message
            : "The try-on failed.",
      );
    } finally {
      if (controller.current === abort) controller.current = null;
      // After a withdrawal, the consent request returns the session; a refresh racing it could
      // show the withdrawn consent again.
      if (epoch.current === started) onFinished();
    }
  }

  function cancel() {
    controller.current?.abort();
    controller.current = null;
    setState("error");
    setMessage("Try-on cancelled. A cancelled try-on still counts against this invite.");
  }

  const disabled = !session.photo_consent || !session.previews_open || consentBusy || state === "loading";
  const planning = target.kind === "plan" && target.planning;

  return (
    // A printout leaves the device, so it holds the looks and the list, never a try-on.
    <section className="tryon-panel no-print" aria-labelledby="tryon-title">
      <div className="panel-heading">
        <h2 aria-describedby="tryon-description" id="tryon-title" tabIndex={-1}>Try a look on</h2>
        <p id="tryon-description">
          An experimental drawing of you in {label}, made from your photos and the look's colors and garment kinds.
        </p>
      </div>

      <div className="tryon-notice">
        <p>
          Your photos are redrawn on this device without metadata, then sent through FullAura to Meta's
          Muse Image model in Standard mode with a text description of the look. If the look includes an item you
          own, that description includes the words you entered for it that describe clothes. FullAura keeps your photos only in
          memory for that request and stores neither photos nor results. Standard mode keeps your content
          out of Meta's model training, but Meta may still retain it under its <a href={META_TERMS} rel="noreferrer" target="_blank">terms</a>.
          Use photos of yourself only. Withdrawing consent stops a try-on in progress and removes your photos and
          result from this page.
        </p>
        {session.photo_consent ? (
          <p className="tryon-consent">
            You agreed to this processing.{" "}
            <button
              className="text-button"
              disabled={consentBusy}
              onClick={() => void changeConsent(false)}
              ref={consentButton}
              type="button"
            >
              Withdraw consent
            </button>
          </p>
        ) : (
          <div>
            <button
              className="secondary-button"
              disabled={consentBusy}
              onClick={() => void changeConsent(true)}
              ref={consentButton}
              type="button"
            >
              I agree to this processing of my photos
            </button>
          </div>
        )}
      </div>

      <div className="tryon-form">
        <div className="form-field">
          <label htmlFor="tryon-portrait">Portrait photo</label>
          <input
            accept={ACCEPTED_PHOTO_TYPES.join(",")}
            disabled={disabled}
            id="tryon-portrait"
            key={`portrait-${inputsKey}`}
            onChange={(event) => void choosePhoto("portrait", event.currentTarget.files?.[0])}
            type="file"
          />
        </div>
        <div className="form-field">
          <label htmlFor="tryon-full-body">Full-body photo</label>
          <input
            accept={ACCEPTED_PHOTO_TYPES.join(",")}
            disabled={disabled}
            id="tryon-full-body"
            key={`full-body-${inputsKey}`}
            onChange={(event) => void choosePhoto("full_body", event.currentTarget.files?.[0])}
            type="file"
          />
        </div>
        {target.kind === "plan" && (
          <fieldset className="budget-mode choice-row">
            <legend>Look to try on</legend>
            {target.lookIds.map((id) => (
              <label className="radio-choice" key={id}>
                <input
                  checked={lookId === id}
                  disabled={state === "loading"}
                  name="tryon-look"
                  onChange={() => target.onSelectLook(id)}
                  type="radio"
                />
                <span>{lookLabel(id)}</span>
              </label>
            ))}
          </fieldset>
        )}
        {photoError && <p className="notice notice-error" role="alert">{photoError}</p>}
        <div className="preview-actions">
          {state === "loading" ? (
            <button className="secondary-button" onClick={cancel} ref={actionButton} type="button">Cancel try-on</button>
          ) : (
            <button
              className="secondary-button"
              disabled={disabled || planning || !portrait || !fullBody || state === "preparing"}
              onClick={() => void generate()}
              ref={actionButton}
              type="button"
            >
              {state === "preparing" ? "Preparing photo…" : `Try on ${label}`}
            </button>
          )}
          <p className="field-help">
            {planning && "Try-ons wait until the plan request finishes. "}
            {session.previews_open
              ? `${remaining} of ${session.preview_requests_limit} try-ons left for this invite.`
              : "Try-ons are not available right now. Your plan and shopping list still work."}
          </p>
        </div>
      </div>

      {message && (
        <p className={`preview-status${state === "error" ? " is-error" : ""}`} role={state === "error" ? "alert" : "status"}>
          {message}
        </p>
      )}
      {shown && (
        <div
          className="tryon-outcome"
          ref={(node) => {
            if (!node || !revealResult.current) return;
            revealResult.current = false;
            // A person who moved anywhere else while the drawing ran keeps their place.
            const active = document.activeElement;
            if (active !== document.body && active !== actionButton.current) return;
            node.scrollIntoView?.({ block: "start" });
            node.focus({ preventScroll: true });
          }}
          tabIndex={-1}
        >
          <p className="preview-status" role="status">Your try-on of {label} is ready.</p>
          <figure className="tryon-result">
            <img alt={`Experimental try-on of ${label}`} src={shown.url} />
            <figcaption>
              Experimental visualization. Colors, shapes and combinations are approximate: it does not show the exact
              products, their fit or your size. It is not saved; download it from your browser if you want to keep it.
            </figcaption>
          </figure>
        </div>
      )}
    </section>
  );
}
