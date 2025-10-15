import React from "react";
import { api } from "../hooks/useApi";
import { useTraktAccount } from "../hooks/useTraktAccount";
import { formatLocalDate } from "../utils/date";

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
    <div className="relative z-10 bg-white/90 backdrop-blur-xl rounded-3xl shadow-2xl border border-indigo-100 w-full max-w-2xl mx-auto p-6 md:p-8 flex flex-col gap-4 transition-all duration-500">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3">
          <button onClick={onBack} className="px-2 py-1 text-sm rounded border border-gray-300 bg-white hover:bg-gray-50">← Back</button>
          <h3 className="text-xl font-bold text-gray-800 truncate">{title}</h3>
        </div>
        <div className="flex items-center gap-2">
          <label className="text-sm text-gray-700 flex items-center gap-2">
            <input type="checkbox" checked={includeWatched} onChange={(e)=>setIncludeWatched(e.target.checked)} />
            Include watched
          </label>
          <select value={sortBy} onChange={(e)=>setSortBy(e.target.value as any)} className="text-sm px-2 py-1 rounded border bg-white">
            <option value="score">Score</option>
            <option value="added_at">Added</option>
            <option value="watched_at">Watched</option>
          </select>
          <select value={order} onChange={(e)=>setOrder(e.target.value as any)} className="text-sm px-2 py-1 rounded border bg-white">
            <option value="desc">Desc</option>
            <option value="asc">Asc</option>
          </select>
          <select value={limit} onChange={(e)=>{ setPage(1); setLimit(Number(e.target.value)); }} className="text-sm px-2 py-1 rounded border bg-white">
            {[10,25,50,100].map(n => <option key={n} value={n}>{n}/page</option>)}
          </select>
          <button onClick={load} className="text-sm px-3 py-1 rounded bg-indigo-600 text-white hover:bg-indigo-700">Refresh</button>
          <button onClick={async()=>{ await loadListMeta(); setShowEdit(true); }} className="text-sm px-3 py-1 rounded bg-gray-600 text-white hover:bg-gray-700">Edit</button>
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
              if ((listMeta.list_type === 'smartlist') && vals.discovery !== undefined) payload.discovery = vals.discovery;
              if ((listMeta.list_type === 'smartlist') && vals.fusion_mode !== undefined) payload.fusion_mode = vals.fusion_mode;
              if ((listMeta.list_type === 'smartlist') && vals.media_types !== undefined) payload.media_types = vals.media_types;
              if ((listMeta.list_type === 'smartlist') && vals.genre_mode !== undefined) payload.genre_mode = vals.genre_mode;
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

      <div className="flex items-center justify-between mb-3 text-xs text-gray-600">
        <div>
          {total > 0 ? `Showing ${Math.min((page-1)*limit+1, total)}–${Math.min(page*limit, total)} of ${total}` : 'No items'}
        </div>
        <div className="flex items-center gap-2">
          <button disabled={page<=1} onClick={()=>setPage(p=>Math.max(1, p-1))} className={`px-2 py-1 rounded border text-xs ${page<=1 ? 'bg-gray-100 text-gray-400 border-gray-200 cursor-not-allowed' : 'bg-white hover:bg-gray-50'}`}>Prev</button>
          <div>Page {page}</div>
          <button disabled={page*limit >= total} onClick={()=>setPage(p=>p+1)} className={`px-2 py-1 rounded border text-xs ${page*limit >= total ? 'bg-gray-100 text-gray-400 border-gray-200 cursor-not-allowed' : 'bg-white hover:bg-gray-50'}`}>Next</button>
        </div>
      </div>

      {loading ? (
        <div className="flex items-center gap-3 py-12 justify-center text-gray-600"><div className="w-5 h-5 border-2 border-indigo-600 border-t-transparent rounded-full animate-spin"/> Loading…</div>
      ) : error ? (
        <div className="p-3 bg-red-100 text-red-800 border border-red-200 rounded text-sm">{error}</div>
      ) : items.length === 0 ? (
        <div className="text-gray-600 text-sm">No items</div>
      ) : (
        <div className="space-y-3">
          {items.map(it => {
            const comps = parseExplanation(it.explanation)?.components || parseExplanation(it.explanation) || {};
            // Pick top 3 component scores
            const entries = Object.entries(comps).filter(([,v])=> typeof v === 'number' && v > 0).sort((a:any,b:any)=> b[1]-a[1]).slice(0,3);
            return (
              <div key={it.id} className="bg-white border border-gray-200 rounded-lg p-4 hover:shadow-sm transition-shadow">
                <div className="flex items-center justify-between">
                  <div className="flex items-start gap-3 min-w-0">
                    {it.poster_url ? (
                      <img src={it.poster_url} alt={it.title || ''} className="w-12 h-18 object-cover rounded shadow-sm" loading="lazy" />
                    ) : null}
                    <div className="min-w-0">
                    <div className="font-semibold text-gray-900 truncate">{it.title || `#${it.trakt_id}`}</div>
                    <div className="text-xs text-gray-500 mt-1 flex items-center gap-1">
                      {it.media_type} • 
                      <span 
                        className="cursor-help underline decoration-dotted"
                        title="Match Score: How well this item matches your preferences. Higher scores (0.0-1.0) indicate better matches based on factors like genre preferences, ratings, popularity, and your viewing history."
                      >
                        score {it.score?.toFixed(2)}
                      </span>
                      <span className="text-gray-400">ℹ️</span>
                    </div>
                    {it.explanation && !parseExplanation(it.explanation) && (
                      <div className="text-xs text-gray-600 mt-2 italic bg-gray-50 p-2 rounded border">
                        💡 {it.explanation}
                      </div>
                    )}
                    </div>
                  </div>
                  <div className="flex flex-col gap-2 items-end">
                    {it.is_watched ? (
                          <span className="inline-flex items-center px-2 py-1 text-xs rounded-full bg-green-100 text-green-700 border border-green-200">Watched{it.watched_at ? ` • ${formatLocalDate(it.watched_at, { dateStyle: 'medium', timeStyle: 'short' })}` : ''}</span>
                    ) : (
                      <span className="inline-flex items-center px-2 py-1 text-xs rounded-full bg-blue-100 text-blue-700 border border-blue-200">Unwatched</span>
                    )}
                    <div className="flex gap-1">
                      <button 
                        onClick={() => handleRating(it.trakt_id, it.media_type, 1)}
                        className={`p-1 rounded text-sm transition-colors ${
                          userRatings[it.trakt_id] === 1 
                            ? 'bg-green-500 text-white hover:bg-green-600' 
                            : 'bg-gray-100 text-gray-600 hover:bg-green-100 hover:text-green-700'
                        }`}
                        title="Thumbs up - I like this recommendation"
                      >
                        👍
                      </button>
                      <button 
                        onClick={() => handleRating(it.trakt_id, it.media_type, -1)}
                        className={`p-1 rounded text-sm transition-colors ${
                          userRatings[it.trakt_id] === -1 
                            ? 'bg-red-500 text-white hover:bg-red-600' 
                            : 'bg-gray-100 text-gray-600 hover:bg-red-100 hover:text-red-700'
                        }`}
                        title="Thumbs down - I don't like this recommendation"
                      >
                        👎
                      </button>
                    </div>
                  </div>
                </div>
                {entries.length > 0 && (
                  <div className="flex flex-wrap gap-2 mt-3">
                    {entries.map(([k,v])=> {
                      const compExplanations: Record<string, string> = {
                        genre_overlap: 'How closely the item matches your favorite genres.',
                        semantic_sim: 'How similar the item is to your past favorites (using AI).',
                        mood_score: 'How well the item fits your current mood.',
                        rating_norm: 'How highly this item is rated by the community.',
                        novelty: 'How new or unique this item is for you.',
                        popularity_norm: 'How popular this item is overall.'
                      };
                      return (
                        <span
                          key={k}
                          className={`inline-flex items-center px-2 py-0.5 text-xs rounded-full border ${compColor(k)}`}
                          title={compExplanations[k] || ''}
                        >
                          {compLabel(k)}: {(v as number).toFixed(2)}
                        </span>
                      );
                    })}
                  </div>
                )}
              </div>
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
  });
  const isSmart = meta.list_type === 'smartlist';
  const maxItems = account?.max_items_per_list ?? 100;
  const update = (k:string, v:any)=> setValues((prev:any)=> ({...prev, [k]: v }));

  return (
  <div className="mb-4 p-3 rounded-2xl border border-indigo-100 bg-white/80 backdrop-blur-xl shadow transition-all duration-500">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <label className="text-xs text-gray-700 flex flex-col gap-1">
          <span>Title</span>
          <input className="text-xs px-2 py-1 bg-white rounded border" value={values.title || ''} onChange={(e)=>update('title', e.target.value)} />
        </label>
        <label className="text-xs text-gray-700 flex items-center gap-2 bg-white px-2 py-1 rounded border">
          <input type="checkbox" checked={!!values.exclude_watched} onChange={(e)=>update('exclude_watched', e.target.checked)} />
          Exclude Watched
        </label>
        <label className="text-xs text-gray-700 flex flex-col gap-1">
          <span>Item limit</span>
          <select className="text-xs px-2 py-1 bg-white rounded border" value={values.item_limit}
            onChange={(e)=>update('item_limit', Math.min(Number(e.target.value), maxItems))}>
            {[10,20,50,100,200,500].filter(n=>n<=maxItems).map(n=> (
              <option key={n} value={n}>{n} items</option>
            ))}
          </select>
        </label>
        <label className="text-xs text-gray-700 flex flex-col gap-1">
          <span>Sync interval (hours)</span>
          <input type="number" min={1} max={48} className="text-xs px-2 py-1 bg-white rounded border" value={values.sync_interval ?? ''}
            onChange={(e)=>update('sync_interval', e.target.value ? Number(e.target.value) : undefined)} />
        </label>
        <label className="text-xs text-gray-700 flex flex-col gap-1">
          <span>Full sync cadence (days)</span>
          <input type="number" min={1} max={7} className="text-xs px-2 py-1 bg-white rounded border" value={values.full_sync_days}
            onChange={(e)=>update('full_sync_days', Number(e.target.value))} />
        </label>
      </div>
      {isSmart && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mt-2">
          <label className="text-xs text-gray-700 flex flex-col gap-1">
            <span>Discovery</span>
            <select className="text-xs px-2 py-1 bg-white rounded border" value={values.discovery} onChange={(e)=>update('discovery', e.target.value)}>
              <option value="balanced">Balanced</option>
              <option value="obscure">Obscure</option>
              <option value="popular">Popular</option>
              <option value="very_obscure">Very Obscure</option>
            </select>
          </label>
          <label className="text-xs text-gray-700 flex items-center gap-2 bg-white px-2 py-1 rounded border">
            <input type="checkbox" checked={!!values.fusion_mode} onChange={(e)=>update('fusion_mode', e.target.checked)} />
            Fusion mode
          </label>
          <div className="text-xs text-gray-700">
            <div className="mb-1">Media types</div>
            <div className="flex gap-2">
              <button type="button" onClick={()=>{
                const cur = new Set(values.media_types || []);
                if (cur.has('movies')) cur.delete('movies'); else cur.add('movies');
                update('media_types', Array.from(cur));
              }} className={`px-2 py-1 rounded border ${values.media_types?.includes('movies') ? 'bg-indigo-600 text-white border-indigo-600' : 'bg-white'}`}>Movies</button>
              <button type="button" onClick={()=>{
                const cur = new Set(values.media_types || []);
                if (cur.has('shows')) cur.delete('shows'); else cur.add('shows');
                update('media_types', Array.from(cur));
              }} className={`px-2 py-1 rounded border ${values.media_types?.includes('shows') ? 'bg-indigo-600 text-white border-indigo-600' : 'bg-white'}`}>Shows</button>
            </div>
            <div className="mt-2 flex gap-4">
              <label className="flex items-center gap-1 text-xs cursor-pointer">
                <input type="radio" name="genreModeEdit" value="any" checked={values.genre_mode==='any'} onChange={()=>update('genre_mode','any')} />
                Match <span className="font-semibold">any</span> genre
              </label>
              <label className="flex items-center gap-1 text-xs cursor-pointer">
                <input type="radio" name="genreModeEdit" value="all" checked={values.genre_mode==='all'} onChange={()=>update('genre_mode','all')} />
                Match <span className="font-semibold">all</span> genres
              </label>
            </div>
          </div>
        </div>
      )}
      <div className="flex gap-2 justify-end mt-3">
        <button onClick={onClose} className="px-3 py-1.5 text-xs rounded border bg-white hover:bg-gray-50">Cancel</button>
        <button onClick={()=>onSave(values)} disabled={!!saving} className={`px-3 py-1.5 text-xs rounded ${saving ? 'bg-gray-300 text-white' : 'bg-indigo-600 text-white hover:bg-indigo-700'}`}>{saving ? 'Saving…' : 'Save & Full Sync'}</button>
      </div>
    </div>
  );
}
