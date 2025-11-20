import React from "react";
import { Home, ListChecks, Brain, Sparkles, Users, Activity, Settings, HelpCircle, BarChart3, Target } from "lucide-react";

type NavItem = {
  key: string;
  label: string;
  icon: React.ReactNode;
  onClick?: () => void;
};

export const navItems: NavItem[] = [
  { key: "home", label: "Home", icon: <Home size={18} />, onClick: () => { window.location.hash = ""; } },
  { key: "smart", label: "SmartLists", icon: <ListChecks size={18} />, onClick: () => { window.location.hash = "lists"; } },
  { key: "ai", label: "AI Lists", icon: <Brain size={18} />, onClick: () => { window.location.hash = "dynamic"; } },
  { key: "individual", label: "Individual Lists", icon: <Users size={18} />, onClick: () => { window.location.hash = "myLists"; } },
  { key: "trainer", label: "Preference Trainer", icon: <Target size={18} />, onClick: () => { window.location.hash = "trainer"; } },
  { key: "status", label: "Status", icon: <Activity size={18} />, onClick: () => { window.location.hash = "status"; } },
  { key: "settings", label: "Settings", icon: <Settings size={18} />, onClick: () => { window.location.hash = "settings"; } },
  { key: "help", label: "Help", icon: <HelpCircle size={18} />, onClick: () => { window.location.hash = "help"; } },
];

export default function Sidebar() {
  const [activeHash, setActiveHash] = React.useState(window.location.hash);

  React.useEffect(() => {
    const handleHashChange = () => {
      setActiveHash(window.location.hash);
    };
    window.addEventListener('hashchange', handleHashChange);
    return () => window.removeEventListener('hashchange', handleHashChange);
  }, []);

  const getActiveKey = () => {
    const hash = activeHash.replace('#', '');
    if (!hash || hash === '') return 'home'; // Default to home page
    if (hash === 'lists') return 'smart';
    if (hash === 'dynamic') return 'ai';
    if (hash === 'myLists') return 'individual';
    if (hash === 'trainer') return 'trainer';
    if (hash === 'status') return 'status';
    if (hash === 'settings') return 'settings';
    if (hash === 'help') return 'help';
    return 'home'; // Fallback to home
  };

  const currentActive = getActiveKey();

  return (
    <aside className="hidden md:flex md:flex-col w-60 shrink-0 h-screen sticky top-0 bg-white/5 backdrop-blur border-r border-white/10">
      <div className="px-4 py-4 text-sm font-semibold text-white/80 tracking-wide">WatchBuddy</div>
      <nav className="flex-1 px-2 py-3 space-y-1">
        {navItems.map((item) => {
          const isActive = item.key === currentActive;
          return (
            <button
              key={item.key}
              onClick={item.onClick}
              className={`group w-full flex items-center gap-3 px-3 py-2 rounded-lg text-left transition-colors ${
                isActive 
                  ? 'bg-gradient-to-r from-purple-500/20 to-pink-500/20 text-white border-l-2 border-purple-500' 
                  : 'text-white/80 hover:text-white hover:bg-white/10'
              }`}
            >
              <span className={isActive ? 'text-purple-400' : 'text-white/70 group-hover:text-white'}>{item.icon}</span>
              <span className="text-sm">{item.label}</span>
            </button>
          );
        })}
      </nav>
      <div className="px-3 py-3 text-xs text-white/50">v2</div>
    </aside>
  );
}
