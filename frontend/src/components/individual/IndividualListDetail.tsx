import React, { useEffect, useMemo, useState } from "react";
import { addItemsToIndividualList, getIndividualList, removeItemFromIndividualList, reorderIndividualList, searchIndividualList, syncIndividualListToTrakt } from "../../api/individualLists";
import SearchModal from "./SearchModal";
import SuggestionsSidebar from "./SuggestionsSidebar";
import { useToast } from "../ToastProvider";
import HoverInfoCard from "../HoverInfoCard";

export default function IndividualListDetail({ listId, onBack }: { listId: number; onBack: () => void }) {
  const [list, setList] = useState<any | null>(null);
  const [loading, setLoading] = useState(false);
  const [showSearch, setShowSearch] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const { addToast } = useToast();

  async function load() {
    setLoading(true);
    try {
      const res = await getIndividualList(listId, 1);
      setList(res);
    } catch (e) {
      console.error(e);
      addToast({ message: "Failed to load list", type: "error" });
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, [listId]);

  async function moveItem(itemId: number, direction: -1 | 1) {
    if (!list?.items) return;
    const idx = list.items.findIndex((i: any) => i.id === itemId);
    const swapIdx = idx + direction;
    if (idx < 0 || swapIdx < 0 || swapIdx >= list.items.length) return;
    const copy = [...list.items];
    const tmp = copy[idx];
    copy[idx] = copy[swapIdx];
    copy[swapIdx] = tmp;
    setList({ ...list, items: copy });
    try {
      await reorderIndividualList(listId, copy.map((i: any) => i.id), 1);
      await load();
    } catch (e) {
      console.error(e);
      addToast({ message: "Failed to reorder items", type: "error" });
      await load(); // Reload to restore correct order
    }
  }

  async function handleSync() {
    if (syncing) return;
    setSyncing(true);
    try {
      const result = await syncIndividualListToTrakt(listId, 1);
      if (result.success) {
        addToast({ message: result.message, type: "success" });
      } else {
        addToast({ message: result.message, type: "error" });
      }
      await load();
    } catch (e) {
      console.error(e);
      addToast({ message: "Failed to sync to Trakt", type: "error" });
    } finally {
      setSyncing(false);
    }
  }

  return (
    <div className="grid lg:grid-cols-3 gap-4 p-2 md:p-0 overflow-hidden">
      <div className="lg:col-span-2 space-y-3 min-w-0">
        <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
          <div className="flex items-center gap-2 flex-shrink-0 min-w-0">
            <button onClick={onBack} className="px-3 py-2 rounded-lg bg-white/15 text-white hover:bg-white/25 text-sm md:text-base flex-shrink-0">← Back</button>
            <h2 className="text-white font-bold text-lg md:text-xl truncate min-w-0">{list?.name || "List"}</h2>
          </div>
          <div className="flex gap-2 w-full sm:w-auto flex-shrink-0">
            <button onClick={() => setShowSearch(true)} className="flex-1 sm:flex-none px-3 py-2 rounded-lg bg-white/15 text-white hover:bg-white/25 text-sm md:text-base">Add</button>
            <button 
              onClick={handleSync} 
              disabled={syncing}
              className="flex-1 sm:flex-none px-3 py-2 rounded-lg bg-emerald-500 hover:bg-emerald-600 text-white disabled:opacity-50 text-sm md:text-base whitespace-nowrap"
            >{syncing ? "Syncing..." : "Sync Trakt"}</button>
          </div>
        </div>

        {loading ? (
          <div className="text-white/80">Loading…</div>
        ) : !list ? (
          <div className="text-white/70">Not found</div>
        ) : list.items?.length === 0 ? (
          <div className="text-white/60 text-center py-8">This list is empty. Click "Add" to search for movies and shows!</div>
        ) : (
          <div className="grid sm:grid-cols-2 xl:grid-cols-3 gap-3 max-w-full">
            {list.items?.map((item: any) => (
              <HoverInfoCard
                key={item.id}
                tmdbId={item.tmdb_id}
                mediaType={item.media_type}
                fallbackInfo={{
                  title: item.title,
                  media_type: item.media_type,
                  release_date: item.year ? `${item.year}-01-01` : null
                }}
              >
                <div className="bg-white/10 border border-white/20 rounded-2xl p-3 flex gap-3 min-w-0 overflow-hidden">
                  <div className="shrink-0 w-16">
                    {item.poster_path ? (
                      <img
                        src={`https://image.tmdb.org/t/p/w154${item.poster_path}`}
                        alt={item.title}
                        className="w-16 h-24 object-cover rounded-md border border-white/20"
                        loading="lazy"
                      />
                    ) : (
                      <div className="w-16 h-24 rounded-md bg-white/10 border border-white/20 flex items-center justify-center text-white/60 text-xs">
                        No image
                      </div>
                    )}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="text-white font-semibold truncate">{item.title}</div>
                    <div className="text-white/70 text-xs">{item.media_type} · {item.year || '—'}</div>
                    {item.fit_score != null && (
                      <div className="text-xs text-white/70 mt-1"><span className="px-2 py-0.5 bg-emerald-500/20 rounded-full">fit {(item.fit_score*100).toFixed(0)}%</span></div>
                    )}
                  </div>
                  <div className="flex flex-col gap-2">
                    <button onClick={() => moveItem(item.id, -1)} className="px-2 py-1 rounded-md bg-white/10 text-white hover:bg-white/20 text-xs">↑</button>
                    <button onClick={() => moveItem(item.id, 1)} className="px-2 py-1 rounded-md bg-white/10 text-white hover:bg-white/20 text-xs">↓</button>
                    <button onClick={async()=>{ 
                    try {
                      await removeItemFromIndividualList(listId, item.id, 1); 
                      addToast({ message: `Removed ${item.title}`, type: "success" });
                      await load();
                    } catch (e) {
                      console.error(e);
                      addToast({ message: "Failed to remove item", type: "error" });
                    }
                  }} className="px-2 py-1 rounded-md bg-red-500/80 text-white hover:bg-red-600 text-xs">×</button>
                </div>
              </div>
              </HoverInfoCard>
            ))}
          </div>
        )}
      </div>
      <div>
        <SuggestionsSidebar listId={listId} onAdded={load} />
      </div>

      {showSearch && <SearchModal listId={listId} onClose={()=>setShowSearch(false)} onAdded={load} />}
    </div>
  );
}
