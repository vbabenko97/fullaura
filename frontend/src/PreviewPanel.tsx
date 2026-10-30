import { useEffect, useRef, useState } from "react";
import { isRecord } from "./api";
import { labelSlot } from "./format";
import { catalogItem } from "./plan";
import { SLOTS } from "./types";
import type { Catalog, OwnedItem, Plan, RequestState } from "./types";

interface PreviewOption {
  id: string;
  label: string;
  prompt: string;
  sourceKey: string;
}

interface PreviewResult {
  url: string;
  optionId: string;
  sourceKey: string;
  portrait: File;
  fullBody: File;
}

export function previewOptionsForPlan(
  plan: Plan,
  catalog: Catalog,
  ownedItems: OwnedItem[],
): PreviewOption[] {
  return plan.looks.map((look) => {
    const describedItems = SLOTS.flatMap((slot) => {
      const item = look.items[slot];
      if (!item) return [];
      if (item.kind === "owned") {
        const owned = ownedItems.find((candidate) => candidate.id === item.id);
        return owned
          ? [`${labelSlot(slot).toLowerCase()}: ${owned.color} ${owned.description}`]
          : [];
      }
      const entry = catalogItem(catalog, item.id);
      if (!entry) return [];
      const details = [entry.product.brand, entry.product.title, entry.variant.color]
        .filter((value): value is string => value !== null)
        .join(" ");
      return [`${labelSlot(slot).toLowerCase()}: ${details}`];
    });
    return {
      id: look.id,
      label: look.id.replaceAll("_", " "),
      sourceKey: JSON.stringify({
        items: SLOTS.map((slot) => [slot, look.items[slot] ?? null]),
        describedItems,
      }),
      prompt:
        `Create an approximate style preview of the person in the supplied portrait and full-body photos. ` +
        `Dress them in this outfit, using the text descriptions as references: ${describedItems.join("; ")}. ` +
        "Keep their identity, face, hair, skin tone and body proportions. Plain light background. " +
        "Do not infer sensitive traits. The result is an approximate preview, not proof of fit or product availability.",
    };
  });
}

interface PreviewPanelProps {
  enabled: boolean;
  detail: string | null;
  loadingStatus: boolean;
  options: PreviewOption[];
}

