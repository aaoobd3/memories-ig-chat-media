import { useEffect, useState } from "react";
import { AlertTriangle, AtSign, CheckCircle2, Cookie, Eye, EyeOff, KeyRound, Lock, LogOut, PauseCircle, PlayCircle, PlugZap, RefreshCw, ShieldCheck, Sparkles, Wand2 } from "lucide-react";
import { api, type Account } from "../api";
import { ago, inTime } from "../lib/format";
import { useApp, useDialog, useJobStarter, useToast } from "../lib/app";
import { Button, Spinner, StatusDot } from "../components/ui";

type Method = Account["login_method"];

const METHODS: { value: Method; title: string; desc: string; icon: typeof Wand2 }[] = [
  { value: "auto", title: "Automatic", desc: "Uses the browser cookie if you added one, otherwise your password. Recommended.", icon: Wand2 },
  { value: "password", title: "Password", desc: "Signs in with your username and password, like the app does.", icon: KeyRound },
  { value: "sessionid", title: "Browser cookie", desc: "Re-uses your login from instagram.com in your browser. Works when the password login is blocked.", icon: Cookie },
];

export default function AccountPage() {
  const { status, refreshStatus, bump } = useApp();
  const toast = useToast();
  const dialog = useDialog();
  const start = useJobStarter();
  const [acc, setAcc] = useState<Account | null>(null);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [sessionid, setSessionid] = useState("");
  const [method, setMethod] = useState<Method>("auto");
  const [showPw, setShowPw] = useState(false);
  const [saving, setSaving] = useState(false);

  const load = async () => {
    try {
      const a = await api.get<Account>("/account");
      setAcc(a);
      setUsername(a.username);
      setMethod(a.login_method);
    } catch (e) {
      toast.error(e);
    }
  };
  useEffect(() => {
    load();
  }, [bump]); // eslint-disable-line react-hooks/exhaustive-deps

  const ig = status?.instagram ?? acc?.status;
  const connecting = ig?.state === "connecting" || status?.active_jobs.some((j) => j.kind === "connect");
  const sidValid = /^\d{3,}(%3A|:)/i.test(sessionid.replace(/^sessionid=/i, "").trim());
  const dirty = username !== (acc?.username ?? "") || !!password || !!sessionid || method !== acc?.login_method;

  const save = async (connect: boolean) => {
    if (sessionid && !sidValid) {
      toast.error("That cookie doesn't look right. It should start with numbers, like 1234567890%3A...");
      return;
    }
    setSaving(true);
    try {
      const a = await api.put<Account>("/account", { username, password: password || undefined, sessionid: sessionid || undefined, login_method: method, connect });
      setAcc(a);
      setPassword("");
      setSessionid("");
      toast.ok(connect ? "Saved. Connecting to Instagram..." : "Saved.");
      refreshStatus();
    } catch (e) {
      toast.error(e);
    } finally {
      setSaving(false);
    }
  };

  const forceRefresh = async () => {
    const ok = await dialog.confirm({
      title: "Start a fresh login?",
      desc: "This throws away the saved login and signs in again with what's saved here. Use it if things stopped working or you changed your password. Instagram may ask for a security code.",
      ok: "Fresh login",
    });
    if (ok) start(() => api.post("/account/connect", { force: true }), "Signing in again from scratch...");
  };

  const signOut = async () => {
    const ok = await dialog.confirm({
      title: "Sign out of Instagram?",
      desc: "The app will stop checking your chats. Your library and settings are kept. The saved password and cookie are removed too.",
      ok: "Sign out",
      danger: true,
    });
    if (!ok) return;
    await api.post("/account/logout", { forget: true }).catch(toast.error);
    toast.ok("Signed out of Instagram.");
    load();
    refreshStatus();
  };

  const clearSecret = async (which: "password" | "sessionid") => {
    try {
      const a = await api.put<Account>("/account", which === "password" ? { clear_password: true } : { clear_sessionid: true });
      setAcc(a);
      toast.ok(which === "password" ? "Password removed." : "Cookie removed.");
    } catch (e) {
      toast.error(e);
    }
  };

  if (!acc) return <div className="page"><div className="empty"><Spinner size={28} /></div></div>;

  const stateText: Record<string, string> = {
    connected: "Connected",
    connecting: "Connecting...",
    needs_code: "Waiting for a security code",
    error: "Needs attention",
    disconnected: ig?.session_saved ? "Saved login ready" : "Not connected",
  };

  return (
    <div className="page" style={{ maxWidth: 920 }}>
      <div className="page-head">
        <div>
          <h1>Instagram account</h1>
          <p>The account the app uses to read your chats. It's best to use your own account (or a second account that's in the same groups).</p>
        </div>
      </div>

      <div className="stack">
        {/* Status */}
        <div className="card card-pad">
          <div className="row" style={{ alignItems: "flex-start", gap: 16, flexWrap: "wrap" }}>
            <div className="avatar" style={{ background: "var(--brand)", width: 54, height: 54, borderRadius: 17 }}>
              <AtSign size={24} />
            </div>
            <div style={{ flex: 1, minWidth: 220 }}>
              <div className="row" style={{ gap: 8 }}>
                <StatusDot state={ig?.state ?? "disconnected"} />
                <h2>{stateText[ig?.state ?? "disconnected"]}</h2>
              </div>
              <p className="muted" style={{ marginTop: 4 }}>
                {ig?.state === "connected" ? <>Signed in as <strong>@{ig.username}</strong>{ig.last_ok && ` · since ${ago(ig.last_ok)}`}</> : ig?.message || (ig?.session_saved ? "A saved login exists. Press Connect to check it still works." : "Fill in the details below, then press Save and connect.")}
              </p>
            </div>
            <div className="row-wrap">
              <Button variant="primary" icon={PlugZap} loading={!!connecting} onClick={() => start(() => api.post("/account/connect", { force: false }), "Connecting to Instagram...")}>
                {ig?.state === "connected" ? "Test connection" : "Connect"}
              </Button>
              <Button icon={RefreshCw} onClick={forceRefresh} disabled={!!connecting} title="Forget the saved login and sign in again">Fresh login</Button>
            </div>
          </div>
          {ig?.state === "error" && ig.message && (
            <div className="callout callout-bad" style={{ marginTop: 16 }}><AlertTriangle size={18} /><span>{ig.message}</span></div>
          )}
          {ig?.paused_until && new Date(ig.paused_until) > new Date() && (
            <div className="callout callout-warn" style={{ marginTop: 16, alignItems: "center" }}>
              <PauseCircle size={18} />
              <span style={{ flex: 1 }}>Instagram asked us to slow down, so automatic checks are paused until {inTime(ig.paused_until).replace(/^at /, "")}. This protects your account.</span>
              <Button size="sm" icon={PlayCircle} onClick={async () => { await api.post("/account/resume"); refreshStatus(); toast.ok("Resumed."); }}>Resume now</Button>
            </div>
          )}
        </div>

        {/* Method */}
        <div className="card card-pad">
          <div className="card-head">
            <div>
              <h3>How should the app sign in?</h3>
              <p>If Instagram says “your version of Instagram is out of date”, use the browser cookie.</p>
            </div>
          </div>
          <div className="method-cards">
            {METHODS.map((m) => (
              <button key={m.value} type="button" className={`method-card ${method === m.value ? "on" : ""}`} onClick={() => setMethod(m.value)}>
                <span className="t"><m.icon size={17} /> {m.title}</span>
                <span className="d">{m.desc}</span>
              </button>
            ))}
          </div>
        </div>

        {/* Username + password */}
        {method !== "sessionid" && (
          <div className="card card-pad stack">
            <div>
              <h3>Username and password</h3>
              <p className="muted small" style={{ marginTop: 2 }}>The password is stored encrypted on this computer and only sent to Instagram.</p>
            </div>
            <div className="grid-2">
              <div className="field">
                <label htmlFor="ig-user">Instagram username</label>
                <div className="input-wrap">
                  <AtSign size={16} />
                  <input id="ig-user" className="input" value={username} onChange={(e) => setUsername(e.target.value.replace(/^@/, ""))} placeholder="your.username" autoComplete="off" autoCapitalize="none" spellCheck={false} />
                </div>
              </div>
              <div className="field">
                <label htmlFor="ig-pw">Password</label>
                <div className="pw-row">
                  <div className="input-wrap" style={{ flex: 1 }}>
                    <Lock size={16} />
                    <input id="ig-pw" className="input" type={showPw ? "text" : "password"} value={password} onChange={(e) => setPassword(e.target.value)} placeholder={acc.has_password ? "Saved. Type to replace it" : "Your Instagram password"} autoComplete="new-password" />
                    <Button type="button" variant="ghost" size="sm" iconOnly className="input-action" icon={showPw ? EyeOff : Eye} onClick={() => setShowPw(!showPw)} aria-label={showPw ? "Hide password" : "Show password"} />
                  </div>
                </div>
                {acc.has_password && (
                  <div className="row">
                    <span className="saved-note"><CheckCircle2 size={14} /> Password saved {acc.password_updated && ago(acc.password_updated)}</span>
                    <Button size="sm" variant="ghost" onClick={() => clearSecret("password")}>Remove</Button>
                  </div>
                )}
              </div>
            </div>
          </div>
        )}

        {/* Browser cookie */}
        {method !== "password" && (
          <div className="card card-pad stack">
            <div className="row" style={{ alignItems: "flex-start" }}>
              <div style={{ flex: 1 }}>
                <h3>Browser cookie (sessionid)</h3>
                <p className="muted small" style={{ marginTop: 2 }}>Lets the app use the login you already have in your web browser. Treat it like a password.</p>
              </div>
              {acc.has_sessionid && (
                <div className="row">
                  <span className="saved-note"><CheckCircle2 size={14} /> Saved {acc.sessionid_updated && ago(acc.sessionid_updated)}</span>
                  <Button size="sm" variant="ghost" onClick={() => clearSecret("sessionid")}>Remove</Button>
                </div>
              )}
            </div>
            <div className="grid-2" style={{ alignItems: "start" }}>
              <ol className="steps">
                <li><span>On a computer, open <a href="https://www.instagram.com" target="_blank" rel="noreferrer noopener">instagram.com</a> in Chrome or Edge and log in.</span></li>
                <li><span>Press <span className="kbd">F12</span> (on Mac: <span className="kbd">⌥</span><span className="kbd">⌘</span><span className="kbd">I</span>) to open the developer tools.</span></li>
                <li><span>Go to the <strong>Application</strong> tab → <strong>Cookies</strong> → <strong>https://www.instagram.com</strong>. (Firefox: <strong>Storage</strong> tab.)</span></li>
                <li><span>Find the row named <strong>sessionid</strong>, double-click its <strong>Value</strong>, copy it and paste it here.</span></li>
              </ol>
              <div className="field">
                <label htmlFor="ig-sid">Paste the sessionid value</label>
                <textarea id="ig-sid" className="textarea mono" rows={4} value={sessionid} onChange={(e) => setSessionid(e.target.value)} placeholder={acc.has_sessionid ? "Saved. Paste a new one to replace it" : "1234567890%3AAbCdEf..."} spellCheck={false} autoComplete="off" />
                {sessionid && (
                  sidValid
                    ? <span className="saved-note"><CheckCircle2 size={14} /> Looks right</span>
                    : <span className="small" style={{ color: "var(--warn)" }}>Hmm, a sessionid starts with numbers followed by %3A or a colon.</span>
                )}
                <span className="hint">Logging out of instagram.com in the browser ends this cookie too, so just close the tab instead.</span>
              </div>
            </div>
          </div>
        )}

        <div className="row-wrap" style={{ justifyContent: "flex-end" }}>
          <Button onClick={() => save(false)} disabled={!dirty || saving}>Save only</Button>
          <Button variant="brand" size="lg" icon={Sparkles} loading={saving} disabled={saving} onClick={() => save(true)}>
            Save and connect
          </Button>
        </div>

        <div className="card card-pad">
          <div className="row" style={{ alignItems: "flex-start", gap: 14 }}>
            <ShieldCheck size={22} style={{ color: "var(--ok)", flex: "none" }} />
            <div className="stack-sm" style={{ flex: 1 }}>
              <h3>Keeping your account safe</h3>
              <ul className="muted small" style={{ margin: 0, paddingLeft: 18, lineHeight: 1.8 }}>
                <li>The app works like a careful person: one thing at a time, with short pauses between requests.</li>
                <li>If Instagram asks for a security code, a box pops up here. Type the code from your email, SMS or authenticator app.</li>
                <li>If Instagram ever asks you to confirm a login in the app, tap <em>“It was me”</em>, then press Connect here.</li>
                <li>This uses Instagram's unofficial app interface. Light, normal use is fine; downloading huge histories over and over isn't.</li>
              </ul>
            </div>
            <Button variant="danger" icon={LogOut} onClick={signOut}>Sign out</Button>
          </div>
        </div>
      </div>
    </div>
  );
}
