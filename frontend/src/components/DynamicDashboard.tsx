import React from "react";
import DynamicListsPanel from "./DynamicListsPanel";
import ChatListCreator from "./ChatListCreator";

export default function DynamicDashboard() {
  return (
    <div className="p-6">
      <h2 className="text-2xl font-bold mb-4">Dynamic Lists & Chat Creator</h2>
      <div className="mb-8">
        <DynamicListsPanel />
      </div>
      <div>
        <ChatListCreator />
      </div>
    </div>
  );
}
