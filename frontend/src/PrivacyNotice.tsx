import { recordsDeletedText } from "./format";

const META_TERMS = "https://dev.meta.ai/legal/terms-of-service";
// Checked 2026-10-31: "For synchronous Model API requests, Baseten doesn't store inputs or outputs"
const BASETEN_RETENTION = "https://docs.baseten.co/inference/model-apis/overview";

/** How the beta handles personal data, in the words a participant needs. */
export function PrivacyNotice({ recordsPurgeAt }: { recordsPurgeAt: string | null }) {
  return (
    <footer className="privacy-footer no-print">
      <details>
        <summary>How FullAura handles your data</summary>
        <ul className="gate-facts">
          <li>
            Planning sends your style goal, colors, occasion, season, budget, sizes, shoe and material choices and
            owned-item descriptions to the Muse Spark model through Baseten's Model APIs. Baseten's{" "}
            <a href={BASETEN_RETENTION} rel="noreferrer" target="_blank">documentation</a> says it does not store the
            inputs or outputs of these requests; it keeps usage counts, such as tokens per request, and may process
            requests outside Austria. That is Baseten's published policy, not a check of FullAura's account. FullAura
            does not store your preferences; the server keeps your latest plan in memory for up to a day.
          </li>
          <li>
            A try-on sends your two photos, redrawn without metadata, and a description of the look to Meta's Muse
            Image model in Standard mode. If the look includes an item you own, the description includes the color and
            words you entered for it. FullAura keeps photos and results only in memory for that request. Meta does not
            train on Standard-mode content but may keep it for safety and abuse review under its{" "}
            <a href={META_TERMS} rel="noreferrer" target="_blank">terms</a>.
          </li>
          <li>
            FullAura records for your invite only your age confirmation, photo consent, request counts, the outcome and
            cost of each request, and any feedback you send. These records are deleted {recordsDeletedText(recordsPurgeAt)}, when the beta
            closes; you can delete feedback yourself at any time.
          </li>
          <li>Saved plans stay in this browser until you delete them. FullAura never infers traits from photos.</li>
          <li>To have your invite's records deleted earlier, ask the person who sent you the invite.</li>
        </ul>
      </details>
    </footer>
  );
}
