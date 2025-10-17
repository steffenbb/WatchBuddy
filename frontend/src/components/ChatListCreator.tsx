import React from "react";
import { api } from "../hooks/useApi";

export default function ChatListCreator({ onCreate }: { onCreate?: () => void }) {
  const [prompt, setPrompt] = React.useState<string>("");
  const [itemLimit, setItemLimit] = React.useState<number>(30);
  const [isCreating, setIsCreating] = React.useState<boolean>(false);
  const [message, setMessage] = React.useState<string>("");
  const [messageType, setMessageType] = React.useState<"success" | "error" | "">("");
  const [createdList, setCreatedList] = React.useState<any>(null);

  async function generateChatList() {
    if (!prompt.trim()) {
      setMessage("Please enter a prompt describing what you want");
      setMessageType("error");
      return;
    }

    setMessage("");
    setMessageType("");
    setIsCreating(true);
    setCreatedList(null);

    try {
      const response = await api.post("/chat/generate-list", {
        prompt: prompt.trim(),
        item_limit: itemLimit,
        user_id: 1
      });

      setCreatedList(response.data);
      setMessage(`Successfully created list: "${response.data.title}" with ${response.data.item_count} items!`);
      setMessageType("success");
      setPrompt("");
      
      if (onCreate) onCreate();
    } catch (e: any) {
      let msg = "Unknown error";
      if (e?.response?.data?.detail) msg = e.response.data.detail;
      else if (e?.message) msg = e.message;
      else if (typeof e === "string") msg = e;
      setMessage("Error generating list: " + msg);
      setMessageType("error");
      console.error("Full error object:", e);
    } finally {
      setIsCreating(false);
    }
  }

  return (
    <div className="relative z-10 bg-white/90 backdrop-blur-xl rounded-3xl shadow-2xl border border-indigo-100 w-full max-w-xl mx-auto p-6 md:p-8 flex flex-col gap-4 transition-all duration-500">
      <div className="flex items-center gap-3 mb-4">
        <div className="w-8 h-8 bg-gradient-to-r from-green-500 to-teal-600 rounded-lg flex items-center justify-center">
          <span className="text-white text-sm font-bold">💬</span>
        </div>
        <h4 className="text-xl font-bold text-gray-800">Chat List Creator</h4>
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
      <div className="mb-4 p-4 bg-gradient-to-r from-green-50 to-teal-50 rounded-lg border border-green-200">
        <p className="text-sm text-gray-700">
          <strong>🤖 AI-Powered List Generation</strong>
          <br />
          Describe what you want in natural language, and we'll automatically create a curated list for you!
        </p>
      </div>

      {/* Prompt Input */}
      <div className="mb-4">
        <label className="block text-sm font-semibold text-gray-700 mb-2">
          What would you like to watch?
        </label>
        <textarea
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder="e.g., Give me 20 intense sci-fi thrillers from the 2010s, or Danish romantic comedies similar to Italian for Beginners"
          rows={4}
          className="w-full p-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-green-500 focus:border-transparent resize-none"
        />
        <div className="mt-2 flex flex-wrap gap-2">
          <button
            onClick={() => setPrompt("Give me 30 cozy Danish movies from the 90s")}
            className="text-xs px-2 py-1 bg-green-50 text-green-700 rounded border border-green-200 hover:bg-green-100"
          >
            💡 Example 1
          </button>
          <button
            onClick={() => setPrompt("20 intense thrillers similar to The Dark Knight")}
            className="text-xs px-2 py-1 bg-green-50 text-green-700 rounded border border-green-200 hover:bg-green-100"
          >
            💡 Example 2
          </button>
          <button
            onClick={() => setPrompt("Uplifting comedies from 2015 to 2020")}
            className="text-xs px-2 py-1 bg-green-50 text-green-700 rounded border border-green-200 hover:bg-green-100"
          >
            💡 Example 3
          </button>
        </div>
      </div>

      {/* Item Limit */}
      <div className="mb-4">
        <label className="block text-sm font-semibold text-gray-700 mb-2">
          Number of items
        </label>
        <select
          value={itemLimit}
          onChange={(e) => setItemLimit(Number(e.target.value))}
          className="w-full p-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-green-500 focus:border-transparent"
        >
          <option value={10}>10 items</option>
          <option value={20}>20 items</option>
          <option value={30}>30 items</option>
          <option value={50}>50 items</option>
        </select>
      </div>

      {/* Generate Button */}
      <button
        onClick={generateChatList}
        disabled={isCreating || !prompt.trim()}
        className={`w-full py-3 px-4 rounded-lg font-semibold text-white transition-all duration-200 ${
          isCreating || !prompt.trim()
            ? "bg-gray-400 cursor-not-allowed"
            : "bg-gradient-to-r from-green-600 to-teal-600 hover:from-green-700 hover:to-teal-700 shadow-lg hover:shadow-xl transform hover:scale-[1.02] active:scale-[0.98]"
        }`}
      >
        {isCreating ? (
          <div className="flex items-center justify-center gap-2">
            <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin"></div>
            Generating your list...
          </div>
        ) : (
          <span>✨ Generate List</span>
        )}
      </button>

      {/* Created List Preview */}
      {createdList && (
        <div className="mt-4 p-4 bg-gradient-to-r from-green-50 to-teal-50 rounded-lg border border-green-200">
          <h5 className="font-semibold text-gray-800 mb-2">✅ List Created!</h5>
          <p className="text-sm text-gray-700 mb-1">
            <strong>Title:</strong> {createdList.title}
          </p>
          <p className="text-sm text-gray-700">
            <strong>Items:</strong> {createdList.item_count}
          </p>
        </div>
      )}

      {/* Help Text */}
      <div className="mt-4 p-3 bg-green-50 rounded-lg border border-green-100">
        <p className="text-xs text-green-700">
          <strong>💡 Pro Tips:</strong>
          <br />
          • Be specific: include genres, years, moods, or languages
          <br />
          • Use "similar to [movie name]" for recommendations
          <br />
          • The list title is automatically generated from your prompt
        </p>
      </div>
    </div>
  );
}
