import { useCallback, useEffect, useState } from "react";
import { Activity as ActivityIcon, AtSign, FolderOpen, Home as HomeIcon, LogOut, MessagesSquare, Settings as SettingsIcon, Sparkles } from "lucide-react";
import { api } from "./api";
import { Providers, SignedInProviders, go, useApp, useRoute } from "./lib/app";
import { StatusDot, Spinner } from "./components/ui";
import { JobDock } from "./components/JobDock";
import { CodePrompt } from "./components/CodePrompt";
import AuthPage from "./pages/Auth";
import HomePage from "./pages/Home";
import ChatsPage from "./pages/Chats";
import LibraryPage from "./pages/Library";
import ActivityPage from "./pages/Activity";
import AccountPage from "./pages/Account";
import SettingsPage from "./pages/Settings";

type AuthState = { setup_needed: boolean; authenticated: boolean } | null;

export default function App() {
  const [auth, setAuth] = useState<AuthState>(null);
  const check = useCallback(() => {
    api.get<{ setup_needed: boolean; authenticated: boolean }>("/auth/state").then(setAuth).catch(() => setAuth({ setup_needed: false, authenticated: false }));
  }, []);
  useEffect(() => {
    check();
    const out = () => setAuth((a) => (a ? { ...a, authenticated: false } : a));
    window.addEventListener("igcm:signed-out", out);
    return () => window.removeEventListener("igcm:signed-out", out);
  }, [check]);

  return (
    <Providers>
      {auth === null ? (
        <div className="auth-wrap">
          <Spinner size={28} />
        </div>
      ) : !auth.authenticated ? (
        <AuthPage setup={auth.setup_needed} onDone={check} />
      ) : (
        <SignedInProviders>
          <Shell onSignOut={check} />
        </SignedInProviders>
      )}
    </Providers>
  );
}

const NAV = [
  { key: "home", label: "Home", icon: HomeIcon },
  { key: "chats", label: "Chats", icon: MessagesSquare },
  { key: "library", label: "Library", icon: FolderOpen },
  { key: "activity", label: "Activity", icon: ActivityIcon },
  { key: "account", label: "Instagram", icon: AtSign },
  { key: "settings", label: "Settings", icon: SettingsIcon },
];

function Shell({ onSignOut }: { onSignOut: () => void }) {
  const route = useRoute();
  const page = route[0] ?? "home";
  const { status, settings } = useApp();
  const ig = status?.instagram;
  const running = (status?.active_jobs.length ?? 0) > 0;

  useEffect(() => {
    try {
      localStorage.setItem("igcm-theme", document.documentElement.dataset.theme ?? "light");
    } catch {
      /* ignore */
    }
  }, [settings?.theme]);

  const signOut = async () => {
    await api.post("/auth/logout").catch(() => undefined);
    onSignOut();
  };

  const igLabel =
    ig?.state === "connected" ? `@${ig.username ?? "connected"}` : ig?.state === "connecting" ? "Connecting..." : ig?.state === "needs_code" ? "Waiting for code" : ig?.state === "error" ? "Needs attention" : ig?.session_saved ? "Saved login ready" : "Not connected";

  let content;
  switch (page) {
    case "chats":
      content = <ChatsPage />;
      break;
    case "library":
      content = <LibraryPage route={route.slice(1)} />;
      break;
    case "activity":
      content = <ActivityPage jobId={route[1] ? Number(route[1]) : undefined} />;
      break;
    case "account":
      content = <AccountPage />;
      break;
    case "settings":
      content = <SettingsPage onSignOut={signOut} />;
      break;
    default:
      content = <HomePage />;
  }

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">
            <Sparkles size={19} />
          </div>
          <div>
            <div className="brand-name">Chat Media</div>
            <div className="brand-sub">Instagram saver</div>
          </div>
        </div>
        {NAV.map((n) => (
          <a key={n.key} href={`#/${n.key}`} className={`nav-item ${page === n.key ? "active" : ""}`}>
            <n.icon size={19} />
            {n.label}
            {n.key === "activity" && running && <span className="badge-dot pulse" title="Working..." />}
            {n.key === "account" && ig && ig.state !== "connected" && <span className={`badge-dot ${ig.state === "error" ? "dot-bad" : "dot-warn"}`} />}
          </a>
        ))}
        <div className="sidebar-foot">
          <button className="conn-chip" onClick={() => go("account")}>
            <StatusDot state={ig?.state ?? "disconnected"} />
            <div className="ellipsis">
              <strong className="ellipsis">{igLabel}</strong>
              {status?.auto_sync && status.chats.auto > 0 ? "Auto-check is on" : "Auto-check is off"}
            </div>
          </button>
          <button className="nav-item" onClick={signOut}>
            <LogOut size={18} /> Lock dashboard
          </button>
        </div>
      </aside>

      <main className="main">
        <div className="mobile-top">
          <div className="brand-mark" style={{ width: 30, height: 30, borderRadius: 9 }}>
            <Sparkles size={16} />
          </div>
          <strong>Chat Media</strong>
          <span className="spacer" />
          <button className="conn-chip" style={{ padding: "6px 10px" }} onClick={() => go("account")}>
            <StatusDot state={ig?.state ?? "disconnected"} />
            <span className="ellipsis" style={{ maxWidth: 140 }}>{igLabel}</span>
          </button>
        </div>
        {content}
      </main>

      <nav className="bottom-nav">
        {NAV.filter((n) => n.key !== "activity").map((n) => (
          <a key={n.key} href={`#/${n.key}`} className={page === n.key ? "active" : ""}>
            <n.icon size={21} />
            {n.label}
          </a>
        ))}
      </nav>

      {page !== "activity" && <JobDock />}
      <CodePrompt />
    </div>
  );
}
