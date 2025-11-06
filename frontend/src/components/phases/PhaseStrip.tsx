import React, { useEffect, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Phase, fetchCurrentPhase, fetchPhaseHistory, fetchPredictedPhase, convertPhaseToList, PhasePrediction } from '../../api/phases';
import { Sparkles, ListPlus, TrendingUp, Calendar } from 'lucide-react';
import PhaseModal from './PhaseModal';

export default function PhaseStrip() {
  const [current, setCurrent] = useState<Phase | null>(null);
  const [predicted, setPredicted] = useState<PhasePrediction | null>(null);
  const [history, setHistory] = useState<Phase[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<Phase | null>(null);

  useEffect(() => {
    async function load() {
      setLoading(true);
      try {
        const [c, p, h] = await Promise.all([
          fetchCurrentPhase(1),
          fetchPredictedPhase(1, 42),
          fetchPhaseHistory(1, 12)
        ]);
        setCurrent(c);
        setPredicted(p);
        setHistory(h);
      } catch (e) {
        console.error('Failed to load phases', e);
      } finally {
        setLoading(false);
      }
    }
    load();
  }, []);

  if (loading) return null;
  if (!current && !predicted && history.length === 0) return null;

  return (
    <div className="space-y-4">
      {/* Current Phase */}
      {current && (
        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          className="relative overflow-hidden rounded-2xl bg-white/[0.06] backdrop-blur border border-white/10"
        >
          {/* Poster collage background */}
          <div className="absolute inset-0 opacity-30">
            <div className="grid grid-cols-3 gap-2 p-3">
              {(current.representative_posters || []).slice(0, 3).map((p, i) => (
                <div key={i} className="aspect-[2/3] rounded-lg bg-center bg-cover" style={{ backgroundImage: `url(https://image.tmdb.org/t/p/w300${p})` }} />
              ))}
            </div>
            <div className="absolute inset-0 bg-gradient-to-r from-black/60 via-black/30 to-transparent" />
          </div>
          
          {/* Content */}
          <div className="relative p-6 flex items-center justify-between">
            <div>
              <div className="text-sm text-white/70 mb-1">Current Phase</div>
              <div className="text-2xl md:text-3xl font-bold flex items-center gap-2">
                <span>{current.icon || '🎬'}</span>
                <span>{current.label}</span>
              </div>
              <div className="text-white/70 text-sm mt-1">
                {current.start_at ? new Date(current.start_at).toLocaleDateString() : ''} — now · {current.item_count} items
                {current.top_language ? ` · ${current.top_language}` : ''}
              </div>
              <div className="text-white/70 text-xs mt-1">
                {current.movie_count} movies · {current.show_count} shows · cohesion {(current.cohesion*100).toFixed(0)}%
              </div>
              <div className="mt-3 flex gap-2">
                <button onClick={() => setSelected(current)} className="px-3 py-1.5 rounded-lg bg-white/10 hover:bg-white/20 border border-white/10 text-white text-sm">
                  <span className="inline-flex items-center"><Sparkles className="w-4 h-4 mr-1" /> Show phase picks</span>
                </button>
                <button className="px-3 py-1.5 rounded-lg bg-white/5 hover:bg-white/10 border border-white/10 text-white text-sm" onClick={async () => {
                  try {
                    const res = await convertPhaseToList(1, current.id);
                    // Simple toast
                    alert(`Creating list from phase... List #${res.list_id}`);
                  } catch (e) {
                    console.error(e);
                    alert('Failed to create list');
                  }
                }}>
                  <span className="inline-flex items-center"><ListPlus className="w-4 h-4 mr-1" /> Make phase list</span>
                </button>
                <a href="#timeline" className="px-3 py-1.5 rounded-lg bg-white/5 hover:bg-white/10 border border-white/10 text-white text-sm inline-flex items-center">
                  <Calendar className="w-4 h-4 mr-1" /> View timeline
                </a>
              </div>
            </div>
          </div>
        </motion.div>
      )}

      {/* Predicted Next Phase */}
      {predicted && (
        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.1 }}
          className="relative overflow-hidden rounded-2xl bg-gradient-to-br from-purple-900/20 via-indigo-900/20 to-blue-900/20 backdrop-blur border border-purple-500/30"
        >
          {/* Poster collage background */}
          <div className="absolute inset-0 opacity-20">
            <div className="grid grid-cols-3 gap-2 p-3">
              {(predicted.representative_posters || []).slice(0, 3).map((p, i) => (
                <div key={i} className="aspect-[2/3] rounded-lg bg-center bg-cover" style={{ backgroundImage: `url(https://image.tmdb.org/t/p/w300${p})` }} />
              ))}
            </div>
            <div className="absolute inset-0 bg-gradient-to-r from-black/70 via-black/40 to-transparent" />
          </div>
          
          {/* Content */}
          <div className="relative p-6">
            <div className="flex items-center gap-2 text-sm text-purple-300 mb-1">
              <TrendingUp className="w-4 h-4" />
              <span>Predicted Next Phase</span>
            </div>
            <div className="text-2xl md:text-3xl font-bold flex items-center gap-2 mb-2">
              <span>{predicted.icon || '🔮'}</span>
              <span>{predicted.label}</span>
            </div>
            <div className="text-white/80 text-sm mb-2">
              {predicted.explanation}
            </div>
            <div className="flex items-center gap-3 text-white/70 text-xs">
              <span>Confidence: {(predicted.confidence * 100).toFixed(0)}%</span>
              <span>·</span>
              <span>Based on {predicted.item_count} recent items</span>
            </div>
          </div>
        </motion.div>
      )}

      {/* History badges */}
      {history.length > 0 && (
        <div className="overflow-x-auto">
          <div className="flex gap-3 pb-2">
            {history.map((p) => (
              <motion.button
                key={p.id}
                onClick={() => setSelected(p)}
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                className="shrink-0 w-44 text-left rounded-xl border border-white/10 bg-white/[0.05] hover:bg-white/[0.08] backdrop-blur p-2"
              >
                <div className="relative">
                  <div className="aspect-[2/3] rounded-lg bg-center bg-cover" style={{ backgroundImage: `url(https://image.tmdb.org/t/p/w300${(p.representative_posters||[])[0]||''})` }} />
                  <div className="absolute bottom-1 left-1 right-1 text-xs bg-black/50 rounded px-1 py-0.5">
                    {p.label}
                  </div>
                </div>
                <div className="text-xs text-white/70 mt-1">
                  {p.start_at ? new Date(p.start_at).toLocaleDateString() : ''} — {p.end_at ? new Date(p.end_at).toLocaleDateString() : ''}
                </div>
              </motion.button>
            ))}
          </div>
        </div>
      )}

      <AnimatePresence>
        {selected && (
          <PhaseModal phase={selected} onClose={() => setSelected(null)} />
        )}
      </AnimatePresence>
    </div>
  );
}
