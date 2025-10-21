// src/components/AiListManager.tsx
// Modern AI-powered lists manager with glassmorphic theme
import React, { useEffect, useState } from 'react';
import { createAiList, listAiLists, refreshAiList, deleteAiList, generateSeven, getCooldown } from '../api/aiLists';
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
      setError(e.message);
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
      
      // Refresh list immediately to show new list in "queued" state
      await fetchLists();
      
      // Keep loading spinner off so user can create more lists
      setLoading(false);
    } catch (e: any) {
      setError(e.message);
      setLoading(false);
    }
  };

  const handleRefresh = async (id: string) => {
    setLoading(true);
    setError(null);
    try {
      await refreshAiList(id);
      fetchLists();
    } catch (e: any) {
      setError(e.message);
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
      fetchLists();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  const handleGenerateSeven = async () => {
    setLoading(true);
    setError(null);
    try {
      await generateSeven();
      setTimeout(fetchLists, 2000); // Give tasks time to queue
    } catch (e: any) {
      setError(e.message);
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

      {/* Lists grid */}
      <div className="space-y-4">
        {lists.map(list => {
          const cooldown = cooldowns[list.id] ?? 0;
          const statusColors = {
            pending: 'bg-yellow-500/30 text-yellow-200',
            queued: 'bg-blue-500/30 text-blue-200',
            running: 'bg-purple-500/30 text-purple-200',
            ready: 'bg-emerald-500/30 text-emerald-200',
            error: 'bg-red-500/30 text-red-200',
          };
          const statusColor = statusColors[list.status as keyof typeof statusColors] || statusColors.pending;

          const handleOpen = () => {
            if (list.status === 'ready') {
              setOpenId(list.id);
              setOpenTitle(list.generated_title || list.prompt || 'AI List');
            }
          };

          return (
            <div
              key={list.id}
              className={`bg-white/10 backdrop-blur-lg border border-white/20 rounded-2xl shadow-lg transition-all duration-200 p-4 md:p-6 ${
                list.status === 'ready' ? 'hover:bg-white/15 cursor-pointer' : 'cursor-default'
              }`}
              onClick={handleOpen}
            >
              <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
                <div className="flex-1 min-w-0 overflow-hidden">
                  <div className="flex items-center gap-3 mb-2 overflow-hidden">
                    <h3 className="font-bold text-xl text-white truncate overflow-hidden text-ellipsis whitespace-nowrap flex-shrink min-w-0">
                      {list.generated_title || list.prompt}
                    </h3>
                    <span className={`px-3 py-1 rounded-full text-xs font-medium flex-shrink-0 ${statusColor}`}>
                      {list.status}
                    </span>
                  </div>
                  {list.prompt && list.generated_title && (
                    <p className="text-white/60 text-sm mb-2 line-clamp-2 overflow-hidden">{list.prompt}</p>
                  )}
                  <div className="flex items-center gap-3 text-xs text-white/50 overflow-hidden">
                    <span className="px-2 py-1 bg-indigo-500/20 rounded-full flex-shrink-0">{list.type}</span>
                    {list.last_synced_at && (
                      <span className="truncate">Last synced: {new Date(list.last_synced_at).toLocaleString()}</span>
                    )}
                  </div>
                </div>
                <div className="flex flex-wrap gap-2">
                  <button
                    className={`px-3 sm:px-4 py-3 rounded-xl font-semibold transition-all min-h-[44px] text-sm ${
                      list.status === 'ready' ? 'bg-white/10 border border-white/20 text-white hover:bg-white/15' : 'bg-white/5 text-white/40 cursor-not-allowed'
                    }`}
                    onClick={(e) => { e.stopPropagation(); if (list.status === 'ready') { setOpenId(list.id); setOpenTitle(list.generated_title || list.prompt || 'AI List'); } }}
                    disabled={list.status !== 'ready' || loading}
                  >
                    View
                  </button>
                  <button
                    className={`px-3 sm:px-4 py-3 rounded-xl font-semibold transition-all min-h-[44px] text-sm ${
                      cooldown > 0
                        ? 'bg-white/5 text-white/40 cursor-not-allowed'
                        : 'bg-indigo-500/80 hover:bg-indigo-600 text-white shadow-lg'
                    }`}
                    onClick={(e) => { e.stopPropagation(); handleRefresh(list.id); }}
                    disabled={cooldown > 0 || loading}
                  >
                    {cooldown > 0 ? `⏱ ${cooldown}s` : '🔄 Refresh'}
                  </button>
                  <button
                    className="px-3 sm:px-4 py-3 bg-red-500/80 hover:bg-red-600 text-white rounded-xl font-semibold transition-all min-h-[44px] text-sm"
                    onClick={(e) => { e.stopPropagation(); handleDelete(list.id); }}
                    disabled={loading}
                  >
                    🗑️
                  </button>
                </div>
              </div>
            </div>
          );
        })}
      </div>

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
