// src/components/AiListManager.tsx
// Modern AI-powered lists manager with glassmorphic theme
import React, { useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import { RefreshCw, Eye, Trash2 } from 'lucide-react';
import { createAiList, listAiLists, refreshAiList, deleteAiList, generateSeven, getCooldown } from '../api/aiLists';
import { toast } from '../utils/toast';
import AiListDetails from './AiListDetails';

export default function AiListManager() {
  const [prompt, setPrompt] = useState('');
  const [lists, setLists] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [cooldowns, setCooldowns] = useState<Record<string, number>>({});
  const [openId, setOpenId] = useState<string | null>(null);
  const [openTitle, setOpenTitle] = useState<string>('');

  const fetchLists = async () => {
    setLoading(true);
    try {
      const data = await listAiLists();
      setLists(data);
    } catch (e: any) {
      const errorMsg = e.message || 'Failed to fetch AI lists';
      setError(errorMsg);
      
      if (e.isRateLimit) {
        toast.error('Trakt rate limit exceeded. Please wait before loading lists.', 6000);
      } else if (!e.isTimeout) {
        toast.error(errorMsg);
      }
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchLists();
  }, []);

  // Poll cooldowns every second for visible lists
  useEffect(() => {
    if (lists.length === 0) return;
    const interval = setInterval(async () => {
      try {
        const entries = await Promise.all(lists.map(async (l) => {
          const r = await getCooldown(l.id);
          return [l.id, r.ttl as number] as const;
        }));
        const next: Record<string, number> = {};
        entries.forEach(([id, ttl]) => next[id] = ttl || 0);
        setCooldowns(next);
      } catch {}
    }, 1000);
    return () => clearInterval(interval);
  }, [lists]);

  const handleCreate = async () => {
    if (!prompt.trim()) return;
    setLoading(true);
    setError(null);
    try {
      // Optimistic UI - clear prompt and refresh immediately
      const userPrompt = prompt;
      setPrompt('');
      
      // Create list (returns immediately, generates in background)
      await createAiList(userPrompt);
      toast.success('AI list creation started!');
      
      // Refresh list immediately to show new list in "queued" state
      await fetchLists();
      
      // Keep loading spinner off so user can create more lists
      setLoading(false);
    } catch (e: any) {
      const errorMsg = e.message || 'Failed to create AI list';
      setError(errorMsg);
      
      if (e.isRateLimit) {
        toast.error('Trakt rate limit exceeded. Please wait before creating more lists.', 6000);
      } else if (e.isTimeout) {
        toast.warning('Creation request is taking longer than expected. It will continue in the background.', 5000);
      } else {
        toast.error(errorMsg);
      }
      
      setLoading(false);
    }
  };

  const handleRefresh = async (id: string) => {
    setLoading(true);
    setError(null);
    try {
      await refreshAiList(id);
      toast.success('List refresh started!');
      fetchLists();
    } catch (e: any) {
      const errorMsg = e.message || 'Failed to refresh list';
      setError(errorMsg);
      
      if (e.isRateLimit) {
        toast.error('Trakt rate limit exceeded. Please wait before refreshing.', 6000);
      } else if (e.isTimeout) {
        toast.warning('Refresh is taking longer than expected. It will continue in the background.', 5000);
      } else {
        toast.error(errorMsg);
      }
    } finally {
      setLoading(false);
    }
  };

  const handleDelete = async (id: string) => {
    if (!confirm('Delete this AI list?')) return;
    setLoading(true);
    setError(null);
    try {
      await deleteAiList(id);
      toast.success('AI list deleted successfully!');
      fetchLists();
    } catch (e: any) {
      const errorMsg = e.message || 'Failed to delete list';
      setError(errorMsg);
      toast.error(errorMsg);
    } finally {
      setLoading(false);
    }
  };

  const handleGenerateSeven = async () => {
    setLoading(true);
    setError(null);
    try {
      await generateSeven();
      toast.success('Generating 7 dynamic lists!');
      setTimeout(fetchLists, 2000); // Give tasks time to queue
    } catch (e: any) {
      const errorMsg = e.message || 'Failed to generate lists';
      setError(errorMsg);
      
      if (e.isRateLimit) {
        toast.error('Trakt rate limit exceeded. Please wait before generating lists.', 6000);
      } else {
        toast.error(errorMsg);
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="p-4 md:p-6 max-w-5xl mx-auto">
      {/* Hero section */}
      <div className="text-center mb-8">
        <h2 className="text-4xl md:text-5xl font-bold text-white mb-3 flex items-center justify-center gap-3">
          <span className="text-4xl">✨</span> AI-Powered Lists
        </h2>
        <p className="text-white/80 text-lg">Create personalized recommendations using natural language</p>
      </div>

      {/* Create section with glassmorphic card */}
      <div className="bg-white/10 backdrop-blur-lg border border-white/20 rounded-3xl shadow-2xl p-6 md:p-8 mb-6">
        <div className="flex flex-col gap-4">
          <div className="relative">
            <textarea
              className="w-full bg-white/10 backdrop-blur-sm border border-white/30 rounded-2xl px-4 py-3 text-white placeholder-white/50 focus:outline-none focus:ring-2 focus:ring-purple-400 transition-all resize-none min-h-[100px]"
              value={prompt}
              onChange={e => setPrompt(e.target.value)}
              placeholder="Describe your ideal list... e.g., 'Dark sci-fi thrillers from the 90s' or 'Feel-good romantic comedies'"
              disabled={loading}
            />
          </div>
          <div className="flex flex-col sm:flex-row gap-3">
            <button
              className="flex-1 bg-gradient-to-r from-purple-500 to-pink-500 hover:from-purple-600 hover:to-pink-600 text-white px-4 sm:px-6 py-3 rounded-xl font-semibold shadow-lg transition-all duration-200 disabled:opacity-50 disabled:cursor-not-allowed min-h-[44px]"
              onClick={handleCreate}
              disabled={loading || !prompt.trim()}
            >
              {loading ? '✨ Creating...' : '✨ Create List'}
            </button>
            <button
              className="bg-white/10 backdrop-blur-lg border border-white/20 hover:bg-white/15 text-white px-4 sm:px-6 py-3 rounded-xl font-semibold transition-all duration-200 disabled:opacity-50 min-h-[44px] whitespace-nowrap text-sm sm:text-base"
              onClick={handleGenerateSeven}
              disabled={loading}
            >
              <span className="hidden sm:inline">Generate 7 Dynamic Lists</span>
              <span className="sm:hidden">Generate 7 Lists</span>
            </button>
          </div>
        </div>
      </div>

      {/* Error display */}
      {error && (
        <div className="bg-red-500/20 border border-red-400/30 text-red-200 px-4 py-3 rounded-xl mb-6">
          {error}
        </div>
      )}

      {/* Loading skeleton */}
      {loading && lists.length === 0 && (
        <div className="space-y-4">
          {[1,2,3].map(i => (
            <div key={i} className="bg-white/5 backdrop-blur-lg rounded-2xl h-24 animate-pulse" />
          ))}
        </div>
      )}

      {/* Lists poster grid with animations */}
      <motion.div 
        className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4"
        initial="hidden"
        animate="visible"
        variants={{
          hidden: { opacity: 0 },
          visible: { opacity: 1, transition: { staggerChildren: 0.05 } }
        }}
      >
        {lists.map((list, idx) => {
          const cooldown = cooldowns[list.id] ?? 0;
          const statusStyles: Record<string, string> = {
            pending: 'bg-yellow-500/80 text-black',
            queued: 'bg-blue-500/80 text-white',
            running: 'bg-purple-500/80 text-white',
            ready: 'bg-emerald-500/80 text-white',
            error: 'bg-red-600/80 text-white',
          };
          const badgeClass = statusStyles[list.status] || statusStyles.pending;
          const posterPath: string | undefined = list.poster_path ? `/posters/${list.poster_path}` : undefined;

          return (
            <motion.div 
              key={list.id}
              whileHover={{ scale: 1.05, y: -4 }}
              whileTap={{ scale: 0.98 }}
              transition={{ type: "spring", stiffness: 300, damping: 20 }}
              variants={{
                hidden: { opacity: 0, y: 20 },
                visible: { opacity: 1, y: 0 }
              }}
              className="relative group rounded-2xl overflow-hidden bg-white/5 border border-white/10 shadow-lg hover:shadow-2xl hover:shadow-purple-500/20"
            >
              {/* Poster */}
              <div className="aspect-[2/3] w-full bg-gradient-to-br from-slate-900 to-slate-800">
                {posterPath ? (
                  <img src={posterPath} alt={list.generated_title || list.prompt || 'AI List'} className="w-full h-full object-cover" />
                ) : (
                  <div className="w-full h-full flex items-center justify-center text-white/40 text-sm px-2 text-center">
                    {list.status === 'pending' || list.status === 'queued' || list.status === 'running' ? 'Generating...' : 'No poster yet'}
                  </div>
                )}
              </div>

              {/* Gradient overlay */}
              <div className="absolute inset-0 bg-gradient-to-t from-black/70 via-black/20 to-transparent opacity-80 group-hover:opacity-90 transition-opacity" />

              {/* Status badge */}
              <div className={`absolute top-2 left-2 px-2 py-1 rounded-md text-xs font-medium ${badgeClass}`}>{list.status}</div>

              {/* Delete button - top right - Always visible on mobile, hover on desktop */}
              <button
                aria-label="Delete"
                onClick={(e) => { e.stopPropagation(); handleDelete(list.id); }}
                disabled={loading}
                className="absolute top-2 right-2 p-2 rounded-lg bg-black/60 hover:bg-red-600/80 text-white backdrop-blur-sm min-w-[36px] min-h-[36px] md:min-w-[28px] md:min-h-[28px] md:p-1.5 md:opacity-0 md:group-hover:opacity-100 transition-all active:scale-95"
              >
                <Trash2 size={16} className="md:w-3.5 md:h-3.5" />
              </button>

              {/* Bottom bar with title and actions */}
              <div className="absolute bottom-0 left-0 right-0 p-3 flex items-center justify-between">
                <div className="flex-1 min-w-0">
                  <div className="text-white font-semibold truncate">{list.generated_title || list.prompt}</div>
                  <div className="text-white/70 text-xs mt-0.5 truncate">{list.type}</div>
                </div>
                {/* Bottom buttons - Always visible on mobile, hover on desktop */}
                <div className="flex items-center gap-1.5 md:opacity-0 md:group-hover:opacity-100 transition-opacity ml-2">
                  <button
                    aria-label="View"
                    disabled={list.status !== 'ready'}
                    onClick={() => { if (list.status === 'ready') { setOpenId(list.id); setOpenTitle(list.generated_title || list.prompt || 'AI List'); } }}
                    className={`p-2 rounded-lg backdrop-blur-sm min-w-[36px] min-h-[36px] md:min-w-[32px] md:min-h-[32px] active:scale-95 transition-all ${list.status==='ready' ? 'bg-black/60 hover:bg-black/70 text-white' : 'bg-black/40 text-white/40 cursor-not-allowed'}`}
                  >
                    <Eye size={16} className="md:w-3.5 md:h-3.5" />
                  </button>
                  <button
                    aria-label="Refresh"
                    onClick={(e) => { e.stopPropagation(); handleRefresh(list.id); }}
                    disabled={cooldown > 0 || loading}
                    className={`p-2 rounded-lg backdrop-blur-sm min-w-[36px] min-h-[36px] md:min-w-[32px] md:min-h-[32px] active:scale-95 transition-all ${cooldown>0 || loading ? 'bg-black/40 text-white/40 cursor-not-allowed' : 'bg-black/60 hover:bg-black/70 text-white'}`}
                  >
                    {cooldown > 0 ? (
                      <span className="text-xs">{cooldown}s</span>
                    ) : (
                      <RefreshCw size={16} className={`${loading ? "animate-spin" : ""} md:w-3.5 md:h-3.5`} />
                    )}
                  </button>
                </div>
              </div>
            </motion.div>
          );
        })}
      </motion.div>

      {/* Empty state */}
      {/* Empty state */}
      {!loading && lists.length === 0 && (
        <div className="text-center py-12">
          <div className="text-6xl mb-4">🎬</div>
          <h3 className="text-2xl font-bold text-white mb-2">No AI lists yet</h3>
          <p className="text-white/60">Create your first AI-powered list or generate 7 dynamic recommendations!</p>
        </div>
      )}

      {openId && (
        <AiListDetails aiListId={openId} title={openTitle} onClose={() => setOpenId(null)} />
      )}
    </div>
  );
}
