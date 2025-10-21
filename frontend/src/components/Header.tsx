import { useEffect, useState } from 'react';
import config from "../config.json";
import { NotificationLog } from './NotificationLog';

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
    <div className="flex items-center gap-1" title={title} aria-label={title}>
      <span className={`inline-block w-2 h-2 rounded-full ${color}`} />
      <span className="hidden sm:inline text-xs text-white/70">API</span>
    </div>
  );
}

export default function Header({ onLogoClick }: { onLogoClick?: () => void }){
  const [showNotifications, setShowNotifications] = useState(false);

  return (
    <>
  <header className="bg-white/10 backdrop-blur-lg rounded-b-3xl shadow-2xl border-b-2 border-white/20 p-4 sticky top-0 z-40 transition-all duration-500">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 md:gap-3 min-w-0">
            <button 
              onClick={onLogoClick} 
              className={`text-xl md:text-2xl font-bold truncate transition-colors text-white ${onLogoClick ? 'hover:text-purple-300 cursor-pointer' : ''}`}
              disabled={!onLogoClick}
            >
              {config.app_title}
            </button>
            <span className="text-xs md:text-sm text-white/70 hidden sm:inline">— personalized recommendations</span>
          </div>
          <div className="flex items-center gap-2 md:gap-4">
            <button
              onClick={() => { window.location.hash = '#help'; }}
              className="text-xs md:text-sm px-3 py-2 rounded-xl bg-white/10 text-white border border-white/20 hover:bg-white/20 transition-all min-h-[36px]"
              title="Help & Wiki"
            >
              📖 Help
            </button>
            <HealthIndicator />
            <button
              onClick={() => setShowNotifications(true)}
              className="relative p-2 hover:bg-white/20 rounded-xl transition-colors touch-manipulation min-h-[36px] min-w-[36px] flex items-center justify-center"
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
            <span className="text-xs md:text-sm whitespace-nowrap text-white/60">v{config.version}</span>
            <a 
              href={config.github_url} 
              target="_blank" 
              rel="noreferrer" 
              className="text-xs md:text-sm text-purple-300 hover:text-purple-200 underline hover:no-underline whitespace-nowrap hidden sm:inline transition"
            >
              GitHub
            </a>
          </div>
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
