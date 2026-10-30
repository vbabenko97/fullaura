import { useEffect, useRef, useState } from "react";
import { ApiResponseError, errorMessage, newOperationId, readJson } from "./api";
import { ACCEPTED_PHOTO_TYPES, normalizePhoto, PhotoError } from "./photos";
import type { Session } from "./types";

const META_TERMS = "https://dev.meta.ai/legal/terms-of-service";

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
  onConsent: (consent: boolean) => Promise<void>;
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
  const controller = useRef<AbortController | null>(null);

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
    setPhotoError(null);
    setResult(null);
    if (!file) {
      setter(null);
      return;
    }
    setState("preparing");
    try {
      setter({ blob: await normalizePhoto(file), name: file.name });
      setState("idle");
    } catch (error) {
      setter(null);
      setState("idle");
      setPhotoError(error instanceof PhotoError ? error.message : "This photo could not be prepared.");
    }
  }

  async function changeConsent(consent: boolean) {
    setConsentBusy(true);
    try {
      await onConsent(consent);
    } finally {
      setConsentBusy(false);
    }
  }

  async function send(token: string, operationId: string, signal: AbortSignal): Promise<Blob> {
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
    if (!["image/png", "image/jpeg", "image/webp"].includes(image.type) || image.size === 0) {
      throw new Error("The try-on response was not an image.");
    }
    return image;
  }

  async function generate() {
    if (!portrait || !fullBody || state === "loading") return;
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
      onFinished();
    }
  }

  function cancel() {
    controller.current?.abort();
    controller.current = null;
    setState("error");
    setMessage("Try-on cancelled. A cancelled try-on still counts against this invite.");
  }

  const disabled = !session.photo_consent || !session.previews_open || state === "loading";

  return (
    <section className="tryon-panel" aria-labelledby="tryon-title">
      <div className="panel-heading">
        <h2 id="tryon-title">Try a look on</h2>
        <p>An experimental drawing of you in the chosen look, made from your photos and the look's colors and garment kinds.</p>
      </div>

      <div className="tryon-notice">
        <p>
          Your photos are redrawn on this device without metadata, then sent through FullAura to Meta's
          Muse Image model in Standard mode with a text description of the look. FullAura keeps them only in
          memory for that request and stores neither photos nor results. Standard mode keeps your content
          out of Meta's model training, but Meta may still retain it under its <a href={META_TERMS} rel="noreferrer" target="_blank">terms</a>.
          Use photos of yourself only.
        </p>
        {session.photo_consent ? (
          <p className="tryon-consent">
            You agreed to this processing.{" "}
            <button className="text-button" disabled={consentBusy} onClick={() => void changeConsent(false)} type="button">
              Withdraw consent
            </button>
          </p>
        ) : (
          <label className="check-choice">
            <input
              checked={false}
              disabled={consentBusy}
              onChange={(event) => void changeConsent(event.target.checked)}
              type="checkbox"
            />
            <span>I agree to this processing of my photos.</span>
          </label>
        )}
      </div>

      <div className="tryon-form">
        <div className="form-field">
          <label htmlFor="tryon-portrait">Portrait photo</label>
          <input
            accept={ACCEPTED_PHOTO_TYPES.join(",")}
            disabled={disabled}
            id="tryon-portrait"
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
            onChange={(event) => void choosePhoto(setFullBody, event.currentTarget.files?.[0])}
            type="file"
          />
        </div>
        <fieldset className="budget-mode">
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