export function PreviewPanel({ enabled, detail, loadingStatus, options }: PreviewPanelProps) {
  const [portrait, setPortrait] = useState<File | null>(null);
  const [fullBody, setFullBody] = useState<File | null>(null);
  const [lookId, setLookId] = useState(options[0]?.id ?? "");
  const [requestState, setRequestState] = useState<RequestState>("idle");
  const [message, setMessage] = useState<string | null>(null);
  const [previewResult, setPreviewResult] = useState<PreviewResult | null>(null);
  const requestController = useRef<AbortController | null>(null);
  const requestVersion = useRef(0);

  useEffect(() => () => {
    requestVersion.current += 1;
    requestController.current?.abort();
  }, []);
  useEffect(() => {
    return () => {
      if (previewResult) URL.revokeObjectURL(previewResult.url);
    };
  }, [previewResult]);

  const selectedLook = options.find((option) => option.id === lookId);
  const previewUrl =
    previewResult &&
    selectedLook &&
    previewResult.optionId === selectedLook.id &&
    previewResult.sourceKey === selectedLook.sourceKey &&
    previewResult.portrait === portrait &&
    previewResult.fullBody === fullBody
      ? previewResult.url
      : null;

  function invalidatePreview() {
    requestVersion.current += 1;
    requestController.current?.abort();
    requestController.current = null;
    setPreviewResult(null);
    setRequestState("idle");
    setMessage(null);
  }

  function setPhoto(setter: (photo: File | null) => void, file: File | undefined) {
    invalidatePreview();
    setter(file ?? null);
  }

  async function generatePreview() {
    if (!enabled || !selectedLook || !portrait || !fullBody) return;
    if ([portrait, fullBody].some((file) => file.type !== "image/jpeg" || file.size <= 0)) {
      setRequestState("error");
      setMessage("Choose two non-empty JPEG photos.");
      return;
    }
    if ([portrait, fullBody].some((file) => file.size > 10 * 1024 * 1024)) {
      setRequestState("error");
      setMessage("Each photo must be no larger than 10 MiB.");
      return;
    }

    requestVersion.current += 1;
    const requestId = requestVersion.current;
    setPreviewResult(null);
    const controller = new AbortController();
    requestController.current = controller;
    setRequestState("loading");
    setMessage(`Creating an approximate preview for ${selectedLook.label}.`);
    try {
      const form = new FormData();
      form.append("portrait", portrait);
      form.append("full_body", fullBody);
      form.append("prompt", selectedLook.prompt);
      const response = await fetch("/api/previews", {
        method: "POST",
        body: form,
        signal: controller.signal,
      });
      if (requestId !== requestVersion.current || controller.signal.aborted) return;
      if (!response.ok) {
        let responseMessage = `The preview request failed (${response.status}).`;
        try {
          const payload: unknown = await response.json();
          if (isRecord(payload) && typeof payload.detail === "string") {
            responseMessage = payload.detail;
          }
        } catch {
          // Keep the status-based message when the server did not return JSON.
        }
        throw new Error(responseMessage);
      }
      const image = await response.blob();
      if (requestId !== requestVersion.current || controller.signal.aborted) return;
      if (
        !["image/png", "image/jpeg", "image/webp"].includes(image.type) ||
        image.size === 0 ||
        image.size > 16 * 1024 * 1024
      ) {
        throw new Error("The preview response was not a supported image.");
      }
      setPreviewResult({
        url: URL.createObjectURL(image),
        optionId: selectedLook.id,
        sourceKey: selectedLook.sourceKey,
        portrait,
        fullBody,
      });
      setRequestState("success");
      setMessage("Approximate preview generated. It cannot establish fit or product availability.");
    } catch (error) {
      if (requestId !== requestVersion.current) return;
      setRequestState("error");
      setMessage(isRecord(error) && error.name === "AbortError"
        ? "Preview generation cancelled."
        : error instanceof Error
          ? error.message
          : "The preview request could not be completed.");
    } finally {
      if (requestId === requestVersion.current) requestController.current = null;
    }
  }

  function cancelPreview() {
    requestVersion.current += 1;
    requestController.current?.abort();
    requestController.current = null;
    setPreviewResult(null);
    setRequestState("error");
    setMessage("Preview generation cancelled.");
  }

  function selectLook(nextLookId: string) {
    invalidatePreview();
    setLookId(nextLookId);
  }

  return (
    <section className="preview-panel" aria-labelledby="preview-title">
      <div className="preview-panel-heading">
        <div>
          <h2 id="preview-title">Approximate photo preview</h2>
          <p>
            Product images are unavailable, so the model uses text descriptions. A preview cannot
            establish fit, exact product details or availability.
          </p>
        </div>
      </div>
      <div className="preview-form">
        <label htmlFor="preview-portrait">Portrait photo</label>
        <input
          accept="image/jpeg"
          disabled={!enabled || requestState === "loading"}
          id="preview-portrait"
          onChange={(event) => setPhoto(setPortrait, event.currentTarget.files?.[0])}
          type="file"
        />
        <label htmlFor="preview-full-body">Full-body photo</label>
        <input
          accept="image/jpeg"
          disabled={!enabled || requestState === "loading"}
          id="preview-full-body"
          onChange={(event) => setPhoto(setFullBody, event.currentTarget.files?.[0])}
          type="file"
        />
        <label htmlFor="preview-look">Look to preview</label>
        <select
          disabled={!enabled || requestState === "loading"}
          id="preview-look"
          onChange={(event) => selectLook(event.target.value)}
          value={lookId}
        >
          {options.map((option) => <option key={option.id} value={option.id}>{option.label}</option>)}
        </select>
        <div className="preview-actions">
          {requestState === "loading" ? (
            <button className="secondary-button" onClick={cancelPreview} type="button">
              Cancel preview
            </button>
          ) : (
            <button
              className="secondary-button"
              disabled={!enabled || !portrait || !fullBody || options.length === 0}
              onClick={() => void generatePreview()}
              type="button"
            >
              Generate approximate preview
            </button>
          )}
        </div>
      </div>
      <p className="preview-privacy">
        {enabled
          ? "Metadata is removed before photos are sent to Meta. FullAura keeps the photos and result in memory only for this request; Meta’s current terms govern provider retention."
          : loadingStatus
            ? "Checking preview availability…"
            : detail ?? "Photo previews are unavailable."}
      </p>
      {message && (
        <p className={`preview-status${requestState === "error" ? " is-error" : ""}`} role={requestState === "error" ? "alert" : "status"}>
          {message}
        </p>
      )}
      {previewUrl && <img className="preview-image" src={previewUrl} alt={`Approximate generated preview for ${lookId.replaceAll("_", " ")}`} />}
    </section>
  );
}
