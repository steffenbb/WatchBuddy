import React, { useEffect, useState } from "react";
import { addItemsToIndividualList, Suggestion, suggestionsForIndividualList } from "../../api/individualLists";
import { useToast } from "../ToastProvider";

export default function SuggestionsSidebar({ listId, onAdded }: { listId: number; onAdded: () => void }) {
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [loading, setLoading] = useState(false);
  const { addToast } = useToast();

  async function load() {
    setLoading(true);
    try {
      const res = await suggestionsForIndividualList(listId, 1);
      setSuggestions(res);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, [listId]);

  async function addOne(s: Suggestion) {
    try {
      await addItemsToIndividualList(listId, [{
        tmdb_id: s.tmdb_id,
        media_type: s.media_type,
        title: s.title,
        original_title: s.original_title,
        year: s.year,
        overview: s.overview,
        poster_path: s.poster_path,
        backdrop_path: s.backdrop_path,
        genres: s.genres,
        fit_score: s.fit_score,
      }], 1);
      addToast({ message: `Added ${s.title}`, type: "success" });
      onAdded();
      await load();
    } catch (e) {
      console.error(e);
      addToast({ message: "Failed to add suggestion", type: "error" });
    }
  }

  return (
    <div className="bg-white/10 border border-white/20 rounded-2xl p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-white font-semibold">Suggestions</h3>
        <button onClick={load} className="text-white/70 hover:text-white text-sm">↻</button>
      </div>
      {loading ? (
        <div className="text-white/80">Loading…</div>
      ) : suggestions.length === 0 ? (
        <div className="text-white/60 text-sm">No suggestions yet.</div>
      ) : (
        <div className="space-y-2 max-h-[70vh] overflow-y-auto pr-1">
          {suggestions.map((s) => {
            const handleItemClick = () => {
              if (s.tmdb_id) {
                window.location.hash = `item/${s.media_type}/${s.tmdb_id}`;
              }
            };
            return (
              <div key={`${s.media_type}:${s.tmdb_id}`} className="flex gap-2 items-start p-2 rounded-lg bg-white/5 border border-white/10 hover:ring-2 hover:ring-purple-500 transition cursor-pointer min-w-0 overflow-hidden" onClick={handleItemClick}>
                {/* Poster thumbnail */}
                {s.poster_path ? (
                  <img
                    src={`https://image.tmdb.org/t/p/w92${s.poster_path}`}
                    alt={s.title}
                    className="w-[46px] h-[69px] rounded-md object-cover flex-shrink-0 border border-white/10"
                    loading="lazy"
                  />
                ) : (
                  <div className="w-[46px] h-[69px] rounded-md bg-white/10 border border-white/10 flex items-center justify-center text-white/30 text-xs flex-shrink-0 text-center px-1">No image</div>
                )}
                <div className="flex-1 min-w-0 overflow-hidden">
                  <div className="text-white text-sm font-semibold break-words line-clamp-2 hover:text-purple-300 transition-colors">{s.title}</div>
                  <div className="text-white/70 text-xs truncate">{s.media_type} · {s.year || '—'}</div>
                  <div className="text-xs text-white/70 mt-1 flex gap-1 flex-wrap overflow-hidden">
                    {s.is_high_fit && <span className="px-2 py-0.5 bg-emerald-500/30 rounded-full whitespace-nowrap">high fit</span>}
                    {s.fit_score != null && <span className="px-2 py-0.5 bg-emerald-500/20 rounded-full whitespace-nowrap">fit {(s.fit_score*100).toFixed(0)}%</span>}
                    {s.similarity_score != null && <span className="px-2 py-0.5 bg-indigo-500/20 rounded-full whitespace-nowrap">sim {(s.similarity_score*100).toFixed(0)}%</span>}
                  </div>
                </div>
                <button onClick={(e) => { e.stopPropagation(); addOne(s); }} className="px-2 py-1 rounded-md bg-white/15 text-white hover:bg-white/25 text-xs flex-shrink-0 whitespace-nowrap">Add</button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
