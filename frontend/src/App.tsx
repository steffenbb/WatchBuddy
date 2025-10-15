import React, { useEffect, useState } from "react";
import Header from "./components/Header";
import Dashboard from "./components/Dashboard";
import SetupScreen from "./components/SetupScreen";
import HelpPage from "./components/HelpPage";
import { ToastProvider } from "./components/ToastProvider";
import { useTraktOAuth } from "./hooks/useTraktOAuth";

export default function App() {
  const [showSetup, setShowSetup] = useState<boolean | null>(null);
  const [checking, setChecking] = useState(false);
  const [onNavigateHome, setOnNavigateHome] = useState<(() => void) | null>(null);
  const [showHelp, setShowHelp] = useState<boolean>(window.location.hash === "#help");
  const { startOAuth } = useTraktOAuth({ onAuth: () => reloadAuth() });

  async function reloadAuth() {
    try {
      setChecking(true);
      // Check if Trakt credentials exist
      const credsRes = await fetch("/api/settings/trakt-credentials");
      const creds = credsRes.ok ? await credsRes.json() : { configured: false };

      // Check Trakt authentication status
      const authRes = await fetch("/api/trakt/status");
      const auth = authRes.ok ? await authRes.json() : { authenticated: false };

      // Gate to setup if missing credentials OR not authenticated yet
      setShowSetup(!(creds.configured && auth.authenticated));
    } catch {
      // On any error, prefer showing setup so user can fix credentials
      setShowSetup(true);
    } finally {
      setChecking(false);
    }
  }

  useEffect(() => {
    reloadAuth();
    const onHashChange = () => setShowHelp(window.location.hash === "#help");
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  if (showSetup === null || checking) {
    return <div className="flex items-center justify-center min-h-screen">Loading...</div>;
  }

  if (showSetup) {
    return (
      <ToastProvider>
        <SetupScreen onTraktConnect={startOAuth} />
      </ToastProvider>
    );
  }

  if (showHelp) {
    return (
      <ToastProvider>
        <div className="min-h-screen bg-gradient-to-br from-fuchsia-100 via-indigo-50 to-blue-100">
          <Header onLogoClick={() => { window.location.hash = ""; setShowHelp(false); }} />
          <main className="container mx-auto px-4 py-4 max-w-7xl">
            <HelpPage />
          </main>
        </div>
      </ToastProvider>
    );
  }

  return (
    <ToastProvider>
      <div className="min-h-screen bg-gradient-to-br from-fuchsia-100 via-indigo-50 to-blue-100">
        <Header onLogoClick={onNavigateHome || undefined} />
        <main className="container mx-auto px-4 py-4 max-w-7xl">
          <Dashboard onRegisterNavigateHome={setOnNavigateHome} />
        </main>
      </div>
    </ToastProvider>
  );
}
