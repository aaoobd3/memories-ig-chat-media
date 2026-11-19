import { useState } from "react";
import { Eye, EyeOff, Lock, ShieldCheck, Sparkles } from "lucide-react";
import { api } from "../api";
import { errorText } from "../lib/app";
import { Button } from "../components/ui";

export default function AuthPage({ setup, onDone }: { setup: boolean; onDone: () => void }) {
  const [code, setCode] = useState("");
  const [repeat, setRepeat] = useState("");
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    if (setup && code !== repeat) {
      setError("The two passcodes don't match.");
      return;
    }
    setBusy(true);
    try {
      await api.post(setup ? "/auth/setup" : "/auth/login", { passcode: code });
      onDone();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="auth-wrap">
      <form className="card auth-card" onSubmit={submit}>
        <div className="brand-mark" style={{ width: 52, height: 52, borderRadius: 16 }}>
          <Sparkles size={26} />
        </div>
        <div>
          <h1>{setup ? "Welcome!" : "Welcome back"}</h1>
          <p className="muted" style={{ marginTop: 6 }}>
            {setup
              ? "Choose a passcode to protect this dashboard. You'll type it when you open the app on a new device."
              : "Type your passcode to open your Instagram media dashboard."}
          </p>
        </div>
        <div className="field">
          <label htmlFor="pc">{setup ? "New passcode" : "Passcode"}</label>
          <div className="input-wrap">
            <Lock size={17} />
            <input id="pc" className="input" type={show ? "text" : "password"} autoFocus value={code} onChange={(e) => setCode(e.target.value)} autoComplete={setup ? "new-password" : "current-password"} placeholder="At least 4 characters" />
            <Button type="button" variant="ghost" size="sm" iconOnly className="input-action" icon={show ? EyeOff : Eye} onClick={() => setShow(!show)} aria-label={show ? "Hide passcode" : "Show passcode"} />
          </div>
        </div>
        {setup && (
          <div className="field">
            <label htmlFor="pc2">Type it again</label>
            <input id="pc2" className="input" type={show ? "text" : "password"} value={repeat} onChange={(e) => setRepeat(e.target.value)} autoComplete="new-password" />
          </div>
        )}
        {error && <div className="callout callout-bad">{error}</div>}
        <Button variant="brand" size="lg" type="submit" loading={busy} disabled={code.length < 4}>
          {setup ? "Create passcode" : "Open dashboard"}
        </Button>
        <p className="hint row" style={{ alignItems: "flex-start" }}>
          <ShieldCheck size={16} style={{ flex: "none", marginTop: 2 }} />
          Your Instagram login stays on this computer, encrypted. Nothing is sent anywhere except Instagram itself.
        </p>
      </form>
    </div>
  );
}
