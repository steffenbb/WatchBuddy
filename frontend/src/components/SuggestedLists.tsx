import React from "react";
import { api } from "../hooks/useApi";
import { useToast } from "./ToastProvider";

interface Suggestion {
  title: string;
  description: string;
  filters: any;
  type: string;
  priority: number;
  item_limit: number;
  icon: string;
  color: string;
  final_score?: number;
  _removing?: boolean;
}

export default function SuggestedLists({ onCreate }:{ onCreate?: ()=>void }){
  const [suggestions, setSuggestions] = React.useState<Suggestion[]>([]);
  const [loading, setLoading] = React.useState<boolean>(true);
  const [error, setError] = React.useState<string>("");
  const [creatingId, setCreatingId] = React.useState<string | null>(null);
  const [removingId, setRemovingId] = React.useState<string | null>(null);
  const { addToast } = useToast();
  // Remove a suggestion and fetch a new one to replace it
  // Confirmation dialog state
  const [confirmIdx, setConfirmIdx] = React.useState<number | null>(null);
  const [restoreSuggestion, setRestoreSuggestion] = React.useState<{s: Suggestion, idx: number} | null>(null);

  // Helper to extract error message
  function extractErrorMessage(e: any): string {
    if (e?.response?.data?.detail) {
      return Array.isArray(e.response.data.detail) ? e.response.data.detail.join(', ') : e.response.data.detail;
    } else if (e?.response?.data?.message) {
      return e.response.data.message;
    } else if (e?.response?.statusText) {
      return `HTTP ${e.response.status}: ${e.response.statusText}`;
    } else if (e?.message) {
      return e.message;
    } else if (typeof e === 'string') {
      return e;
    } else if (e && typeof e === 'object') {
      try { return JSON.stringify(e, null, 2); } catch { return String(e); }
    }
    return "Unknown error";
  }

  async function removeSuggestion(suggestion: Suggestion, idx: number) {
    setConfirmIdx(idx);
  }

  async function confirmRemove(suggestion: Suggestion, idx: number) {
    const suggestionId = `${suggestion.type}-${suggestion.title}`;
    setRemovingId(suggestionId);
    setConfirmIdx(null);
    let removed: Suggestion | null = null;
    setSuggestions(prev => {
      removed = prev[idx];
      return prev.map((s, i) => i === idx ? { ...s, _removing: true } : s);
    });
    setTimeout(async () => {
      setSuggestions(prev => prev.filter((_, i) => i !== idx));
      try {
        const resp = await api.get("/suggested/fallback");
        const newSuggestion = (resp.data.suggestions || []).find((s: Suggestion) =>
          !suggestions.some(existing => existing.title === s.title && existing.type === s.type)
        );
        if (newSuggestion) {
          setSuggestions(prev => {
            const arr = [...prev];
            arr.splice(idx, 0, newSuggestion);
            return arr;
          });
        }
      } catch (e) {
        if (removed) {
          setSuggestions(prev => {
            const arr = [...prev];
            arr.splice(idx, 0, removed!);
            return arr;
          });
        }
        setError("Failed to regenerate suggestion. " + extractErrorMessage(e));
        console.error("Error fetching replacement suggestion", e);
      } finally {
        setRemovingId(null);
      }
    }, 400);
  }

  React.useEffect(() => {
    loadSuggestions();
  }, []);

  async function loadSuggestions() {
    try {
      setLoading(true);
      setError("");
      const response = await api.get("/suggested/");
      setSuggestions(response.data.suggestions || []);
    } catch (e: any) {
      console.error("Error loading suggestions:", e);
      setError("Failed to load suggestions. " + extractErrorMessage(e));
      // Load fallback suggestions
      try {
        const fallbackResponse = await api.get("/suggested/fallback");
        setSuggestions(fallbackResponse.data.suggestions || []);
      } catch (fallbackError) {
        console.error("Error loading fallback suggestions:", fallbackError);
      }
    } finally {
      setLoading(false);
    }
  }

  async function createList(suggestion: Suggestion, idx: number) {
    const suggestionId = `${suggestion.type}-${suggestion.title}`;
    setCreatingId(suggestionId);
    try {
      const payload = { 
        suggestion: { ...suggestion, title: suggestion.title },
        user_id: 1  // Always use user_id=1 for demo mode
      };
      await api.post("/suggested/create", payload);
      
      // Show success toast
      addToast({
        message: `✅ Created "${suggestion.title}" list successfully!`,
        type: 'success',
        duration: 4000
      });
      
      // Remove the suggestion from the list
      setSuggestions(prev => prev.filter((_, i) => i !== idx));
      
      // Try to fetch a replacement suggestion
      try {
        const resp = await api.get("/suggested/fallback");
        const newSuggestion = (resp.data.suggestions || []).find((s: Suggestion) =>
          !suggestions.some(existing => existing.title === s.title && existing.type === s.type)
        );
        if (newSuggestion) {
          setSuggestions(prev => [...prev, newSuggestion]);
        }
      } catch (fallbackError) {
        console.warn("Could not fetch replacement suggestion:", fallbackError);
        // Don't show error for this as it's not critical
      }
      
      if(onCreate) onCreate();
    } catch(e:any){
      setError("Error creating list: " + extractErrorMessage(e));
      console.error("Full error object:", e);
      
      // Show error toast
      addToast({
        message: `❌ Failed to create "${suggestion.title}" list`,
        type: 'error',
        duration: 5000
      });
    } finally {
      setCreatingId(null);
    }
  }

  const getColorClasses = (color: string) => {
    const colorMap: Record<string, string> = {
      purple: "from-purple-500 to-purple-600 border-purple-200",
      emerald: "from-emerald-500 to-emerald-600 border-emerald-200",
      yellow: "from-yellow-500 to-yellow-600 border-yellow-200",
      indigo: "from-indigo-500 to-indigo-600 border-indigo-200",
      blue: "from-blue-500 to-blue-600 border-blue-200",
      amber: "from-amber-500 to-amber-600 border-amber-200",
      green: "from-green-500 to-green-600 border-green-200",
      rose: "from-rose-500 to-rose-600 border-rose-200",
      cyan: "from-cyan-500 to-cyan-600 border-cyan-200",
      violet: "from-violet-500 to-violet-600 border-violet-200",
      orange: "from-orange-500 to-orange-600 border-orange-200",
      red: "from-red-500 to-red-600 border-red-200",
      pink: "from-pink-500 to-pink-600 border-pink-200"
    };
    return colorMap[color] || "from-gray-500 to-gray-600 border-gray-200";
  };

  if (loading) {
    return (
      <div className="bg-gradient-to-br from-slate-50 to-gray-100 p-6 rounded-xl shadow-lg border border-gray-200">
        <div className="flex items-center gap-3 mb-4">
          <div className="w-8 h-8 bg-gradient-to-r from-blue-500 to-indigo-600 rounded-lg flex items-center justify-center">
            <span className="text-white text-sm font-bold">💡</span>
          </div>
          <h3 className="text-xl font-bold text-gray-800">Suggested Lists</h3>
        </div>
        <div className="flex items-center justify-center py-8">
          <div className="flex items-center gap-3">
            <div className="w-6 h-6 border-2 border-blue-600 border-t-transparent rounded-full animate-spin"></div>
            <span className="text-gray-600">Analyzing your preferences...</span>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="bg-gradient-to-br from-slate-50 to-gray-100 p-6 rounded-xl shadow-lg border border-gray-200">
      <div className="flex items-center gap-3 mb-6">
        <div className="w-8 h-8 bg-gradient-to-r from-blue-500 to-indigo-600 rounded-lg flex items-center justify-center">
          <span className="text-white text-sm font-bold">💡</span>
        </div>
          <h3 className="text-xl font-bold text-gray-800">Suggested Lists</h3>
      </div>

      {error && (
        <div className="mb-4 p-3 bg-red-100 border border-red-200 rounded-lg text-red-800 text-sm">
          {error}
        </div>
      )}

      {suggestions.length === 0 ? (
        <div className="text-center py-8">
          <div className="text-gray-500 mb-4">
            <span className="text-4xl">📋</span>
          </div>
          <p className="text-gray-600">No suggestions available at the moment.</p>
          <button 
            onClick={loadSuggestions}
            className="mt-3 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors"
          >
            Refresh
          </button>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {suggestions.map((suggestion, i) => {
            const suggestionId = `${suggestion.type}-${suggestion.title}`;
            const isCreating = creatingId === suggestionId;
            const isRemoving = removingId === suggestionId;
            return (
              <div key={i} className={`bg-white rounded-lg border border-gray-200 p-4 hover:shadow-md transition-shadow relative ${suggestion._removing ? "opacity-60" : ""}`}
                aria-live="polite" aria-busy={isRemoving}>
                <div className="flex items-start gap-3 mb-3">
                  <div className={`w-8 h-8 bg-gradient-to-r ${getColorClasses(suggestion.color)} rounded-lg flex items-center justify-center text-white text-sm`}>
                    {suggestion.icon}
                  </div>
                  <div className="flex-1">
                    <h4 className="font-semibold text-gray-800 mb-1">{suggestion.title}</h4>
                    <p className="text-xs text-gray-600 leading-relaxed">{suggestion.description}</p>
                  </div>
                  <button
                    aria-label="Remove and regenerate suggestion"
                    title="Remove and regenerate suggestion"
                    onClick={() => removeSuggestion(suggestion, i)}
                    disabled={isRemoving}
                    className={`ml-2 px-2 py-1 text-xs rounded border border-gray-300 bg-gray-100 hover:bg-red-100 text-gray-500 hover:text-red-600 transition ${isRemoving ? "opacity-60 cursor-not-allowed" : ""}`}
                  >
                    {isRemoving ? (
                      <span className="inline-flex items-center gap-1"><span className="w-3 h-3 border border-red-400 border-t-transparent rounded-full animate-spin"></span>Removing…</span>
                    ) : (
                      <span>✕</span>
                    )}
                  </button>
                </div>
                <div className="flex items-center justify-between mt-4">
                  <div className="text-xs text-gray-500">
                    {suggestion.item_limit} items
                  </div>
                  <button 
                    aria-label="Create list from suggestion"
                    onClick={() => createList(suggestion, i)}
                    disabled={isCreating}
                    className={`px-4 py-2 rounded-lg text-sm font-medium transition-all ${
                      isCreating
                        ? "bg-gray-400 text-white cursor-not-allowed"
                        : `bg-gradient-to-r ${getColorClasses(suggestion.color)} text-white hover:shadow-lg transform hover:scale-105 active:scale-95`
                    }`}
                  >
                    {isCreating ? (
                      <div className="flex items-center gap-2">
                        <div className="w-3 h-3 border border-white border-t-transparent rounded-full animate-spin"></div>
                        Creating...
                      </div>
                    ) : (
                      "Create List"
                    )}
                  </button>
                </div>
                {/* Confirmation dialog */}
                {confirmIdx === i && (
                  <div className="absolute inset-0 bg-black bg-opacity-30 flex items-center justify-center z-10 rounded-lg">
                    <div className="bg-white border border-gray-300 rounded-lg p-4 shadow-xl flex flex-col items-center">
                      <p className="mb-3 text-gray-800 text-sm">Remove this suggestion and get a new one?</p>
                      <div className="flex gap-2">
                        <button
                          className="px-3 py-1 rounded bg-red-600 text-white text-xs hover:bg-red-700"
                          onClick={() => confirmRemove(suggestion, i)}
                          autoFocus
                        >Yes, remove</button>
                        <button
                          className="px-3 py-1 rounded bg-gray-200 text-gray-700 text-xs hover:bg-gray-300"
                          onClick={() => setConfirmIdx(null)}
                        >Cancel</button>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      <div className="mt-6 p-3 bg-blue-50 rounded-lg border border-blue-100">
        <p className="text-xs text-blue-700">
          <strong>Personalized for you:</strong> These suggestions are based on your viewing history, 
          ratings, and preferences. Lists automatically update as your tastes evolve.
        </p>
      </div>
    </div>
  );
}
