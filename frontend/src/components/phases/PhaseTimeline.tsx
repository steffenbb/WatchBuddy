import React, { useEffect, useRef, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { fetchPhaseTimeline, fetchPhaseDetail, Phase } from '../../api/phases';
import { Timeline, TimelineOptions, DataSet } from 'vis-timeline/standalone';
import 'vis-timeline/styles/vis-timeline-graph2d.css';
import PhaseModal from './PhaseModal';
import { Calendar, TrendingUp, Film, Tv, Sparkles, ZoomIn, ZoomOut, Maximize2 } from 'lucide-react';

export default function PhaseTimeline() {
  const containerRef = useRef<HTMLDivElement>(null);
  const timelineRef = useRef<Timeline | null>(null);
  const [items, setItems] = useState<any[]>([]);
  const [selectedPhase, setSelectedPhase] = useState<Phase | null>(null);
  const [loading, setLoading] = useState(false);
  const [stats, setStats] = useState({ total: 0, movies: 0, shows: 0, avgCohesion: 0 });

  useEffect(() => {
    async function load() {
      try {
        const res = await fetchPhaseTimeline(1);
        const timelineItems = res.timeline || [];
        setItems(timelineItems);
        
        // Calculate stats
        const total = timelineItems.length;
        const movies = timelineItems.reduce((sum: number, i: any) => sum + (i.movie_count || 0), 0);
        const shows = timelineItems.reduce((sum: number, i: any) => sum + (i.show_count || 0), 0);
        const avgCohesion = timelineItems.length > 0 
          ? timelineItems.reduce((sum: number, i: any) => sum + (i.cohesion || 0), 0) / timelineItems.length 
          : 0;
        setStats({ total, movies, shows, avgCohesion });
      } catch (e) {
        console.error('Failed to load timeline', e);
      }
    }
    load();
  }, []);

  useEffect(() => {
    if (!containerRef.current || items.length === 0) return;
    
    const container = containerRef.current;
    
    // Enhanced styling for timeline items
    const timelineItems = new DataSet(items.map((i, idx) => {
      // Generate gradient colors based on phase position
      const hue = (idx * 360 / items.length) % 360;
      const color = `hsl(${hue}, 70%, 55%)`;
      const darkColor = `hsl(${hue}, 70%, 40%)`;
      
      return {
        id: i.id,
        content: `
          <div style="
            padding: 8px 12px; 
            font-weight: 600; 
            font-size: 13px;
            display: flex;
            align-items: center;
            gap: 6px;
            text-shadow: 0 1px 2px rgba(0,0,0,0.3);
          ">
            <span style="font-size: 16px;">${i.icon || '🎬'}</span>
            <span>${i.label}</span>
          </div>
        `,
        start: i.start,
        end: i.end,
        style: `
          background: linear-gradient(135deg, ${color} 0%, ${darkColor} 100%);
          color: #fff;
          border: none;
          border-radius: 10px;
          box-shadow: 0 4px 12px rgba(0,0,0,0.3);
          cursor: pointer;
          transition: all 0.2s ease;
        `,
        className: 'timeline-phase-item',
        title: `
          <div style="padding: 4px 0;">
            <strong style="font-size: 14px;">${i.label}</strong><br>
            <span style="opacity: 0.9;">${i.item_count} items (${i.movie_count || 0} movies, ${i.show_count || 0} shows)</span><br>
            <span style="opacity: 0.8;">Cohesion: ${(i.cohesion * 100).toFixed(0)}%</span><br>
            <span style="opacity: 0.8;">Top genres: ${i.genres.slice(0, 3).join(', ')}</span>
          </div>
        `
      };
    }));
    
    const options: TimelineOptions = {
      stack: true,
      showCurrentTime: true,
      zoomable: true,
      moveable: true,
      orientation: 'top',
      height: '600px',
      margin: {
        item: 15,
        axis: 10
      },
      tooltip: {
        followMouse: true,
        overflowMethod: 'cap',
        delay: 100
      },
      // Enhanced styling
      timeAxis: { scale: 'day', step: 7 },
      format: {
        minorLabels: {
          day: 'D',
          month: 'MMM',
          year: 'YYYY'
        },
        majorLabels: {
          month: 'MMMM YYYY',
          year: 'YYYY'
        }
      }
    };
    
    const timeline = new Timeline(container, timelineItems, options);
    timelineRef.current = timeline;
    
    // Add custom CSS for timeline items hover effect
    const style = document.createElement('style');
    style.textContent = `
      .timeline-phase-item:hover {
        transform: translateY(-2px);
        box-shadow: 0 6px 16px rgba(0,0,0,0.4) !important;
      }
      .vis-timeline {
        border: none !important;
        background: rgba(255,255,255,0.03) !important;
        border-radius: 12px !important;
      }
      .vis-panel.vis-background {
        background: transparent !important;
      }
      .vis-panel.vis-top {
        background: transparent !important;
        border-bottom: 1px solid rgba(255,255,255,0.1) !important;
      }
      .vis-time-axis .vis-text {
        color: rgba(255,255,255,0.7) !important;
        font-weight: 500 !important;
      }
      .vis-time-axis .vis-grid.vis-major {
        border-color: rgba(255,255,255,0.1) !important;
      }
      .vis-time-axis .vis-grid.vis-minor {
        border-color: rgba(255,255,255,0.05) !important;
      }
      .vis-current-time {
        background-color: rgba(239, 68, 68, 0.3) !important;
        border-left: 2px solid rgb(239, 68, 68) !important;
      }
    `;
    document.head.appendChild(style);
    
    // Add click handler
    timeline.on('select', async (properties) => {
      if (properties.items && properties.items.length > 0) {
        const phaseId = properties.items[0];
        setLoading(true);
        try {
          const phase = await fetchPhaseDetail(1, phaseId as number);
          setSelectedPhase(phase);
        } catch (e) {
          console.error('Failed to load phase detail', e);
        } finally {
          setLoading(false);
        }
      }
    });
    
    return () => {
      timeline.destroy();
      timelineRef.current = null;
      style.remove();
    };
  }, [items]);

  const handleZoomIn = () => {
    if (timelineRef.current) {
      timelineRef.current.zoomIn(0.5);
    }
  };

  const handleZoomOut = () => {
    if (timelineRef.current) {
      timelineRef.current.zoomOut(0.5);
    }
  };

  const handleFit = () => {
    if (timelineRef.current) {
      timelineRef.current.fit();
    }
  };

  if (items.length === 0) {
    return (
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        className="text-center py-20"
      >
        <div className="inline-flex items-center justify-center w-20 h-20 rounded-full bg-white/10 mb-4">
          <Calendar size={40} className="text-white/60" />
        </div>
        <h3 className="text-2xl font-bold text-white mb-2">No Phases Yet</h3>
        <p className="text-white/60 max-w-md mx-auto">
          Start watching and rating content to build your viewing history. 
          We'll automatically detect phases in your viewing patterns.
        </p>
      </motion.div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Stats Overview */}
      <motion.div
        initial={{ opacity: 0, y: -20 }}
        animate={{ opacity: 1, y: 0 }}
        className="grid grid-cols-2 md:grid-cols-4 gap-4"
      >
        <div className="bg-gradient-to-br from-purple-500/20 to-purple-600/10 border border-purple-500/30 rounded-xl p-4 backdrop-blur-sm">
          <div className="flex items-center gap-2 mb-1">
            <Sparkles size={18} className="text-purple-400" />
            <span className="text-white/70 text-sm">Total Phases</span>
          </div>
          <div className="text-3xl font-bold text-white">{stats.total}</div>
        </div>
        
        <div className="bg-gradient-to-br from-blue-500/20 to-blue-600/10 border border-blue-500/30 rounded-xl p-4 backdrop-blur-sm">
          <div className="flex items-center gap-2 mb-1">
            <Film size={18} className="text-blue-400" />
            <span className="text-white/70 text-sm">Movies</span>
          </div>
          <div className="text-3xl font-bold text-white">{stats.movies}</div>
        </div>
        
        <div className="bg-gradient-to-br from-green-500/20 to-green-600/10 border border-green-500/30 rounded-xl p-4 backdrop-blur-sm">
          <div className="flex items-center gap-2 mb-1">
            <Tv size={18} className="text-green-400" />
            <span className="text-white/70 text-sm">Shows</span>
          </div>
          <div className="text-3xl font-bold text-white">{stats.shows}</div>
        </div>
        
        <div className="bg-gradient-to-br from-amber-500/20 to-amber-600/10 border border-amber-500/30 rounded-xl p-4 backdrop-blur-sm">
          <div className="flex items-center gap-2 mb-1">
            <TrendingUp size={18} className="text-amber-400" />
            <span className="text-white/70 text-sm">Avg Cohesion</span>
          </div>
          <div className="text-3xl font-bold text-white">{(stats.avgCohesion * 100).toFixed(0)}%</div>
        </div>
      </motion.div>

      {/* Timeline Container */}
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.1 }}
        className="rounded-2xl border border-white/20 bg-gradient-to-br from-white/[0.08] to-white/[0.03] backdrop-blur-xl overflow-hidden shadow-2xl"
      >
        {/* Header with controls */}
        <div className="p-6 border-b border-white/10 bg-gradient-to-r from-purple-500/10 to-blue-500/10">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-lg bg-gradient-to-br from-purple-500 to-blue-500 flex items-center justify-center">
                <Calendar size={20} className="text-white" />
              </div>
              <div>
                <h2 className="text-2xl font-bold text-white">Your Phase Timeline</h2>
                <p className="text-white/60 text-sm">Visualizing {stats.total} distinct viewing phases</p>
              </div>
            </div>
            
            {/* Timeline controls */}
            <div className="flex items-center gap-2">
              <button
                onClick={handleZoomIn}
                className="p-2 rounded-lg bg-white/10 hover:bg-white/20 border border-white/20 text-white transition-colors"
                title="Zoom In"
              >
                <ZoomIn size={18} />
              </button>
              <button
                onClick={handleZoomOut}
                className="p-2 rounded-lg bg-white/10 hover:bg-white/20 border border-white/20 text-white transition-colors"
                title="Zoom Out"
              >
                <ZoomOut size={18} />
              </button>
              <button
                onClick={handleFit}
                className="p-2 rounded-lg bg-white/10 hover:bg-white/20 border border-white/20 text-white transition-colors"
                title="Fit All"
              >
                <Maximize2 size={18} />
              </button>
            </div>
          </div>
          
          <div className="flex items-center gap-2 text-white/70 text-sm">
            <span className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-white/10">
              💡 Click any phase to explore details
            </span>
            <span className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-white/10">
              🖱️ Scroll to zoom • Drag to pan
            </span>
          </div>
        </div>
        
        {/* Timeline */}
        <div className="p-6">
          <div ref={containerRef} className="rounded-xl overflow-hidden shadow-inner" />
        </div>
      </motion.div>

      {/* Loading overlay */}
      <AnimatePresence>
        {loading && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 bg-black/50 backdrop-blur-sm flex items-center justify-center z-50"
          >
            <div className="bg-white/10 backdrop-blur-xl border border-white/20 rounded-2xl p-6 flex items-center gap-4">
              <div className="w-6 h-6 border-3 border-white/30 border-t-white rounded-full animate-spin" />
              <span className="text-white font-medium">Loading phase details...</span>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
      
      {/* Phase detail modal */}
      <AnimatePresence>
        {selectedPhase && (
          <PhaseModal phase={selectedPhase} onClose={() => setSelectedPhase(null)} />
        )}
      </AnimatePresence>
    </div>
  );
}
