import React from "react";
import { motion } from "framer-motion";
import { RefreshCw, Eye, Edit2, Trash2 } from "lucide-react";
import { api } from "../hooks/useApi";
import { toast } from "../utils/toast";

interface ListCardProps {
  id: number;
  title: string;
  listType?: string;
  posterPath?: string | null;
  itemLimit?: number | null;
  onOpen?: (id: number, title: string) => void;
  onSynced?: () => void;
  onEdit?: (id: number) => void;
  onDelete?: (id: number) => void;
}

export default function ListCard({ id, title, listType, posterPath, itemLimit, onOpen, onSynced, onEdit, onDelete }: ListCardProps) {
  const [syncing, setSyncing] = React.useState(false);
  const imageUrl = posterPath ? `/posters/${posterPath}` : undefined;

  async function handleSync() {
    try {
      setSyncing(true);
      await api.post(`/lists/${id}/sync?user_id=1&force_full=true`);
      toast.success('List synced successfully!');
      onSynced?.();
    } catch (e: any) {
      console.error("Failed to sync list", e);
      
      // Provide specific user feedback based on error type
      if (e.isRateLimit) {
        toast.error('Trakt rate limit exceeded. Please wait a few minutes before syncing again.', 6000);
      } else if (e.isTimeout) {
        toast.warning('Sync is taking longer than expected. It will continue in the background.', 5000);
      } else {
        toast.error(e.message || 'Failed to sync list. Please try again.');
      }
    } finally {
      setSyncing(false);
    }
  }

  async function handleDelete(e: React.MouseEvent) {
    e.stopPropagation();
    if (!confirm(`Delete "${title}"?`)) return;
    try {
      await api.delete(`/lists/${id}?user_id=1`);
      toast.success('List deleted successfully!');
      onDelete?.(id);
    } catch (e: any) {
      console.error("Failed to delete list", e);
      toast.error(e.message || 'Failed to delete list. Please try again.');
    }
  }

  return (
    <motion.div
      whileHover={{ scale: 1.05, y: -4 }}
      whileTap={{ scale: 0.98 }}
      transition={{ type: "spring", stiffness: 300, damping: 20 }}
      className="relative group rounded-2xl overflow-hidden bg-white/5 border border-white/10 shadow-lg hover:shadow-2xl hover:shadow-purple-500/20 flex md:block"
    >
      {/* Poster section */}
      <div className="w-24 h-36 flex-shrink-0 md:aspect-[2/3] md:w-full md:h-auto bg-gradient-to-br from-slate-900 to-slate-800">
        {imageUrl ? (
          <img src={imageUrl} alt={title} className="w-full h-full object-cover" loading="lazy" />
        ) : (
          <div className="w-full h-full flex items-center justify-center text-white/40 text-sm">
            No poster
          </div>
        )}
      </div>
      <div className="absolute inset-0 bg-gradient-to-t from-black/70 via-black/20 to-transparent opacity-80 group-hover:opacity-90 transition-opacity md:block hidden" />
      
      {/* Action buttons - Top right corner - Always visible on mobile, hover on desktop */}
      <div className="absolute top-2 right-2 flex items-center gap-1.5 md:opacity-0 md:group-hover:opacity-100 transition-opacity">
        {onEdit && (
          <button
            aria-label="Edit"
            onClick={(e) => { e.stopPropagation(); onEdit(id); }}
            className="p-2 rounded-lg bg-black/60 hover:bg-black/70 text-white backdrop-blur-sm min-w-[36px] min-h-[36px] md:min-w-[28px] md:min-h-[28px] md:p-1.5 active:scale-95 transition-all"
          >
            <Edit2 size={16} className="md:w-3.5 md:h-3.5" />
          </button>
        )}
        {onDelete && (
          <button
            aria-label="Delete"
            onClick={handleDelete}
            className="p-2 rounded-lg bg-black/60 hover:bg-red-600/80 text-white backdrop-blur-sm min-w-[36px] min-h-[36px] md:min-w-[28px] md:min-h-[28px] md:p-1.5 active:scale-95 transition-all"
          >
            <Trash2 size={16} className="md:w-3.5 md:h-3.5" />
          </button>
        )}
      </div>
      
      {/* Desktop: absolute positioned overlay */}
      <div className="hidden md:flex absolute bottom-0 left-0 right-0 p-3 items-center justify-between">
        <div className="flex-1 min-w-0">
          <div className="text-white font-semibold truncate leading-tight">{title}</div>
          <div className="text-white/70 text-xs mt-0.5">
            {listType || "list"}{itemLimit ? ` • ${itemLimit} items` : ""}
          </div>
        </div>
        {/* Bottom buttons - hover on desktop */}
        <div className="flex items-center gap-1.5 opacity-0 group-hover:opacity-100 transition-opacity ml-2">
          <button
            aria-label="View"
            onClick={() => onOpen?.(id, title)}
            className="p-2 rounded-lg bg-black/60 hover:bg-black/70 text-white backdrop-blur-sm min-w-[32px] min-h-[32px] active:scale-95 transition-all"
          >
            <Eye size={16} className="w-3.5 h-3.5" />
          </button>
          <button
            aria-label="Sync"
            onClick={handleSync}
            disabled={syncing}
            className={`p-2 rounded-lg ${syncing ? "bg-black/40 text-white/40" : "bg-black/60 hover:bg-black/70 text-white"} backdrop-blur-sm min-w-[32px] min-h-[32px] active:scale-95 transition-all`}
          >
            <RefreshCw size={16} className={`${syncing ? "animate-spin" : ""} w-3.5 h-3.5`} />
          </button>
        </div>
      </div>

      {/* Mobile: side-by-side layout with full title */}
      <div className="md:hidden flex-1 flex flex-col p-3 justify-between min-w-0">
        <div className="flex-1">
          <div className="text-white font-semibold text-base leading-tight mb-1">{title}</div>
          <div className="text-white/70 text-xs">
            {listType || "list"}{itemLimit ? ` • ${itemLimit} items` : ""}
          </div>
        </div>
        <div className="flex items-center gap-2 mt-3">
          <button
            aria-label="View"
            onClick={() => onOpen?.(id, title)}
            className="flex-1 py-2 rounded-lg bg-white/10 hover:bg-white/20 text-white text-sm font-medium active:scale-95 transition-all flex items-center justify-center gap-2"
          >
            <Eye size={16} />
            View
          </button>
          <button
            aria-label="Sync"
            onClick={handleSync}
            disabled={syncing}
            className={`flex-1 py-2 rounded-lg ${syncing ? "bg-white/5 text-white/40" : "bg-white/10 hover:bg-white/20 text-white"} text-sm font-medium active:scale-95 transition-all flex items-center justify-center gap-2`}
          >
            <RefreshCw size={16} className={syncing ? "animate-spin" : ""} />
            {syncing ? 'Syncing' : 'Sync'}
          </button>
        </div>
      </div>
    </motion.div>
  );
}
