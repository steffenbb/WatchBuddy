import React from "react";
import { api } from "../hooks/useApi";
import { useTraktAccount } from "../hooks/useTraktAccount";
import { formatLocalDate } from "../utils/date";
import HoverInfoCard from "./HoverInfoCard";

type Item = {
  id: number;
  trakt_id: number;
  media_type: string;
  score: number;
  is_watched: boolean;
  watched_at: string | null;
  added_at: string;
  explanation?: string;
  title?: string | null;
  poster_url?: string | null;
};

export default function ListDetails({ listId, title, onBack }: { listId: number; title: string; onBack: ()=>void }){
  const [items, setItems] = React.useState<Item[]>([]);
  const [loading, setLoading] = React.useState<boolean>(true);
  const [error, setError] = React.useState<string>("");
  const [includeWatched, setIncludeWatched] = React.useState<boolean>(true);
  const [sortBy, setSortBy] = React.useState<'score'|'added_at'|'watched_at'>('score');
  const [order, setOrder] = React.useState<'asc'|'desc'>('desc');
  const [page, setPage] = React.useState<number>(1);
  const [limit, setLimit] = React.useState<number>(25);
  const [total, setTotal] = React.useState<number>(0);
  const { account } = useTraktAccount();
  const [showEdit, setShowEdit] = React.useState<boolean>(false);
  const [saving, setSaving] = React.useState<boolean>(false);
  const [userRatings, setUserRatings] = React.useState<Record<number, number>>({});
  const [listMeta, setListMeta] = React.useState<any | null>(null);

  const load = React.useCallback(async () => {
    try{
      setLoading(true); setError("");
  const res = await api.get(`/lists/${listId}/items/`, {
        params: { include_watched: includeWatched, sort_by: sortBy, order, page, limit, user_id: 1 }
      });
      setItems(res.data.items || []);
      setTotal(res.data.total || 0);
    } catch(e:any){
      setError(e?.response?.data?.detail || e.message || 'Failed to load items');
    } finally {
      setLoading(false);
    }
  }, [listId, includeWatched, sortBy, order, page, limit]);

  React.useEffect(()=>{ load(); }, [load]);

  async function loadListMeta(){
    try{
      const resp = await api.get('/lists/');
      const lists = Array.isArray(resp.data) ? resp.data : (resp.data?.lists || []);
      const l = lists.find((x:any)=> x.id === listId);
      if (l){
        let filters:any = {};
        try{ filters = l.filters ? JSON.parse(l.filters) : {}; }catch{ filters = {}; }
        setListMeta({
          id: l.id,
          list_type: l.list_type,
          title: l.title,
          exclude_watched: !!l.exclude_watched,
          item_limit: l.item_limit || 20,
          sync_interval: l.sync_interval || undefined,
          full_sync_days: (filters.full_sync_days || 1),
          discovery: filters.discovery || 'balanced',
          fusion_mode: !!filters.fusion_mode,
          media_types: Array.isArray(filters.media_types) ? filters.media_types : ['movies','shows']
        });
      }
    }catch{}
  }

  const parseExplanation = (s?: string) => {
    if(!s) return null;
    try{ return JSON.parse(s); }catch{ return null; }
  };

  const compLabel = (k: string) => {
    const map: Record<string,string> = {
      genre_overlap: 'Genre', semantic_sim: 'Semantic', mood_score: 'Mood', rating_norm: 'Rating', novelty: 'Novelty', popularity_norm: 'Popularity'
    };
    return map[k] || k;
  };

  const compColor = (k: string) => {
    const map: Record<string,string> = {
      genre_overlap: 'bg-purple-100 text-purple-800 border-purple-200',
      semantic_sim: 'bg-indigo-100 text-indigo-800 border-indigo-200',
      mood_score: 'bg-pink-100 text-pink-800 border-pink-200',
      rating_norm: 'bg-green-100 text-green-800 border-green-200',
      novelty: 'bg-amber-100 text-amber-800 border-amber-200',
      popularity_norm: 'bg-blue-100 text-blue-800 border-blue-200'
    };
    return map[k] || 'bg-gray-100 text-gray-800 border-gray-200';
  };

  const handleRating = async (traktId: number, mediaType: string, rating: number) => {
    try {
      const currentRating = userRatings[traktId] || 0;
      const newRating = currentRating === rating ? 0 : rating; // Toggle off if same rating
      
      const response = await api.post('/ratings/rate', {
        trakt_id: traktId,
        media_type: mediaType,
        rating: newRating
      });
      
      console.log('Rating response:', response.data); // Debug log
      
      setUserRatings(prev => ({
        ...prev,
        [traktId]: newRating
      }));
      
      // Show success feedback
      if (newRating === 0) {
        console.log('Rating removed');
      } else {
        console.log(`Rated ${newRating === 1 ? 'thumbs up' : 'thumbs down'}`);
      }
    } catch (error: any) {
      console.error('Error rating item:', error);
      // Show error to user (could add toast notification here)
    }
  };

  const loadUserRatings = React.useCallback(async () => {
    try {
      const response = await api.get('/ratings/user/1');
      const ratingsMap: Record<number, number> = {};
      
      response.data.ratings.forEach((rating: any) => {
        ratingsMap[rating.trakt_id] = rating.rating;
      });
      
      setUserRatings(ratingsMap);
    } catch (error) {
      console.error('Error loading user ratings:', error);
    }
  }, []);

  React.useEffect(() => {
    loadUserRatings();
  }, [loadUserRatings]);

  return (
    <div className="w-full max-w-7xl mx-auto p-4 md:p-6">
      {/* Header with glassmorphic styling */}
      <div className="bg-white/10 backdrop-blur-lg border border-white/20 rounded-2xl shadow-lg p-4 md:p-6 mb-6">
        <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 mb-4">
          <div className="flex items-center gap-3">
            <button 
              onClick={onBack} 
              className="px-4 py-3 rounded-xl border border-white/30 bg-white/10 backdrop-blur-sm text-white hover:bg-white/15 transition-all min-h-[44px] font-medium"
            >
              ← Back
            </button>
            <h3 className="text-2xl font-bold text-white truncate">{title}</h3>
          </div>
          <div className="flex items-center gap-2">
            <button 
              onClick={load} 
              className="px-4 py-3 rounded-xl bg-gradient-to-r from-indigo-500 to-purple-500 hover:from-indigo-600 hover:to-purple-600 text-white transition-all min-h-[44px] font-medium shadow-lg"
            >
              Refresh
            </button>
            <button 
              onClick={async()=>{ await loadListMeta(); setShowEdit(true); }} 
              className="px-4 py-3 rounded-xl bg-white/10 backdrop-blur-sm border border-white/30 text-white hover:bg-white/15 transition-all min-h-[44px] font-medium"
            >
              Edit
            </button>
          </div>
        </div>

        {/* Controls row */}
        <div className="flex flex-wrap items-center gap-3">
          <label className="text-sm text-white/90 flex items-center gap-2 bg-white/10 backdrop-blur-sm px-4 py-3 rounded-xl border border-white/20 min-h-[44px]">
            <input 
              type="checkbox" 
              checked={includeWatched} 
              onChange={(e)=>setIncludeWatched(e.target.checked)} 
              className="w-4 h-4 rounded border-white/30 text-purple-500"
            />
            <span className="font-medium">Include watched</span>
          </label>
          <select 
            value={sortBy} 
            onChange={(e)=>setSortBy(e.target.value as any)} 
            className="px-4 py-3 bg-white/10 backdrop-blur-sm text-white rounded-xl border border-white/20 focus:outline-none focus:ring-2 focus:ring-purple-400 min-h-[44px]"
          >
            <option value="score">Sort: Score</option>
            <option value="added_at">Sort: Added</option>
            <option value="watched_at">Sort: Watched</option>
          </select>
          <select 
            value={order} 
            onChange={(e)=>setOrder(e.target.value as any)} 
            className="px-4 py-3 bg-white/10 backdrop-blur-sm text-white rounded-xl border border-white/20 focus:outline-none focus:ring-2 focus:ring-purple-400 min-h-[44px]"
          >
            <option value="desc">▼ Desc</option>
            <option value="asc">▲ Asc</option>
          </select>
          <select 
            value={limit} 
            onChange={(e)=>{ setPage(1); setLimit(Number(e.target.value)); }} 
            className="px-4 py-3 bg-white/10 backdrop-blur-sm text-white rounded-xl border border-white/20 focus:outline-none focus:ring-2 focus:ring-purple-400 min-h-[44px]"
          >
            {[10,25,50,100].map(n => <option key={n} value={n}>{n}/page</option>)}
          </select>
        </div>
      </div>

      {showEdit && listMeta && (
        <EditPanel
          meta={listMeta}
          account={account}
          onClose={()=> setShowEdit(false)}
          onSave={async (vals:any)=>{
            setSaving(true);
            try{
              const payload:any = {};
              if (vals.title !== undefined) payload.title = vals.title;
              if (vals.exclude_watched !== undefined) payload.exclude_watched = vals.exclude_watched;
              if (vals.item_limit !== undefined) payload.item_limit = vals.item_limit;
              if (vals.sync_interval !== undefined) payload.sync_interval = vals.sync_interval;
              if (vals.full_sync_days !== undefined) payload.full_sync_days = vals.full_sync_days;
              // Custom/Suggested list filters
              if (vals.genres !== undefined) payload.genres = vals.genres;
              if (vals.genre_mode !== undefined) payload.genre_mode = vals.genre_mode;
              if (vals.languages !== undefined) payload.languages = vals.languages;
              if (vals.year_from !== undefined) payload.year_from = vals.year_from;
              if (vals.year_to !== undefined) payload.year_to = vals.year_to;
              if (vals.min_rating !== undefined) payload.min_rating = vals.min_rating;
              await api.patch(`/lists/${listId}`, payload);
              await api.post(`/lists/${listId}/sync?user_id=1&force_full=true`);
              await load();
              setShowEdit(false);
            }catch(e){ console.error('Failed to save edits', e); }
            finally{ setSaving(false); }
          }}
          saving={saving}
        />
      )}

      {/* Pagination */}
      <div className="flex items-center justify-between mb-4 text-white/80">
        <div className="text-sm">
          {total > 0 ? `Showing ${Math.min((page-1)*limit+1, total)}–${Math.min(page*limit, total)} of ${total}` : 'No items'}
        </div>
        <div className="flex items-center gap-2">
          <button 
            disabled={page<=1} 
            onClick={()=>setPage(p=>Math.max(1, p-1))} 
            className={`px-4 py-3 rounded-xl text-sm font-medium min-h-[44px] ${
              page<=1 
                ? 'bg-white/5 text-white/40 border border-white/10 cursor-not-allowed' 
                : 'bg-white/10 backdrop-blur-sm text-white border border-white/20 hover:bg-white/15'
            }`}
          >
            Prev
          </button>
          <div className="text-sm font-medium">Page {page}</div>
          <button 
            disabled={page*limit >= total} 
            onClick={()=>setPage(p=>p+1)} 
            className={`px-4 py-3 rounded-xl text-sm font-medium min-h-[44px] ${
              page*limit >= total 
                ? 'bg-white/5 text-white/40 border border-white/10 cursor-not-allowed' 
                : 'bg-white/10 backdrop-blur-sm text-white border border-white/20 hover:bg-white/15'
            }`}
          >
            Next
          </button>
        </div>
      </div>

      {loading ? (
        <div className="flex items-center gap-3 py-12 justify-center text-white">
          <div className="w-6 h-6 border-2 border-white/60 border-t-transparent rounded-full animate-spin"/>
          <span>Loading…</span>
        </div>
      ) : error ? (
        <div className="p-4 bg-red-500/20 text-red-200 border border-red-400/30 rounded-xl">{error}</div>
      ) : items.length === 0 ? (
        <div className="text-center py-12">
          <div className="text-6xl mb-4">🎬</div>
          <h3 className="text-2xl font-bold text-white mb-2">No items found</h3>
          <p className="text-white/60">Try adjusting your filters or sync the list</p>
        </div>
      ) : (
        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-4">
          {items.map(it => {
            const comps = parseExplanation(it.explanation)?.components || parseExplanation(it.explanation) || {};
            const entries = Object.entries(comps).filter(([,v])=> typeof v === 'number' && v > 0).sort((a:any,b:any)=> b[1]-a[1]).slice(0,3);
            return (
              <HoverInfoCard
                key={it.id}
                traktId={it.trakt_id}
                mediaType={it.media_type}
                fallbackInfo={{
                  title: it.title,
                  media_type: it.media_type
                }}
              >
                <div className="group bg-white/10 backdrop-blur-lg border border-white/20 rounded-xl shadow-lg hover:bg-white/15 hover:shadow-2xl transition-all duration-300 overflow-hidden">
                  {/* Poster */}
                  <div className="relative aspect-[2/3] bg-gradient-to-br from-indigo-900/50 to-purple-900/50 overflow-hidden">
                    {it.poster_url ? (
                      <img 
                        src={it.poster_url} 
                        alt={it.title || ''} 
                        className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300" 
                        loading="lazy" 
                      />
                    ) : (
                      <div className="w-full h-full flex items-center justify-center text-4xl text-white/30">
                        {it.media_type === 'movie' ? '🎬' : '📺'}
                      </div>
                    )}
                    {/* Overlay with score */}
                    <div className="absolute top-2 right-2 bg-black/70 backdrop-blur-sm px-2 py-1 rounded-lg">
                      <span className="text-xs font-bold text-white">{it.score?.toFixed(2)}</span>
                    </div>
                    {/* Watched badge */}
                    {it.is_watched && (
                      <div className="absolute top-2 left-2 bg-emerald-500/80 backdrop-blur-sm px-2 py-1 rounded-lg">
                        <span className="text-xs font-bold text-white">✓</span>
                      </div>
                    )}
                  </div>
                  
                  {/* Content */}
                  <div className="p-3">
                    <h4 className="font-semibold text-white text-sm line-clamp-2 mb-2">
                      {it.title || `#${it.trakt_id}`}
                  </h4>
                  
                  {/* Rating buttons */}
                  <div className="flex gap-2 mb-2">
                    <button 
                      onClick={() => handleRating(it.trakt_id, it.media_type, 1)}
                      className={`flex-1 py-2 rounded-lg text-sm font-medium transition-all min-h-[44px] ${
                        userRatings[it.trakt_id] === 1 
                          ? 'bg-emerald-500 text-white' 
                          : 'bg-white/10 text-white/70 hover:bg-emerald-500/30 border border-white/20'
                      }`}
                      title="Thumbs up"
                    >
                      👍
                    </button>
                    <button 
                      onClick={() => handleRating(it.trakt_id, it.media_type, -1)}
                      className={`flex-1 py-2 rounded-lg text-sm font-medium transition-all min-h-[44px] ${
                        userRatings[it.trakt_id] === -1 
                          ? 'bg-red-500 text-white' 
                          : 'bg-white/10 text-white/70 hover:bg-red-500/30 border border-white/20'
                      }`}
                      title="Thumbs down"
                    >
                      👎
                    </button>
                  </div>
                  
                  {/* Score breakdown */}
                  {entries.length > 0 && (
                    <div className="flex items-center justify-between">
                      <div className="flex flex-wrap gap-1">
                        {entries.slice(0, 2).map(([k,v])=> (
                          <span
                            key={k}
                            className="inline-flex items-center px-2 py-0.5 text-xs rounded-full bg-purple-500/20 text-purple-200 border border-purple-400/30"
                            title={`${compLabel(k)} contribution`}
                          >
                            {compLabel(k).slice(0, 3)}: {(v as number).toFixed(1)}
                          </span>
                        ))}
                      </div>
                      {/* Details popover */}
                      <div className="relative group">
                        <button className="px-2 py-1 text-xs rounded-md bg-white/10 text-white/80 hover:bg-white/20 border border-white/20" title="Why this?">Why</button>
                        <div className="absolute right-0 mt-2 hidden group-hover:block z-20 w-60 p-3 rounded-xl bg-black/80 text-white/90 border border-white/20 shadow-xl">
                          <div className="text-xs font-semibold mb-1">Why this recommendation</div>
                          <ul className="space-y-1">
                            {Object.entries(comps).filter(([,v])=> typeof v === 'number' && v>0).map(([k,v])=> (
                              <li key={k} className="flex items-center justify-between text-xs">
                                <span className="text-white/80">{compLabel(k)}</span>
                                <span className="text-white/70">{(v as number).toFixed(2)}</span>
                              </li>
                            ))}
                          </ul>
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              </div>
              </HoverInfoCard>
            );
          })}
        </div>
      )}
    </div>
  );
}

function EditPanel({ meta, account, onClose, onSave, saving }:{
  meta: any;
  account: any;
  onClose: ()=>void;
  onSave: (v:any)=>void;
  saving: boolean;
}){
  const [values, setValues] = React.useState<any>({
    ...meta,
    genre_mode: meta.genre_mode || 'any',
    genres: meta.genres || [],
    languages: meta.languages || [],
    year_from: meta.year_from || 2000,
    year_to: meta.year_to || new Date().getFullYear(),
    min_rating: meta.min_rating || 0,
  });
  const maxItems = account?.max_items_per_list ?? 100;
  const update = (k:string, v:any)=> setValues((prev:any)=> ({...prev, [k]: v }));

  return (
    <div className="mb-6 p-5 rounded-2xl border border-white/30 bg-white/10 backdrop-blur-lg shadow-lg">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <label className="text-sm text-white/90 flex flex-col gap-2">
          <span className="font-medium">Title</span>
          <input 
            className="px-4 py-3 bg-white/10 backdrop-blur-sm text-white placeholder-white/40 rounded-xl border border-white/20 focus:outline-none focus:ring-2 focus:ring-purple-400 min-h-[44px]" 
            value={values.title || ''} 
            onChange={(e)=>update('title', e.target.value)} 
          />
        </label>
        <label className="text-sm text-white/90 flex items-center gap-3 bg-white/10 backdrop-blur-sm px-4 py-3 rounded-xl border border-white/20 min-h-[44px]">
          <input 
            type="checkbox" 
            checked={!!values.exclude_watched} 
            onChange={(e)=>update('exclude_watched', e.target.checked)} 
            className="w-5 h-5 rounded border-white/30 text-purple-500"
          />
          <span className="font-medium">Exclude Watched</span>
        </label>
        <label className="text-sm text-white/90 flex flex-col gap-2">
          <span className="font-medium">Item limit</span>
          <select 
            className="px-4 py-3 bg-white/10 backdrop-blur-sm text-white rounded-xl border border-white/20 focus:outline-none focus:ring-2 focus:ring-purple-400 min-h-[44px]" 
            value={values.item_limit}
            onChange={(e)=>update('item_limit', Math.min(Number(e.target.value), maxItems))}
          >
            {[10,20,50,100,200,500].filter(n=>n<=maxItems).map(n=> (
              <option key={n} value={n}>{n} items</option>
            ))}
          </select>
        </label>
        <label className="text-sm text-white/90 flex flex-col gap-2">
          <span className="font-medium">Sync interval (hours)</span>
          <input 
            type="number" 
            min={1} 
            max={48} 
            className="px-4 py-3 bg-white/10 backdrop-blur-sm text-white placeholder-white/40 rounded-xl border border-white/20 focus:outline-none focus:ring-2 focus:ring-purple-400 min-h-[44px]" 
            value={values.sync_interval ?? ''}
            onChange={(e)=>update('sync_interval', e.target.value ? Number(e.target.value) : undefined)} 
          />
        </label>
        <label className="text-sm text-white/90 flex flex-col gap-2">
          <span className="font-medium">Full sync cadence (days)</span>
          <input 
            type="number" 
            min={1} 
            max={7} 
            className="px-4 py-3 bg-white/10 backdrop-blur-sm text-white placeholder-white/40 rounded-xl border border-white/20 focus:outline-none focus:ring-2 focus:ring-purple-400 min-h-[44px]" 
            value={values.full_sync_days}
            onChange={(e)=>update('full_sync_days', Number(e.target.value))} 
          />
        </label>
      </div>
      {/* Custom/Suggested List Filters */}
      <div className="space-y-4 mt-4">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <label className="text-sm text-white/90 flex flex-col gap-2">
            <span className="font-medium">Year From</span>
            <input 
              type="number" 
              min={1900} 
              max={new Date().getFullYear()} 
              className="px-4 py-3 bg-white/10 backdrop-blur-sm text-white placeholder-white/40 rounded-xl border border-white/20 focus:outline-none focus:ring-2 focus:ring-purple-400 transition-all min-h-[44px]" 
              value={values.year_from}
              onChange={(e)=>update('year_from', Number(e.target.value))} 
            />
          </label>
          <label className="text-sm text-white/90 flex flex-col gap-2">
            <span className="font-medium">Year To</span>
            <input 
              type="number" 
              min={1900} 
              max={new Date().getFullYear()} 
              className="px-4 py-3 bg-white/10 backdrop-blur-sm text-white placeholder-white/40 rounded-xl border border-white/20 focus:outline-none focus:ring-2 focus:ring-purple-400 transition-all min-h-[44px]" 
              value={values.year_to}
              onChange={(e)=>update('year_to', Number(e.target.value))} 
            />
          </label>
          <label className="text-sm text-white/90 flex flex-col gap-2">
            <span className="font-medium">Minimum Rating (0-10)</span>
            <input 
              type="number" 
              min={0} 
              max={10} 
              step={0.1}
              className="px-4 py-3 bg-white/10 backdrop-blur-sm text-white placeholder-white/40 rounded-xl border border-white/20 focus:outline-none focus:ring-2 focus:ring-purple-400 transition-all min-h-[44px]" 
              value={values.min_rating}
              onChange={(e)=>update('min_rating', Number(e.target.value))} 
            />
          </label>
          <label className="text-sm text-white/90 flex flex-col gap-2">
            <span className="font-medium">Genre Mode</span>
            <select 
              className="px-4 py-3 bg-white/10 backdrop-blur-sm text-white rounded-xl border border-white/20 focus:outline-none focus:ring-2 focus:ring-purple-400 transition-all min-h-[44px]" 
              value={values.genre_mode} 
              onChange={(e)=>update('genre_mode', e.target.value)}
            >
              <option value="any">Any Genre (OR)</option>
              <option value="all">All Genres (AND)</option>
            </select>
          </label>
        </div>
        <div className="text-sm text-white/90">
          <div className="mb-2 font-medium">Genres (select multiple)</div>
          <div className="flex flex-wrap gap-2">
            {['action','comedy','drama','sci-fi','romance','mystery','thriller','horror','documentary','animation','fantasy','adventure'].map(g => (
              <button
                key={g}
                type="button"
                onClick={()=>{
                  const cur = new Set(values.genres || []);
                  if (cur.has(g)) cur.delete(g); else cur.add(g);
                  update('genres', Array.from(cur));
                }}
                className={`px-3 py-2 rounded-lg border transition-all text-sm ${
                  values.genres?.includes(g)
                    ? 'bg-indigo-500 text-white border-indigo-500'
                    : 'bg-white/10 text-white border-white/20 hover:bg-white/15'
                }`}
              >
                {g}
              </button>
            ))}
          </div>
        </div>
        <div className="text-sm text-white/90">
          <div className="mb-2 font-medium">Languages (select multiple)</div>
          <div className="flex flex-wrap gap-2">
            {['en','da','sv','no','es','fr','de','it','ja','ko','zh'].map(lang => (
              <button
                key={lang}
                type="button"
                onClick={()=>{
                  const cur = new Set(values.languages || []);
                  if (cur.has(lang)) cur.delete(lang); else cur.add(lang);
                  update('languages', Array.from(cur));
                }}
                className={`px-3 py-2 rounded-lg border transition-all text-sm uppercase ${
                  values.languages?.includes(lang)
                    ? 'bg-purple-500 text-white border-purple-500'
                    : 'bg-white/10 text-white border-white/20 hover:bg-white/15'
                }`}
              >
                {lang}
              </button>
            ))}
          </div>
        </div>
      </div>
      <div className="flex gap-3 justify-end mt-5 pt-4 border-t border-white/20">
        <button 
          onClick={onClose} 
          className="px-6 py-3 rounded-xl border border-white/20 bg-white/10 backdrop-blur-sm text-white hover:bg-white/15 transition-all min-h-[44px] font-medium"
        >
          Cancel
        </button>
        <button 
          onClick={()=>onSave(values)} 
          disabled={!!saving} 
          className={`px-6 py-3 rounded-xl font-medium min-h-[44px] transition-all ${
            saving 
              ? 'bg-white/5 text-white/40 cursor-not-allowed' 
              : 'bg-gradient-to-r from-purple-500 to-pink-500 hover:from-purple-600 hover:to-pink-600 text-white shadow-lg'
          }`}
        >
          {saving ? 'Saving…' : 'Save & Full Sync'}
        </button>
      </div>
    </div>
  );
}
