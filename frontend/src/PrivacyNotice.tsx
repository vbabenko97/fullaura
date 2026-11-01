import { recordsDeletedText } from "./format";

const META_TERMS = "https://dev.meta.ai/legal/terms-of-service";
// Checked 2026-10-31: "For synchronous Model API requests, Baseten doesn't store inputs or outputs"
const BASETEN_RETENTION = "https://docs.baseten.co/inference/model-apis/overview";

interface PrivacyNoticeProps {
  recordsPurgeAt: string | null;
  /** Design my look is open for this invite, so the notice says what it sends. */
  designOpen: boolean;
}

/** How the beta handles personal data, in the words a participant needs. */
export function PrivacyNotice({ recordsPurgeAt, designOpen }: PrivacyNoticeProps) {
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
            requests outside Austria. That is Baseten's published policy, not a check of FullAura's account.{" "}
            {designOpen &&
              "Design my look first sends your style goal, colors, occasion, season, budget, clothing line, the kinds of bottoms and dresses you wear, and your shoe and material choices, without your sizes or owned items; it then sends its concept with the catalog products in your sizes, so that the model can pick the closest ones. "}
            FullAura does not store your preferences; the server keeps your latest plan
            {designOpen && " and your latest concept"} in memory until a day after you last use FullAura.
          </li>
          <li>
            A try-on sends your two photos, redrawn without metadata, and a description of the look
            {designOpen && ", or of your concept in Design my look,"} to Meta's Muse Image model in Standard mode. If
            the look includes an item you own, the
            description includes the words you entered for it that describe clothes. FullAura keeps photos and results only in
            memory for that request. Meta does not train on Standard-mode content but may keep it for safety and abuse
            review under its <a href={META_TERMS} rel="noreferrer" target="_blank">terms</a>. FullAura turns off the
            image model's own web and image search, so it works only from your photos and that text.
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
