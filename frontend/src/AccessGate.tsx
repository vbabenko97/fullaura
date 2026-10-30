import { useState } from "react";
import type { FormEvent } from "react";
import type { Session } from "./types";

interface AccessGateProps {
  session: Session;
  busy: boolean;
  message: string | null;
  onInvite: (code: string) => void;
  onConfirmAge: () => void;
}

/** What a person sees before planning: a closed beta, an invite to open, or the age check. */
export function AccessGate({ session, busy, message, onInvite, onConfirmAge }: AccessGateProps) {
  const [code, setCode] = useState("");
  const [over18, setOver18] = useState(false);

  function submitInvite(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (code.trim()) onInvite(code.trim());
  }

  function submitAge(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (over18) onConfirmAge();
  }

  if (!session.beta) {
    return (
      <section className="gate-panel" aria-labelledby="gate-title">
        <h1 id="gate-title">The FullAura beta is closed</h1>
        <p>Planning and try-ons are available only while the closed beta runs.</p>
      </section>
    );
  }

  if (!session.invited) {
    return (
      <section className="gate-panel" aria-labelledby="gate-title">
        <h1 id="gate-title">Open your invite</h1>
        <p>FullAura is in a closed beta. Open the link you were sent, or paste its code here.</p>
        <form className="gate-form" onSubmit={submitInvite}>
          <label htmlFor="invite-code">Invite code</label>
          <input
            autoComplete="off"
            id="invite-code"
            maxLength={64}
            onChange={(event) => setCode(event.target.value)}
            required
            spellCheck={false}
            type="text"
            value={code}
          />
          <button className="primary-button" disabled={busy || !code.trim()} type="submit">
            {busy ? "Checking…" : "Open invite"}
          </button>
        </form>
        {message && <p className="notice notice-error" role="alert">{message}</p>}
      </section>
    );
  }

  return (
    <section className="gate-panel" aria-labelledby="gate-title">
      <h1 id="gate-title">Before you start</h1>
      <p>
        FullAura proposes three outfits from real products that ship to Austria, checks your budget,
        sizes and materials in code, and can draw an experimental try-on on your own photos.
      </p>
      <ul className="gate-facts">
        <li>Your preferences and owned-item descriptions go to the Muse Spark model on Baseten to plan looks.</li>
        <li>Photos are used only for a try-on you start, after a separate consent.</li>
        <li>Plans you save stay on this device. Feedback is stored without your plan or photos.</li>
      </ul>
      <form className="gate-form" onSubmit={submitAge}>
        <label className="check-choice" htmlFor="confirm-over-18">
          <input
            checked={over18}
            id="confirm-over-18"
            onChange={(event) => setOver18(event.target.checked)}
            type="checkbox"
          />
          <span>I confirm that I am 18 or older.</span>
        </label>
        <button className="primary-button" disabled={busy || !over18} type="submit">
          {busy ? "Saving…" : "Continue"}
        </button>
      </form>
      {message && <p className="notice notice-error" role="alert">{message}</p>}
    </section>
  );
}
