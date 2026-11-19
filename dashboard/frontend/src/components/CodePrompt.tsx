import { useEffect, useState } from "react";
import { KeyRound } from "lucide-react";
import { api } from "../api";
import { useApp, useToast } from "../lib/app";
import { Button, Modal } from "./ui";

/** Pops up whenever Instagram asks for a 2FA / security code during sign-in. */
export function CodePrompt() {
  const { status, refreshStatus } = useApp();
  const toast = useToast();
  const pending = status?.instagram.pending_code ?? null;
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [hidden, setHidden] = useState<string | null>(null);

  useEffect(() => setCode(""), [pending?.since]);
  if (!pending || hidden === pending.since) return null;

  const send = async () => {
    setBusy(true);
    try {
      await api.post("/account/code", { code });
      toast.ok("Code sent to Instagram...");
      setHidden(pending.since);
      await refreshStatus();
    } catch (e) {
      toast.error(e);
    } finally {
      setBusy(false);
    }
  };
  const cancel = async () => {
    await api.post("/account/code/cancel").catch(() => undefined);
    setHidden(pending.since);
    refreshStatus();
  };

  return (
    <Modal
      title={pending.kind === "two_factor" ? "Two-step verification" : "Security code"}
      desc={pending.message}
      icon={<div className="empty-icon" style={{ width: 44, height: 44, borderRadius: 13, display: "grid", placeItems: "center", background: "var(--accent-soft)", color: "var(--accent-text)" }}><KeyRound size={22} /></div>}
      onClose={cancel}
      footer={
        <>
          <Button onClick={cancel}>Cancel sign-in</Button>
          <Button variant="primary" onClick={send} loading={busy} disabled={code.replace(/\D/g, "").length < 4}>
            Continue
          </Button>
        </>
      }
    >
      <input
        className="input code-input"
        inputMode="numeric"
        autoComplete="one-time-code"
        autoFocus
        maxLength={8}
        value={code}
        placeholder="••••••"
        onChange={(e) => setCode(e.target.value.replace(/[^\d]/g, ""))}
        onKeyDown={(e) => e.key === "Enter" && code.length >= 4 && send()}
      />
      <p className="hint" style={{ marginTop: 10 }}>
        The code is only sent to Instagram to finish signing in. If nothing arrives, cancel and press Reconnect.
      </p>
    </Modal>
  );
}
