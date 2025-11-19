import React from "react";
import { navItems } from "./Sidebar";

export default function BottomNav() {
  const [activeHash, setActiveHash] = React.useState(window.location.hash);

  React.useEffect(() => {
    const handleHashChange = () => setActiveHash(window.location.hash);
    window.addEventListener("hashchange", handleHashChange);
    return () => window.removeEventListener("hashchange", handleHashChange);
  }, []);

  const getActiveKey = () => {
    const hash = activeHash.replace("#", "");
    if (!hash || hash === "") return "home";
    if (hash === "overview") return "overview";
    if (hash === "lists") return "smart";
    if (hash === "dynamic") return "ai";
    if (hash === "myLists") return "individual";
    if (hash === "trainer") return "trainer";
    if (hash === "status") return "status";
    if (hash === "settings") return "settings";
    if (hash === "help") return "help";
    return "home";
  };

  const activeKey = getActiveKey();

  return (
    <nav className="md:hidden fixed bottom-0 left-0 right-0 z-40 bg-black/80 backdrop-blur-xl border-t border-white/20">
      <div className="flex items-center justify-around px-1 py-2 safe-area-inset-bottom">
        {navItems.map((item) => {
          const isActive = item.key === activeKey;
          return (
            <button
              key={item.key}
              onClick={item.onClick}
              className={`flex flex-col items-center gap-1 px-2 py-2 rounded-xl min-w-[48px] transition-all ${
                isActive 
                  ? "text-white bg-white/20" 
                  : "text-white/60 hover:text-white hover:bg-white/10"
              }`}
            >
              <span className={isActive ? "text-white" : "text-white/70"}>
                {item.icon}
              </span>
              <span className="text-[9px] leading-tight font-medium truncate max-w-[44px]">
                {item.label}
              </span>
            </button>
          );
        })}
      </div>
    </nav>
  );
}
