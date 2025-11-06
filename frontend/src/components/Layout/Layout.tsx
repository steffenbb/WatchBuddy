import React from "react";
import Sidebar from "./Sidebar";
import BottomNav from "./BottomNav";
import Header from "../Header";

interface LayoutProps {
  onLogoClick?: () => void;
  children: React.ReactNode;
}

export default function Layout({ onLogoClick, children }: LayoutProps) {
  return (
    <div className="min-h-screen bg-gradient-to-br from-indigo-950 via-purple-950 to-fuchsia-950 text-white">
      <div className="flex">
        <Sidebar />
        <div className="flex-1 min-w-0">
          <Header onLogoClick={onLogoClick} />
          <main className="container mx-auto px-4 py-6 max-w-7xl pb-24 md:pb-6">
            {children}
          </main>
        </div>
      </div>
      <BottomNav />
    </div>
  );
}
