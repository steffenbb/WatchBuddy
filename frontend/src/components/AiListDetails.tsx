import React, { useEffect, useState } from 'react';
import { listAiListItems } from '../api/aiLists';

interface AiListItem {
  tmdb_id: number | null;
  trakt_id: number | null;
  rank: number;
  score: number;
  title?: string | null;
  media_type?: string | null;
  poster_url?: string | null;
  explanation_text?: string | null;
  explanation_meta?: Record<string, any> | null;
}

export default function AiListDetails({ aiListId, title, onClose }: { aiListId: string; title: string; onClose: ()=>void }){
  const [items, setItems] = useState<AiListItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    async function load(){
      try{
        setLoading(true); setError(null);
        const data = await listAiListItems(aiListId);
        if (mounted) setItems(data || []);
      }catch(e:any){
        setError(e?.message || 'Failed to load AI list items');
      }finally{
        setLoading(false);
      }
    }
    load();
    return () => { mounted = false; };
  }, [aiListId]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/60" onClick={onClose} />
      <div className="relative w-full max-w-5xl bg-white/10 backdrop-blur-xl border border-white/20 rounded-2xl shadow-2xl p-4 md:p-6 overflow-hidden">
        <div className="flex items-center justify-between mb-4 gap-3">
          <h3 className="text-xl md:text-2xl font-bold text-white truncate overflow-hidden text-ellipsis whitespace-nowrap flex-shrink min-w-0">{title}</h3>
          <button onClick={onClose} className="px-3 py-2 bg-white/10 hover:bg-white/20 text-white rounded-lg border border-white/20 flex-shrink-0">Close</button>
        </div>

        {error && (
          <div className="mb-4 text-red-200 bg-red-500/20 border border-red-400/30 rounded-xl p-3">{error}</div>
        )}

        {loading ? (
          <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-4">
            {Array.from({ length: 8 }).map((_, i) => (
              <div key={i} className="h-48 bg-white/5 rounded-xl animate-pulse" />
            ))}
          </div>
        ) : items.length === 0 ? (
          <div className="text-white/70">No items yet.</div>
        ) : (
          <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-4 max-h-[70vh] overflow-auto pr-1">
            {items.map((it) => (
              <div key={`${it.tmdb_id}-${it.rank}`} className="bg-white/5 border border-white/10 rounded-xl overflow-hidden hover:bg-white/10 transition">
                {it.poster_url ? (
                  <img src={it.poster_url} alt={it.title || ''} className="w-full h-48 object-cover" />
                ) : (
                  <div className="w-full h-48 bg-white/5 flex items-center justify-center text-white/50">No poster</div>
                )}
                <div className="p-3">
                  <div className="text-white font-semibold truncate" title={it.title || undefined}>{it.title || `#${it.tmdb_id || it.trakt_id}`}</div>
                  <div className="text-white/60 text-xs mt-1 flex items-center gap-2">
                    <span className="px-2 py-0.5 bg-indigo-500/30 rounded-full">{it.media_type || 'item'}</span>
                    <span className="px-2 py-0.5 bg-purple-500/30 rounded-full">#{it.rank}</span>
                    <span className="px-2 py-0.5 bg-emerald-500/30 rounded-full">{(it.score ?? 0).toFixed(2)}</span>
                  </div>
                  {it.explanation_text && (
                    <div className="text-white/70 text-xs mt-2 line-clamp-2">{it.explanation_text}</div>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
