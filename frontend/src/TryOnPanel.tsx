import { useEffect, useRef, useState } from "react";
import { ApiResponseError, errorMessage, newOperationId, readJson } from "./api";
import { ACCEPTED_PHOTO_TYPES, normalizePhoto, PhotoError } from "./photos";
import type { Session } from "./types";

const META_TERMS = "https://dev.meta.ai/legal/terms-of-service";
const CLEARED = "Any try-on in progress was stopped, and your photos and result were removed from this page.";

interface TryOnResult {
  url: string;
  lookId: string;
  planToken: string;
}

interface TryOnPanelProps {
  session: Session;
  planToken: string;
  lookIds: string[];
  lookId: string;
  onSelectLook: (lookId: string) => void;
  /** Records the consent on the server; false when that failed. */
  onConsent: (consent: boolean) => Promise<boolean>;
  onRestore: () => Promise<string | null>;
  onFinished: () => void;
}

type Photo = { blob: Blob; name: string } | null;

function lookLabel(lookId: string): string {
  return lookId.replaceAll("_", " ");
}

export function TryOnPanel({
  session,
  planToken,
  lookIds,
  lookId,
  onSelectLook,
  onConsent,
  onRestore,
  onFinished,
}: TryOnPanelProps) {
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

  useEffect(() => () => controller.current?.abort(), []);
  useEffect(() => {
    return () => {
      if (result) URL.revokeObjectURL(result.url);
    };
  }, [result]);

  // A try-on belongs to the plan version and look it was made for; a changed plan hides it.
  const shown = result && result.planToken === planToken && result.lookId === lookId ? result : null;
  const remaining = Math.max(0, session.preview_requests_limit - session.preview_requests_used);

  async function choosePhoto(setter: (photo: Photo) => void, file: File | undefined) {
    const started = epoch.current;
    setPhotoError(null);
    setResult(null);
    if (!file) {
      setter(null);
      return;
    }
    setState("preparing");
    let photo: Photo = null;
    let error: unknown = null;
    try {
      photo = { blob: await normalizePhoto(file), name: file.name };
    } catch (caught) {
      error = caught;
    }
    if (epoch.current !== started) return;
    setter(photo);
    setState("idle");
    if (error) setPhotoError(error instanceof PhotoError ? error.message : "This photo could not be prepared.");
  }

  /** Stop the try-on at once and forget its photos and result, before the server hears of it. */
  function forgetPhotos() {
    epoch.current += 1;
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
    if (!consent) forgetPhotos();
    else setMessage(null);
    setConsentBusy(true);
    try {
      const saved = await onConsent(consent);
      if (consent) return;
      setMessage(
        saved
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
    const response = await fetch("/api/previews", {
      method: "POST",
      body: form,
      headers: { "X-Plan-Token": token, "X-Look-Id": lookId, "X-Operation-Id": operationId },
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
    setMessage(`Drawing ${lookLabel(lookId)} on your photos. This can take up to two minutes.`);
    const operationId = newOperationId();
    try {
      let token = planToken;
      let image: Blob;
      try {
        image = await send(token, operationId, abort.signal);
      } catch (error) {
        if (!(error instanceof ApiResponseError && error.reason === "plan_expired")) throw error;
        const restored = await onRestore();
        if (!restored) throw error;
        token = restored;
        image = await send(token, operationId, abort.signal);
      }
      if (abort.signal.aborted) return;
      setResult({ url: URL.createObjectURL(image), lookId, planToken: token });
      setState("idle");
      setMessage(null);
    } catch (error) {
      if (abort.signal.aborted) return;
      setState("error");
      setMessage(error instanceof ApiResponseError ? errorMessage(error) : error instanceof Error ? error.message : "The try-on failed.");
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

  return (
    <section className="tryon-panel" aria-labelledby="tryon-title">
      <div className="panel-heading">
        <h2 id="tryon-title">Try a look on</h2>
        <p>An experimental drawing of you in the chosen look, made from your photos and the look's colors and garment kinds.</p>
      </div>

      <div className="tryon-notice">
        <p>
          Your photos are redrawn on this device without metadata, then sent through FullAura to Meta's
          Muse Image model in Standard mode with a text description of the look. If the look includes an item you
          own, that description includes the color and words you entered for it. FullAura keeps your photos only in
          memory for that request and stores neither photos nor results. Standard mode keeps your content
          out of Meta's model training, but Meta may still retain it under its <a href={META_TERMS} rel="noreferrer" target="_blank">terms</a>.
          Use photos of yourself only. Withdrawing consent stops a try-on in progress and removes your photos and
          result from this page.
        </p>
        {session.photo_consent ? (
          <p className="tryon-consent">
            You agreed to this processing.{" "}
            <button className="text-button" disabled={consentBusy} onClick={() => void changeConsent(false)} type="button">
              Withdraw consent
            </button>
          </p>
        ) : (
          <div>
            <button
              className="secondary-button"
              disabled={consentBusy}
              onClick={() => void changeConsent(true)}
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
            onChange={(event) => void choosePhoto(setPortrait, event.currentTarget.files?.[0])}
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
            onChange={(event) => void choosePhoto(setFullBody, event.currentTarget.files?.[0])}
            type="file"
          />
        </div>
        <fieldset className="budget-mode choice-row">
          <legend>Look to try on</legend>
          {lookIds.map((id) => (
            <label className="radio-choice" key={id}>
              <input
                checked={lookId === id}
                disabled={state === "loading"}
                name="tryon-look"
                onChange={() => onSelectLook(id)}
                type="radio"
              />
              <span>{lookLabel(id)}</span>
            </label>
          ))}
        </fieldset>
        {photoError && <p className="notice notice-error" role="alert">{photoError}</p>}
        <div className="preview-actions">
          {state === "loading" ? (
            <button className="secondary-button" onClick={cancel} type="button">Cancel try-on</button>
          ) : (
            <button
              className="secondary-button"
              disabled={disabled || !portrait || !fullBody || state === "preparing"}
              onClick={() => void generate()}
              type="button"
            >
              {state === "preparing" ? "Preparing photo…" : `Try on ${lookLabel(lookId)}`}
            </button>
          )}
          <p className="field-help">
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
        <figure className="tryon-result">
          <img alt={`Experimental try-on of ${lookLabel(shown.lookId)}`} src={shown.url} />
          <figcaption>
            Experimental visualization. Colors, shapes and combinations are approximate: it does not show the exact
            products, their fit or your size. It is not saved; download it from your browser if you want to keep it.
          </figcaption>
        </figure>
      )}
    </section>
  );
}
