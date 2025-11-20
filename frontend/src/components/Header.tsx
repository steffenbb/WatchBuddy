import { useEffect, useState } from 'react';
import config from "../config.json";
import { NotificationLog } from './NotificationLog';
import GlobalSearch from './GlobalSearch/GlobalSearch';

function HealthIndicator() {
  const [healthy, setHealthy] = useState<boolean | null>(null);

  useEffect(() => {
    let cancelled = false;
    const fetchHealth = async () => {
      try {
        const res = await fetch('/api/status/health');
        if (!res.ok) throw new Error('bad status');
        const data = await res.json();
        if (!cancelled) setHealthy(Boolean(data?.redis && data?.database));
      } catch {
        if (!cancelled) setHealthy(false);
      }
    };
    fetchHealth();
    const t = setInterval(fetchHealth, 10000);
    return () => { cancelled = true; clearInterval(t); };
  }, []);

  const color = healthy == null ? 'bg-white/40' : healthy ? 'bg-emerald-400 shadow-lg shadow-emerald-400/50' : 'bg-red-400 shadow-lg shadow-red-400/50';
  const title = healthy == null ? 'Checking health…' : healthy ? 'Backend healthy' : 'Backend unhealthy';

  return (
    <button className="flex items-center gap-1" title={title} aria-label={title} onClick={() => { window.location.hash = '#status'; }}>
      <span className={`inline-block w-2 h-2 rounded-full ${color}`} />
      <span className="hidden sm:inline text-xs text-white/70">API</span>
    </button>
  );
}

export default function Header({ onLogoClick }: { onLogoClick?: () => void }){
  const [showNotifications, setShowNotifications] = useState(false);

  return (
    <>
      {/* Minimal top-right header bar with only health + notifications */}
      <header className="sticky top-0 z-40 w-max ml-auto mt-2 mr-2 bg-white/10 backdrop-blur-lg rounded-bl-3xl shadow-2xl border border-white/20 p-2 sm:p-3 transition-all">
        <div className="flex items-center gap-2 sm:gap-3">
          <GlobalSearch />
          <HealthIndicator />
          <button
            onClick={() => setShowNotifications(true)}
            className="relative p-2 sm:p-2.5 hover:bg-white/20 rounded-xl transition-colors touch-manipulation min-h-[36px] min-w-[36px] flex items-center justify-center"
            title="Notifications"
            aria-label="Open notifications"
          >
            <svg
              className="w-5 h-5 text-white"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
              xmlns="http://www.w3.org/2000/svg"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9"
              />
            </svg>
          </button>
        </div>
      </header>

      <NotificationLog
        isOpen={showNotifications}
        onClose={() => setShowNotifications(false)}
        userId="1"
      />
    </>
  );
}
