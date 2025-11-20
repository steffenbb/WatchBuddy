import React from "react";
import { X, RefreshCw, ThumbsUp, ThumbsDown, Filter, Eye, EyeOff } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { api } from "../hooks/useApi";

interface ListModalProps {
  listId: number;
  title: string;
  onClose: () => void;
}

export default function ListModal({ listId, title, onClose }: ListModalProps) {
  // Log title to ensure it's passed correctly
  React.useEffect(() => {
    console.log('[ListModal] Opening with title:', title, 'listId:', listId);
  }, [title, listId]);
  const [items, setItems] = React.useState<any[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [syncing, setSyncing] = React.useState(false);
  const [hideWatched, setHideWatched] = React.useState(false);
  const [sortBy, setSortBy] = React.useState<'score' | 'title' | 'year' | 'rank'>('score');
  const [userRatings, setUserRatings] = React.useState<Record<number, number>>({});

  React.useEffect(() => {
    let cancel = false;
    async function load() {
      try {
        setLoading(true);
        const res = await api.get(`/lists/${listId}/items?limit=50`);
        const data = res.data || res;
        if (!cancel) {
          const fetchedItems = data.items || data;
          setItems(fetchedItems);
          
          // Fetch user ratings for all items
          const traktIds = fetchedItems.map((it: any) => it.trakt_id).filter(Boolean);
          if (traktIds.length > 0) {
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
        }
      } catch (e) {
        console.error("Failed to load list items", e);
      } finally {
        if (!cancel) setLoading(false);
      }
    }
    load();
    return () => { cancel = true; };
  }, [listId]);

  async function handleRate(itemId: number, traktId: number, mediaType: string, liked: boolean) {
    try {
      // Call the correct ratings endpoint
      await api.post('/ratings/rate', { 
        user_id: 1, 
        trakt_id: traktId,
        media_type: mediaType,
        rating: liked ? 1 : -1
      });
      // Update local state
      const newRating = liked ? 1 : -1;
      setItems(prev => prev.map(it => 
        it.id === itemId ? { ...it, user_rating: newRating } : it
      ));
      setUserRatings(prev => ({
        ...prev,
        [traktId]: newRating
      }));
    } catch (e) {
      console.error("Failed to rate item", e);
    }
  }

  async function handleSync() {
    try {
      setSyncing(true);
      await api.post(`/lists/${listId}/sync?user_id=1&force_full=true`);
      // Reload a moment later to allow backend to update items
      setTimeout(async () => {
        try {
          const res = await api.get(`/lists/${listId}/items?limit=50`);
          const data = res.data || res;
          setItems(data.items || data);
        } catch {}
        setSyncing(false);
      }, 800);
    } catch (e) {
      console.error("Sync failed", e);
      setSyncing(false);
    }
  }

  // Filter and sort items
  const filteredAndSorted = React.useMemo(() => {
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

  // Lock body scroll when modal is open
  React.useEffect(() => {
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = '';
    };
  }, []);

  // Handle escape key
  React.useEffect(() => {
    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose();
      }
    };
    document.addEventListener('keydown', handleEscape);
    return () => document.removeEventListener('keydown', handleEscape);
  }, [onClose]);

  return (
    <motion.div 
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      className="fixed inset-0 z-50 flex items-center justify-center p-0 md:p-4" 
      onClick={onClose}
    >
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" />
      <motion.div 
        initial={{ scale: 0.9, opacity: 0, y: 20 }}
        animate={{ scale: 1, opacity: 1, y: 0 }}
        exit={{ scale: 0.9, opacity: 0, y: 20 }}
        transition={{ type: "spring", stiffness: 300, damping: 30 }}
        className="relative w-full h-full md:h-auto md:max-w-6xl md:max-h-[90vh] md:rounded-3xl overflow-hidden border-0 md:border md:border-white/20 bg-gradient-to-br from-slate-900 via-indigo-950 to-purple-950 shadow-2xl" 
        onClick={(e)=>e.stopPropagation()}
      >
        <div className="sticky top-0 z-10 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-2 sm:gap-3 p-3 md:p-4 border-b border-white/10 bg-black/30 backdrop-blur">
          <h2 className="text-white font-semibold w-full sm:flex-1 sm:truncate text-sm md:text-base break-words line-clamp-2 sm:line-clamp-1">{title}</h2>
          <div className="flex items-center gap-2 flex-shrink-0 w-full sm:w-auto justify-end">
            <button 
              onClick={() => setHideWatched(!hideWatched)} 
              className={`px-2 md:px-3 py-2 rounded-lg text-white text-xs md:text-sm border border-white/20 ${hideWatched ? "bg-indigo-500/30" : "bg-white/10 hover:bg-white/20"}`}
              title={hideWatched ? "Show watched" : "Hide watched"}
            >
              {hideWatched ? <Eye size={16} /> : <EyeOff size={16} />}
            </button>
            <select 
              value={sortBy} 
              onChange={(e) => setSortBy(e.target.value as any)}
              className="px-2 md:px-3 py-2 rounded-lg text-white text-xs md:text-sm border border-white/20 bg-white/10 hover:bg-white/20"
            >
              <option value="score" className="bg-slate-900">Score</option>
              <option value="title" className="bg-slate-900">Title</option>
              <option value="year" className="bg-slate-900">Year</option>
              <option value="rank" className="bg-slate-900">Rank</option>
            </select>
            <button onClick={handleSync} disabled={syncing} className={`px-2 md:px-3 py-2 rounded-lg text-white text-xs md:text-sm border border-white/20 ${syncing?"bg-white/5 text-white/40":"bg-white/10 hover:bg-white/20"}`}>
              <span className="inline-flex items-center gap-2"><RefreshCw size={16} className={syncing?"animate-spin":""}/> <span className="hidden sm:inline">Sync</span></span>
            </button>
            <button onClick={onClose} aria-label="Close" className="p-2 rounded-lg bg-white/10 hover:bg-white/20 text-white border border-white/20">
              <X size={18} />
            </button>
          </div>
        </div>

        <div className="p-3 md:p-4 overflow-y-auto h-[calc(100vh-64px)] md:max-h-[calc(90vh-80px)]">
          {loading ? (
            <div className="flex items-center justify-center py-16 text-white/70">Loading…</div>
          ) : filteredAndSorted.length === 0 ? (
            <div className="flex items-center justify-center py-16 text-white/70">
              {hideWatched && items.length > 0 ? "All items watched" : "No items yet"}
            </div>
          ) : (
            <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-2 md:gap-3">
              {filteredAndSorted.map((it: any, idx: number) => {
                // Backend returns poster_url (full URL) not poster_path
                const poster = it.poster_url || it.poster_path || it.metadata?.poster_path;
                // If it's already a full URL, use it directly, otherwise prefix with TMDB
                const src = (poster && poster.startsWith("http")) ? poster : (poster ? `https://image.tmdb.org/t/p/w342${poster}` : undefined);
                // Get user rating from state (trakt_id is the key)
                const userRating = userRatings[it.trakt_id] || 0;
                
                const handleItemClick = () => {
                  if (it.tmdb_id) {
                    window.location.hash = `item/${it.media_type}/${it.tmdb_id}`;
                  }
                };
                
                return (
                  <motion.div 
                    key={idx}
                    initial={{ opacity: 0, scale: 0.9 }}
                    animate={{ opacity: 1, scale: 1 }}
                    transition={{ delay: idx * 0.02, duration: 0.2 }}
                    className="relative group rounded-xl overflow-hidden bg-white/5 border border-white/10 hover:ring-2 hover:ring-purple-500 transition cursor-pointer"
                    onClick={handleItemClick}
                  >
                      <div className="aspect-[2/3] w-full bg-slate-900">
                        {src ? (
                          <img src={src} alt={it.title || "Item"} className="w-full h-full object-cover" loading="lazy" />
                        ) : (
                          <div className="w-full h-full flex items-center justify-center text-white/40 text-xs">No poster</div>
                        )}
                        
                        {/* Score overlay */}
                        {it.score != null && (
                          <div className="absolute top-2 left-2 px-2 py-1 rounded-lg backdrop-blur-sm bg-black/70 text-white font-semibold text-xs">
                            {Math.round(it.score * 100)}%
                          </div>
                        )}
                      </div>
                      
                      {/* Rating buttons */}
                      <div className="absolute top-2 right-2 flex flex-col gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                        <button
                          onClick={(e) => { e.stopPropagation(); handleRate(it.id, it.trakt_id, it.media_type, true); }}
                          className={`p-1.5 rounded-lg backdrop-blur-sm ${userRating === 1 ? 'bg-green-500/80 text-white' : 'bg-black/50 hover:bg-green-500/80 text-white'}`}
                          aria-label="Like"
                        >
                          <ThumbsUp size={12} />
                        </button>
                        <button
                          onClick={(e) => { e.stopPropagation(); handleRate(it.id, it.trakt_id, it.media_type, false); }}
                          className={`p-1.5 rounded-lg backdrop-blur-sm ${userRating === -1 ? 'bg-red-500/80 text-white' : 'bg-black/50 hover:bg-red-500/80 text-white'}`}
                          aria-label="Dislike"
                        >
                          <ThumbsDown size={12} />
                        </button>
                      </div>
                      
                      <div className="p-2 text-xs text-white/80 truncate">{it.title || it.original_title || "Untitled"}</div>
                    </motion.div>
                );
              })}
            </div>
          )}
        </div>
      </motion.div>
    </motion.div>
  );
}
