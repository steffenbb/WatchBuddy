import React from 'react';
import { motion } from 'framer-motion';
import { Phase, convertPhaseToList } from '../../api/phases';
import { ListPlus } from 'lucide-react';

export default function PhaseModal({ phase, onClose }: { phase: Phase; onClose: () => void }) {
  const handleConvertToList = async () => {
    try {
      const res = await convertPhaseToList(1, phase.id);
      alert(`Creating list from phase... List #${res.list_id}`);
      onClose();
    } catch (e) {
      console.error(e);
      alert('Failed to create list');
    }
  };

  return (
    <div className="fixed inset-0 z-50">
      <div className="absolute inset-0 bg-black/50" onClick={onClose} />
      <motion.div
        initial={{ x: '100%', opacity: 0 }}
        animate={{ x: 0, opacity: 1 }}
        exit={{ x: '100%', opacity: 0 }}
        transition={{ type: 'spring', damping: 20, stiffness: 200 }}
        className="absolute right-0 top-0 bottom-0 w-full sm:w-[520px] bg-[#0b1220] border-l border-white/10 p-4 overflow-y-auto"
      >
        <div className="flex items-center justify-between mb-3">
          <div className="text-lg font-semibold flex items-center gap-2">
            <span>{phase.icon || '🎬'}</span>
            <span>{phase.label}</span>
          </div>
          <div className="flex items-center gap-2">
            <button 
              onClick={handleConvertToList}
              className="px-3 py-1.5 rounded-lg bg-white/10 hover:bg-white/20 border border-white/10 text-white text-sm"
            >
              <span className="inline-flex items-center"><ListPlus className="w-4 h-4 mr-1" /> Make list</span>
            </button>
            <button onClick={onClose} className="text-white/70 hover:text-white">Close</button>
          </div>
        </div>

        <div className="text-white/70 text-sm mb-2">
          {phase.start_at ? new Date(phase.start_at).toLocaleDateString() : ''} — {phase.end_at ? new Date(phase.end_at).toLocaleDateString() : 'now'} · {phase.item_count} items
        </div>

        {/* Stats */}
        <div className="grid grid-cols-2 gap-3 mb-4">
          <div className="rounded-xl bg-white/[0.06] border border-white/10 p-3">
            <div className="text-xs text-white/60">Genres</div>
            <div className="text-sm">{phase.dominant_genres.slice(0,3).join(', ')}</div>
          </div>
          <div className="rounded-xl bg-white/[0.06] border border-white/10 p-3">
            <div className="text-xs text-white/60">Cohesion</div>
            <div className="text-sm">{(phase.cohesion * 100).toFixed(0)}%</div>
          </div>
          <div className="rounded-xl bg-white/[0.06] border border-white/10 p-3">
            <div className="text-xs text-white/60">Avg runtime</div>
            <div className="text-sm">{phase.avg_runtime ? `${phase.avg_runtime} min` : '—'}</div>
          </div>
          <div className="rounded-xl bg-white/[0.06] border border-white/10 p-3">
            <div className="text-xs text-white/60">Language</div>
            <div className="text-sm">{phase.top_language || '—'}</div>
          </div>
        </div>

        {phase.explanation && (
          <div className="mb-3 text-sm text-white/80">{phase.explanation}</div>
        )}

        {/* Poster grid */}
        <div className="grid grid-cols-3 gap-2">
          {(phase.representative_posters || []).slice(0, 9).map((p, i) => (
            <div key={i} className="aspect-[2/3] rounded-lg bg-center bg-cover" style={{ backgroundImage: `url(https://image.tmdb.org/t/p/w300${p})` }} />
          ))}
        </div>
      </motion.div>
    </div>
  );
}
