import React, { useEffect, useState, useMemo } from 'react';
import { RefreshCw, ThumbsUp, ThumbsDown, Eye, EyeOff } from 'lucide-react';
import { listAiListItems, refreshAiList } from '../api/aiLists';
import { toast } from '../utils/toast';
import HoverInfoCard from './HoverInfoCard';
import { api } from '../hooks/useApi';

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
  watched?: boolean;
  year?: number;
}

export default function AiListDetails({ aiListId, title, onClose }: { aiListId: string; title: string; onClose: ()=>void }){
  const [items, setItems] = useState<AiListItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hideWatched, setHideWatched] = useState(false);
  const [sortBy, setSortBy] = useState<'score' | 'title' | 'year' | 'rank'>('rank');
  const [userRatings, setUserRatings] = useState<Record<number, number>>({});

  useEffect(() => {
    let mounted = true;
    async function load(){
      try{
        setLoading(true); setError(null);
        const data = await listAiListItems(aiListId);
        if (mounted) {
          setItems(data || []);
          
          // Fetch user ratings
          try {
            const ratingsRes = await api.get('/ratings/user/1');
            const ratingsData = ratingsRes.data || ratingsRes;
            const ratingsMap: Record<number, number> = {};
            if (Array.isArray(ratingsData.ratings)) {
              ratingsData.ratings.forEach((r: any) => {
                ratingsMap[r.trakt_id] = r.rating;
              });
            }
            setUserRatings(ratingsMap);
          } catch (e) {
            console.error("Failed to load user ratings", e);
          }
        }
      }catch(e:any){
        const errorMsg = e?.message || 'Failed to load AI list items';
        setError(errorMsg);
        if (mounted) toast.error(errorMsg);
      }finally{
        if (mounted) setLoading(false);
      }
    }
    load();
    return () => { mounted = false; };
  }, [aiListId]);

  const handleRate = async (traktId: number, mediaType: string, liked: boolean) => {
    try {
      await api.post('/ratings/rate', { 
        user_id: 1, 
        trakt_id: traktId,
        media_type: mediaType,
        rating: liked ? 1 : -1
      });
      const newRating = liked ? 1 : -1;
      setUserRatings(prev => ({
        ...prev,
        [traktId]: newRating
      }));
    } catch (e) {
      console.error("Failed to rate item", e);
      toast.error("Failed to save rating");
    }
  };

  const handleSync = async () => {
    try {
      setSyncing(true);
      await refreshAiList(aiListId);
      toast.success('List refresh started!');
      
      // Reload items after a moment
      setTimeout(async () => {
        try {
          const data = await listAiListItems(aiListId);
          setItems(data || []);
        } catch (e: any) {
          if (e.isRateLimit) {
            toast.error('Trakt rate limit exceeded. Please wait before refreshing again.', 6000);
          }
        }
        setSyncing(false);
      }, 1000);
    } catch (e: any) {
      const errorMsg = e?.message || 'Failed to refresh list';
      setError(errorMsg);
      
      if (e.isRateLimit) {
        toast.error('Trakt rate limit exceeded. Please wait a few minutes before refreshing again.', 6000);
      } else if (e.isTimeout) {
        toast.warning('Refresh is taking longer than expected. It will continue in the background.', 5000);
      } else {
        toast.error(errorMsg);
      }
      
      setSyncing(false);
    }
  };

  // Filter and sort items
  const filteredAndSorted = useMemo(() => {
    let filtered = [...items];
    
    // Filter watched items if hideWatched is true
    if (hideWatched) {
      filtered = filtered.filter(it => !it.watched);
    }
    
    // Sort items
    filtered.sort((a, b) => {
      switch (sortBy) {
        case 'score':
          return (b.score || 0) - (a.score || 0);
        case 'title':
          return (a.title || '').localeCompare(b.title || '');
        case 'year':
          return (b.year || 0) - (a.year || 0);
        case 'rank':
          return (a.rank || 0) - (b.rank || 0);
        default:
          return 0;
      }
    });
    
    return filtered;
  }, [items, hideWatched, sortBy]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/60" onClick={onClose} />
      <div className="relative w-full max-w-5xl bg-white/10 backdrop-blur-xl border border-white/20 rounded-2xl shadow-2xl p-4 md:p-6 overflow-hidden">
        <div className="flex items-center justify-between mb-4 gap-3">
          <h3 className="text-xl md:text-2xl font-bold text-white truncate overflow-hidden text-ellipsis whitespace-nowrap flex-shrink min-w-0">{title}</h3>
          <div className="flex items-center gap-2 flex-shrink-0">
            <button 
              onClick={() => setHideWatched(!hideWatched)} 
              className={`px-3 py-2 rounded-lg text-white text-sm border border-white/20 ${hideWatched ? "bg-indigo-500/30" : "bg-white/10 hover:bg-white/20"}`}
              title={hideWatched ? "Show watched" : "Hide watched"}
            >
              {hideWatched ? <Eye size={16} /> : <EyeOff size={16} />}
            </button>
            <select 
              value={sortBy} 
              onChange={(e) => setSortBy(e.target.value as any)}
              className="px-3 py-2 rounded-lg text-white text-sm border border-white/20 bg-white/10 hover:bg-white/20"
            >
              <option value="rank" className="bg-slate-900">Rank</option>
              <option value="score" className="bg-slate-900">Score</option>
              <option value="title" className="bg-slate-900">Title</option>
              <option value="year" className="bg-slate-900">Year</option>
            </select>
            <button 
              onClick={handleSync} 
              disabled={syncing || loading}
              className={`px-3 py-2 rounded-lg text-white text-sm border border-white/20 ${syncing || loading ? 'bg-white/5 text-white/40' : 'bg-white/10 hover:bg-white/20'}`}
            >
              <span className="inline-flex items-center gap-2">
                <RefreshCw size={16} className={syncing ? "animate-spin" : ""} />
                <span className="hidden sm:inline">Sync</span>
              </span>
            </button>
            <button onClick={onClose} className="px-3 py-2 bg-white/10 hover:bg-white/20 text-white rounded-lg border border-white/20">Close</button>
          </div>
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
        ) : filteredAndSorted.length === 0 ? (
          <div className="text-white/70">
            {hideWatched && items.length > 0 ? "All items watched" : "No items yet."}
          </div>
        ) : (
          <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-4 max-h-[70vh] overflow-auto pr-1">
            {filteredAndSorted.map((it, idx) => (
              <HoverInfoCard
                key={`${it.tmdb_id}-${it.rank}`}
                tmdbId={it.tmdb_id}
                traktId={it.trakt_id}
                mediaType={it.media_type}
                fallbackInfo={{
                  title: it.title,
                  media_type: it.media_type
                }}
              >
                <div className="relative group bg-white/5 border border-white/10 rounded-xl overflow-hidden hover:bg-white/10 transition">
                  {it.poster_url ? (
                    <img src={it.poster_url} alt={it.title || ''} className="w-full h-48 object-cover" />
                  ) : (
                    <div className="w-full h-48 bg-white/5 flex items-center justify-center text-white/50">No poster</div>
                  )}
                  
                  {/* Rating buttons */}
                  <div className="absolute top-2 right-2 flex flex-col gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                    <button
                      onClick={() => it.trakt_id && it.media_type && handleRate(it.trakt_id, it.media_type, true)}
                      className={`p-1.5 rounded-lg backdrop-blur-sm ${it.trakt_id && userRatings[it.trakt_id] === 1 ? 'bg-green-500/80 text-white' : 'bg-black/50 hover:bg-green-500/80 text-white'}`}
                      aria-label="Like"
                    >
                      <ThumbsUp size={12} />
                    </button>
                    <button
                      onClick={() => it.trakt_id && it.media_type && handleRate(it.trakt_id, it.media_type, false)}
                      className={`p-1.5 rounded-lg backdrop-blur-sm ${it.trakt_id && userRatings[it.trakt_id] === -1 ? 'bg-red-500/80 text-white' : 'bg-black/50 hover:bg-red-500/80 text-white'}`}
                      aria-label="Dislike"
                    >
                      <ThumbsDown size={12} />
                    </button>
                  </div>
                  
                  <div className="p-3">
                    <div className="text-white font-semibold truncate" title={it.title || undefined}>{it.title || `#${it.tmdb_id || it.trakt_id}`}</div>
                    <div className="text-white/60 text-xs mt-1 flex items-center gap-2 flex-wrap">
                      <span className="px-2 py-0.5 bg-indigo-500/30 rounded-full">{it.media_type || 'item'}</span>
                      <span className="px-2 py-0.5 bg-purple-500/30 rounded-full">#{it.rank}</span>
                      <span className="px-2 py-0.5 bg-emerald-500/30 rounded-full" title={`Score: ${it.score ?? 0}`}>{(it.score ?? 0).toFixed(1)}</span>
                    </div>
                    {it.explanation_text && (
                      <div className="text-white/70 text-xs mt-2 line-clamp-2">{it.explanation_text}</div>
                    )}
                  </div>
                </div>
              </HoverInfoCard>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
