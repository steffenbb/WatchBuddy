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
      <div className="bg-white/10 backdrop-blur-lg p-6 rounded-3xl shadow-2xl border border-white/20">
        <div className="flex items-center gap-3 mb-4">
          <div className="w-10 h-10 bg-gradient-to-r from-purple-500 to-pink-500 rounded-xl flex items-center justify-center shadow-lg">
            <span className="text-2xl">💡</span>
          </div>
          <h3 className="text-2xl font-bold text-white">Suggested Lists</h3>
        </div>
        <div className="flex items-center justify-center py-8">
          <div className="flex items-center gap-3">
            <div className="w-6 h-6 border-2 border-purple-400 border-t-transparent rounded-full animate-spin"></div>
            <span className="text-white/70">Analyzing your preferences...</span>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="bg-white/10 backdrop-blur-lg p-6 rounded-3xl shadow-2xl border border-white/20">
      <div className="flex items-center gap-3 mb-6">
        <div className="w-10 h-10 bg-gradient-to-r from-purple-500 to-pink-500 rounded-xl flex items-center justify-center shadow-lg">
          <span className="text-2xl">💡</span>
        </div>
          <h3 className="text-2xl font-bold text-white">Suggested Lists</h3>
      </div>

      {error && (
        <div className="mb-4 p-3 bg-red-500/20 border border-red-400/30 rounded-xl text-red-200 text-sm backdrop-blur-sm">
          {error}
        </div>
      )}

      {suggestions.length === 0 ? (
        <div className="text-center py-8">
          <div className="text-white/50 mb-4">
            <span className="text-4xl">📋</span>
          </div>
          <p className="text-white/70 mb-4">No suggestions available at the moment.</p>
          <button 
            onClick={loadSuggestions}
            className="min-h-[44px] px-6 py-3 bg-gradient-to-r from-purple-500 to-pink-500 text-white rounded-xl hover:shadow-xl transition-all hover:scale-105"
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
              <div key={i} className={`bg-white/5 backdrop-blur-sm rounded-2xl border border-white/20 p-4 hover:bg-white/10 hover:shadow-lg transition-all relative ${suggestion._removing ? "opacity-60" : ""}`}
                aria-live="polite" aria-busy={isRemoving}>
                <div className="flex items-start gap-3 mb-3">
                  <div className={`w-10 h-10 bg-gradient-to-r ${getColorClasses(suggestion.color)} rounded-xl flex items-center justify-center text-white text-lg shadow-lg`}>
                    {suggestion.icon}
                  </div>
                  <div className="flex-1">
                    <h4 className="font-semibold text-white text-lg mb-1">{suggestion.title}</h4>
                    <p className="text-xs text-white/60 leading-relaxed">{suggestion.description}</p>
                  </div>
                  <button
                    aria-label="Remove and regenerate suggestion"
                    title="Remove and regenerate suggestion"
                    onClick={() => removeSuggestion(suggestion, i)}
                    disabled={isRemoving}
                    className={`ml-2 min-h-[36px] min-w-[36px] px-2 py-1 text-sm rounded-xl border border-white/20 bg-white/5 hover:bg-red-500/20 hover:border-red-400/30 text-white/70 hover:text-red-300 transition ${isRemoving ? "opacity-60 cursor-not-allowed" : ""}`}
                  >
                    {isRemoving ? (
                      <span className="inline-flex items-center gap-1"><span className="w-3 h-3 border border-red-400 border-t-transparent rounded-full animate-spin"></span></span>
                    ) : (
                      <span>✕</span>
                    )}
                  </button>
                </div>
                <div className="flex items-center justify-between mt-4">
                  <div className="text-xs text-white/50">
                    🎬 {suggestion.item_limit} items
                  </div>
                  <button 
                    aria-label="Create list from suggestion"
                    onClick={() => createList(suggestion, i)}
                    disabled={isCreating}
                    className={`min-h-[44px] px-5 py-2 rounded-xl text-sm font-semibold transition-all ${
                      isCreating
                        ? "bg-white/10 text-white/40 cursor-not-allowed"
                        : `bg-gradient-to-r ${getColorClasses(suggestion.color)} text-white shadow-lg hover:shadow-xl transform hover:scale-105 active:scale-95`
                    }`}
                  >
                    {isCreating ? (
                      <div className="flex items-center gap-2">
                        <div className="w-3 h-3 border border-white border-t-transparent rounded-full animate-spin"></div>
                        Creating...
                      </div>
                    ) : (
                      "✨ Create List"
                    )}
                  </button>
                </div>
                {/* Confirmation dialog */}
                {confirmIdx === i && (
                  <div className="absolute inset-0 bg-black/50 backdrop-blur-sm flex items-center justify-center z-10 rounded-2xl">
                    <div className="bg-white/10 backdrop-blur-lg border border-white/30 rounded-2xl p-5 shadow-2xl flex flex-col items-center">
                      <p className="mb-4 text-white text-sm font-medium">Remove this suggestion and get a new one?</p>
                      <div className="flex gap-3">
                        <button
                          className="min-h-[44px] px-5 py-2 rounded-xl bg-gradient-to-r from-red-500 to-red-600 text-white text-sm font-semibold hover:shadow-lg transition hover:scale-105"
                          onClick={() => confirmRemove(suggestion, i)}
                          autoFocus
                        >Yes, remove</button>
                        <button
                          className="min-h-[44px] px-5 py-2 rounded-xl bg-white/10 border border-white/20 text-white text-sm font-semibold hover:bg-white/20 transition"
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

      <div className="mt-6 p-4 bg-purple-500/10 backdrop-blur-sm rounded-2xl border border-purple-400/20">
        <p className="text-xs text-purple-200">
          <strong className="text-purple-100">💫 Personalized for you:</strong> These suggestions are based on your viewing history, 
          ratings, and preferences. Lists automatically update as your tastes evolve.
        </p>
      </div>
    </div>
  );
}
