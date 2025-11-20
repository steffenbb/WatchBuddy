import React, { useEffect, useMemo, useState } from "react";
import { addItemsToIndividualList, searchIndividualList, SearchResult } from "../../api/individualLists";
import { useToast } from "../ToastProvider";

export default function SearchModal({ listId, onClose, onAdded }: { listId: number; onClose: () => void; onAdded: () => void }) {
  const [q, setQ] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [suggestions, setSuggestions] = useState<string[]>([]);
  // Controls visibility of the autocomplete dropdown so it doesn't block actions
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<Record<string, boolean>>({});
  const { addToast } = useToast();

  // Autocomplete suggestions effect (faster, 150ms debounce)
  useEffect(() => {
    let cancelled = false;
    async function fetchSuggestions() {
      if (!q.trim() || q.trim().length < 2) {
        setSuggestions([]);
        setShowSuggestions(false);
        return;
      }
      
      try {
        const res = await searchIndividualList(listId, q.trim(), 1, 5, true); // Skip fit scoring for autocomplete
        if (!cancelled && res.length > 0) {
          // Extract unique title suggestions
          const titles: string[] = [];
          for (const r of res.slice(0, 5)) {
            if (r.title && r.title.toLowerCase() !== q.trim().toLowerCase()) {
              titles.push(r.title);
            }
          }
          setSuggestions(Array.from(new Set(titles)));
          setShowSuggestions(true);
        } else if (!cancelled) {
          setSuggestions([]);
          setShowSuggestions(false);
        }
      } catch (e) {
        console.error("Autocomplete failed:", e);
      }
    }
    const timer = setTimeout(fetchSuggestions, 150);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [q, listId]);

  // Main search results effect (300ms debounce)
  useEffect(() => {
    let cancelled = false;
    async function run() {
      if (!q.trim()) { setResults([]); return; }
      setLoading(true);
      try {
        const res = await searchIndividualList(listId, q.trim(), 1, 50);
        if (!cancelled) setResults(res);
      } catch (e) {
        console.error(e);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    const t = setTimeout(run, 300);
    return () => { cancelled = true; clearTimeout(t); };
  }, [q, listId]);

  const selectedItems = useMemo(() => results.filter(r => selected[`${r.media_type}:${r.tmdb_id}`]), [results, selected]);

  async function onAdd() {
    if (selectedItems.length === 0) return;
    const items = selectedItems.map(r => ({
      tmdb_id: r.tmdb_id,
      media_type: r.media_type,
      title: r.title,
      original_title: r.original_title,
      year: r.year,
      overview: r.overview,
      poster_path: r.poster_path,
      backdrop_path: r.backdrop_path,
      genres: r.genres,
      fit_score: r.fit_score,
    }));
    try {
      const result = await addItemsToIndividualList(listId, items, 1);
      addToast({ message: `Added ${result.added} items${result.skipped > 0 ? ` (${result.skipped} already in list)` : ''}`, type: "success" });
      onAdded();
      onClose();
    } catch (e) {
      console.error(e);
      addToast({ message: "Failed to add items", type: "error" });
    }
  }

  return (
    <div className="fixed inset-0 z-50" aria-modal>
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />
      <div className="absolute inset-x-4 md:inset-x-10 lg:inset-x-20 top-10 bottom-10 bg-gradient-to-br from-indigo-900 via-purple-900 to-pink-900 rounded-3xl border border-white/20 shadow-2xl overflow-hidden flex flex-col">
        <div className="p-4 border-b border-white/20 bg-white/5">
          <div className="flex items-center gap-3">
            <div className="flex-1 relative">
              <input
                autoFocus
                placeholder="Search movies and shows..."
                className="w-full px-3 py-2 rounded-lg bg-white/10 border border-white/20 text-white placeholder:text-white/50"
                value={q}
                onChange={(e) => { setQ(e.target.value); setShowSuggestions(true); }}
                onFocus={() => setShowSuggestions(suggestions.length > 0)}
                onBlur={() => setTimeout(() => setShowSuggestions(false), 120)}
                onKeyDown={(e) => {
                  if (e.key === 'Escape' || e.key === 'Enter') {
                    setShowSuggestions(false);
                  }
                }}
              />
              {/* Autocomplete suggestions dropdown */}
              {showSuggestions && suggestions.length > 0 && (
                <div className="absolute top-full left-0 right-0 mt-1 bg-slate-900/95 backdrop-blur-lg border border-white/20 rounded-lg shadow-2xl overflow-hidden z-20 max-h-64 overflow-y-auto">
                  <div className="px-3 py-1.5 text-xs text-white/50 border-b border-white/10">
                    Did you mean...?
                  </div>
                  {suggestions.map((suggestion, idx) => (
                    <button
                      key={idx}
                      onClick={() => { setQ(suggestion); setShowSuggestions(false); }}
                      className="w-full px-3 py-2 text-left text-white hover:bg-white/10 transition-colors text-sm"
                    >
                      {suggestion}
                    </button>
                  ))}
                </div>
              )}
            </div>
            <button onMouseDown={() => setShowSuggestions(false)} onClick={onClose} className="px-3 py-2 rounded-lg bg-white/15 text-white hover:bg-white/25">Close</button>
            <button onMouseDown={() => setShowSuggestions(false)} onClick={onAdd} className="px-3 py-2 rounded-lg bg-emerald-500 hover:bg-emerald-600 text-white disabled:opacity-50" disabled={selectedItems.length===0}>Add {selectedItems.length || ""}</button>
          </div>
        </div>
        <div className="p-4 overflow-y-auto flex-1">
          {loading ? (
            <div className="text-white/80">Searching…</div>
          ) : results.length === 0 ? (
            <div className="text-white/60">Type to search</div>
          ) : (
            <div className="grid sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3">
              {results.map(r => {
                const key = `${r.media_type}:${r.tmdb_id}`;
                const isSel = !!selected[key];
                const handleItemClick = () => {
                  if (r.tmdb_id) {
                    window.location.hash = `item/${r.media_type}/${r.tmdb_id}`;
                  }
                };
                return (
                  <div key={key} className="relative">
                    <label className={`flex gap-3 p-3 rounded-xl border ${isSel? 'border-emerald-400 bg-emerald-400/10':'border-white/20 bg-white/5'} cursor-pointer hover:bg-white/10 hover:ring-2 hover:ring-purple-500 transition`}>
                      <input type="checkbox" checked={isSel} onChange={(e)=>setSelected(prev=>({...prev,[key]: e.target.checked}))} onClick={(e) => e.stopPropagation()} />
                      {r.poster_path ? (
                        <img
                          src={`https://image.tmdb.org/t/p/w154${r.poster_path}`}
                          alt={r.title}
                          className="w-12 h-18 object-cover rounded-md border border-white/20"
                          loading="lazy"
                          onClick={handleItemClick}
                        />
                      ) : (
                        <div className="w-12 h-18 rounded-md bg-white/10 border border-white/20 flex items-center justify-center text-white/60 text-[10px]">
                          No image
                        </div>
                      )}
                      <div className="flex-1 min-w-0">
                        <div className="text-white font-semibold truncate cursor-pointer hover:text-purple-300 transition-colors" onClick={handleItemClick}>{r.title}</div>
                        <div className="text-white/70 text-xs truncate">{r.media_type} · {r.year || '—'}</div>
                        <div className="text-white/60 text-xs mt-1 line-clamp-2">{r.overview || ''}</div>
                        {(r.fit_score != null || r.relevance_score != null) && (
                          <div className="text-xs text-white/70 mt-1 flex gap-2">
                            {r.fit_score != null && <span className="px-2 py-0.5 bg-emerald-500/30 rounded-full">fit {(r.fit_score*100).toFixed(0)}%</span>}
                            {r.relevance_score != null && <span className="px-2 py-0.5 bg-indigo-500/30 rounded-full">rel {(r.relevance_score*100).toFixed(0)}%</span>}
                          </div>
                        )}
                      </div>
                    </label>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
