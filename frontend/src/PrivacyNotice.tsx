const META_TERMS = "https://dev.meta.ai/legal/terms-of-service";

/** How the beta handles personal data, in the words a participant needs. */
export function PrivacyNotice() {
  return (
    <footer className="privacy-footer no-print">
      <details>
        <summary>How FullAura handles your data</summary>
        <ul className="gate-facts">
          <li>
            Planning sends your style goal, colors, occasion, season, budget, sizes, shoe and material choices and
            owned-item descriptions to the Muse Spark model on Baseten. FullAura does not store them; the server keeps
            your latest plan in memory for up to a day.
          </li>
          <li>
            A try-on sends your two photos, redrawn without metadata, and a description of the look to Meta's Muse
            Image model in Standard mode. FullAura keeps photos and results only in memory for that request. Meta does
            not train on Standard-mode content but may keep it for safety and abuse review under its{" "}
            <a href={META_TERMS} rel="noreferrer" target="_blank">terms</a>.
          </li>
          <li>
            FullAura records for your invite only your age confirmation, photo consent, request counts and costs, and
            any feedback you send. These records are deleted 30 days after their last change; you can delete feedback
            yourself at any time.
          </li>
          <li>Saved plans stay in this browser until you delete them. FullAura never infers traits from photos.</li>
          <li>To have your invite's records deleted earlier, ask the person who sent you the invite.</li>
        </ul>
      </details>
    </footer>
  );
}
