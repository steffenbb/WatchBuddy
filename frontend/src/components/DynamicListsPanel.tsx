import React from "react";
import { api } from "../hooks/useApi";

export default function DynamicListsPanel({ onRebuild }: { onRebuild?: () => void }) {
  const [isRebuilding, setIsRebuilding] = React.useState<boolean>(false);
  const [message, setMessage] = React.useState<string>("");
  const [messageType, setMessageType] = React.useState<"success" | "error" | "">("");

  async function rebuildDynamicLists() {
    setMessage("");
    setMessageType("");
    setIsRebuilding(true);
    try {
      // Trigger a full sync which includes dynamic list generation
      await api.post("/smartlists/sync", {
        force_full: true,
        user_id: 1
      });
      if (onRebuild) onRebuild();
      setMessage("Dynamic lists rebuilt successfully!");
      setMessageType("success");
    } catch (e: any) {
      let msg = "Unknown error";
      if (e?.response?.data?.detail) msg = e.response.data.detail;
      else if (e?.message) msg = e.message;
      else if (typeof e === "string") msg = e;
      else if (e && typeof e === "object") {
        msg = JSON.stringify(e);
      }
      setMessage("Error rebuilding dynamic lists: " + msg);
      setMessageType("error");
      console.error("Full error object:", e);
    } finally {
      setIsRebuilding(false);
    }
  }

  return (
    <div className="relative z-10 bg-white/90 backdrop-blur-xl rounded-3xl shadow-2xl border border-indigo-100 w-full max-w-xl mx-auto p-6 md:p-8 flex flex-col gap-4 transition-all duration-500">
      <div className="flex items-center gap-3 mb-4">
        <div className="w-8 h-8 bg-gradient-to-r from-purple-500 to-pink-600 rounded-lg flex items-center justify-center">
          <span className="text-white text-sm font-bold">✨</span>
        </div>
        <h4 className="text-xl font-bold text-gray-800">Dynamic Lists</h4>
      </div>

      {message && (
        <div
          className={`mb-4 p-3 rounded-lg text-sm font-medium ${
            messageType === "success"
              ? "bg-green-100 text-green-800 border border-green-200"
              : "bg-red-100 text-red-800 border border-red-200"
          }`}
        >
          {message}
        </div>
      )}

      {/* Description */}
      <div className="mb-6 p-4 bg-gradient-to-r from-purple-50 to-pink-50 rounded-lg border border-purple-200">
        <p className="text-sm text-gray-700 mb-3">
          Dynamic lists are automatically generated based on your preferences and viewing history.
          They include:
        </p>
        <ul className="space-y-2 text-sm text-gray-700">
          <li className="flex items-start gap-2">
            <span className="text-purple-600 font-bold">🎭</span>
            <span><strong>3 Mood Lists</strong> - Content matching different emotional tones (cozy, intense, uplifting, etc.)</span>
          </li>
          <li className="flex items-start gap-2">
            <span className="text-pink-600 font-bold">⚡</span>
            <span><strong>2 Fusion Lists</strong> - Creative genre combinations (sci-fi + thriller, comedy + crime, etc.)</span>
          </li>
          <li className="flex items-start gap-2">
            <span className="text-indigo-600 font-bold">🎬</span>
            <span><strong>2 Theme Lists</strong> - Curated selections around specific themes (noir, witty crime, etc.)</span>
          </li>
        </ul>
      </div>

      {/* List Status */}
      <div className="mb-6 p-4 bg-white rounded-lg border border-gray-200">
        <div className="flex items-center justify-between mb-2">
          <span className="text-sm font-semibold text-gray-700">Current Status</span>
          <span className="text-xs text-gray-500">7 active lists</span>
        </div>
        <div className="grid grid-cols-3 gap-2 text-xs">
          <div className="text-center p-2 bg-purple-50 rounded">
            <div className="font-bold text-purple-700">3</div>
            <div className="text-gray-600">Mood</div>
          </div>
          <div className="text-center p-2 bg-pink-50 rounded">
            <div className="font-bold text-pink-700">2</div>
            <div className="text-gray-600">Fusion</div>
          </div>
          <div className="text-center p-2 bg-indigo-50 rounded">
            <div className="font-bold text-indigo-700">2</div>
            <div className="text-gray-600">Theme</div>
          </div>
        </div>
      </div>

      {/* Rebuild Button */}
      <button
        onClick={rebuildDynamicLists}
        disabled={isRebuilding}
        className={`w-full py-3 px-4 rounded-lg font-semibold text-white transition-all duration-200 ${
          isRebuilding
            ? "bg-gray-400 cursor-not-allowed"
            : "bg-gradient-to-r from-purple-600 to-pink-600 hover:from-purple-700 hover:to-pink-700 shadow-lg hover:shadow-xl transform hover:scale-[1.02] active:scale-[0.98]"
        }`}
      >
        {isRebuilding ? (
          <div className="flex items-center justify-center gap-2">
            <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin"></div>
            Rebuilding...
          </div>
        ) : (
          <span>🔄 Rebuild All Dynamic Lists</span>
        )}
      </button>

      {/* Help Text */}
      <div className="mt-4 p-3 bg-purple-50 rounded-lg border border-purple-100">
        <p className="text-xs text-purple-700">
          <strong>Tip:</strong> Rebuilding refreshes all 7 dynamic lists with new recommendations
          based on your latest viewing history and preferences. Lists are automatically synced to Trakt!
        </p>
      </div>
    </div>
  );
}
