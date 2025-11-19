import React, { useState, useEffect } from "react";
import { X } from "lucide-react";
import { motion } from "framer-motion";
import { api } from "../hooks/useApi";
import { toast } from "../utils/toast";

interface EditListModalProps {
  listId: number;
  currentTitle: string;
  onClose: () => void;
  onSaved: () => void;
}

export default function EditListModal({ listId, currentTitle, onClose, onSaved }: EditListModalProps) {
  const [title, setTitle] = useState(currentTitle);
  const [itemLimit, setItemLimit] = useState<number>(50);
  const [excludeWatched, setExcludeWatched] = useState(true);
  const [syncInterval, setSyncInterval] = useState<number>(24);
  const [discovery, setDiscovery] = useState('balanced');
  const [genres, setGenres] = useState<string[]>([]);
  const [genreMode, setGenreMode] = useState<'any'|'all'>('any');
  const [languages, setLanguages] = useState<string[]>([]);
  const [yearFrom, setYearFrom] = useState<number | undefined>();
  const [yearTo, setYearTo] = useState<number | undefined>();
  const [mediaType, setMediaType] = useState<string[]>(['movies', 'shows']);
  const [availableGenres, setAvailableGenres] = useState<string[]>([]);
  const [availableLanguages, setAvailableLanguages] = useState<{code: string, name: string}[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    async function loadData() {
      try {
        // Load list details
        const listRes = await api.get(`/lists/${listId}`);
        const list = listRes.data || listRes;
        setTitle(list.title || currentTitle);
        setItemLimit(list.item_limit || 50);
        setExcludeWatched(list.exclude_watched ?? true);
        setSyncInterval(list.sync_interval || 24);
        
        // Parse filters
        try {
          const filters = typeof list.filters === 'string' ? JSON.parse(list.filters) : (list.filters || {});
          setDiscovery(filters.discovery || filters.obscurity?.[0] || 'balanced');
          setGenres(filters.genres || []);
          setGenreMode(filters.genre_mode || 'any');
          setLanguages(filters.languages || []);
          setYearFrom(filters.year_from);
          setYearTo(filters.year_to);
          setMediaType(filters.media_types || ['movies', 'shows']);
        } catch {}

        // Load available genres and languages
        const [genresRes, langsRes] = await Promise.all([
          api.get('/metadata/options/genres'),
          api.get('/metadata/options/languages')
        ]);
        
        const genresData = genresRes.data || genresRes;
        const langsData = langsRes.data || langsRes;
        
        setAvailableGenres(genresData.genres || []);
        setAvailableLanguages(langsData.languages || []);
      } catch (e) {
        console.error('Failed to load data', e);
      } finally {
        setLoading(false);
      }
    }
    loadData();
  }, [listId, currentTitle]);

  // Lock body scroll when modal is open
  useEffect(() => {
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = '';
    };
  }, []);

  // Handle escape key
  useEffect(() => {
    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !saving) {
        onClose();
      }
    };
    document.addEventListener('keydown', handleEscape);
    return () => document.removeEventListener('keydown', handleEscape);
  }, [onClose, saving]);

  const toggleGenre = (genre: string) => {
    setGenres(prev => prev.includes(genre) ? prev.filter(g => g !== genre) : [...prev, genre]);
  };

  const toggleLanguage = (lang: string) => {
    setLanguages(prev => prev.includes(lang) ? prev.filter(l => l !== lang) : [...prev, lang]);
  };

  const toggleMediaType = (type: string) => {
    setMediaType(prev => prev.includes(type) ? prev.filter(t => t !== type) : [...prev, type]);
  };

  async function handleSave() {
    if (!title.trim()) {
      toast.error('Title cannot be empty');
      return;
    }

    try {
      setSaving(true);
      await api.patch(`/lists/${listId}`, { 
        title: title.trim(),
        item_limit: itemLimit,
        exclude_watched: excludeWatched,
        sync_interval: syncInterval,
        discovery: discovery,
        genres: genres.length > 0 ? genres : null,
        genre_mode: genreMode,
        languages: languages.length > 0 ? languages : null,
        media_types: mediaType,
        year_from: yearFrom,
        year_to: yearTo,
        user_id: 1 
      });
      toast.success('List updated successfully!');
      onSaved();
      onClose();
    } catch (e: any) {
      console.error('Failed to update list', e);
      if (e.isRateLimit) {
        toast.error('Trakt rate limit exceeded. Please wait before updating.', 6000);
      } else {
        toast.error(e.message || 'Failed to update list');
      }
    } finally {
      setSaving(false);
    }
  }

  return (
    <motion.div 
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      className="fixed inset-0 z-50 flex items-center justify-center p-4" 
      onClick={onClose}
    >
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" />
      <motion.div 
        initial={{ scale: 0.9, opacity: 0, y: 20 }}
        animate={{ scale: 1, opacity: 1, y: 0 }}
        exit={{ scale: 0.9, opacity: 0, y: 20 }}
        transition={{ type: "spring", stiffness: 300, damping: 30 }}
        className="relative w-full max-w-3xl max-h-[90vh] rounded-2xl overflow-hidden border border-white/20 bg-gradient-to-br from-slate-900 via-indigo-950 to-purple-950 shadow-2xl flex flex-col" 
        onClick={(e: React.MouseEvent)=>e.stopPropagation()}
      >
        <div className="flex items-center justify-between gap-3 p-4 border-b border-white/10">
          <h2 className="text-white font-semibold">Edit List</h2>
          <button onClick={onClose} aria-label="Close" className="p-2 rounded-lg bg-white/10 hover:bg-white/20 text-white">
            <X size={18} />
          </button>
        </div>

        {loading ? (
          <div className="p-6 text-white/70 text-center">Loading...</div>
        ) : (
          <div className="p-6 space-y-5 overflow-y-auto flex-1">
            <div>
              <label className="block text-white/80 text-sm mb-2">List Title</label>
              <input
                type="text"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleSave()}
                className="w-full px-4 py-2 rounded-lg bg-white/10 border border-white/20 text-white placeholder-white/40 focus:outline-none focus:ring-2 focus:ring-purple-400"
                placeholder="Enter list title"
                autoFocus
              />
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-white/80 text-sm mb-2">Max Items</label>
                <input
                  type="number"
                  min="10"
                  max="5000"
                  value={itemLimit}
                  onChange={(e) => setItemLimit(parseInt(e.target.value) || 50)}
                  className="w-full px-4 py-2 rounded-lg bg-white/10 border border-white/20 text-white placeholder-white/40 focus:outline-none focus:ring-2 focus:ring-purple-400"
                />
              </div>

              <div>
                <label className="block text-white/80 text-sm mb-2">Sync Interval (hours)</label>
                <input
                  type="number"
                  min="1"
                  max="168"
                  value={syncInterval}
                  onChange={(e) => setSyncInterval(parseInt(e.target.value) || 24)}
                  className="w-full px-4 py-2 rounded-lg bg-white/10 border border-white/20 text-white placeholder-white/40 focus:outline-none focus:ring-2 focus:ring-purple-400"
                />
              </div>
            </div>

            {/* Media Type Selection */}
            <div>
              <label className="block text-white/80 text-sm mb-2 font-semibold">Type</label>
              <div className="flex gap-2">
                <button 
                  onClick={() => toggleMediaType("movies")} 
                  className={`flex-1 min-h-[44px] px-4 py-2 rounded-xl text-sm border transition-all ${mediaType.includes("movies") ? "bg-gradient-to-r from-purple-500 to-pink-500 text-white border-transparent shadow-lg" : "bg-white/5 text-white/80 border-white/20 hover:border-purple-400 hover:bg-white/10"}`}
                >
                  🎬 Movies
                </button>
                <button 
                  onClick={() => toggleMediaType("shows")} 
                  className={`flex-1 min-h-[44px] px-4 py-2 rounded-xl text-sm border transition-all ${mediaType.includes("shows") ? "bg-gradient-to-r from-purple-500 to-pink-500 text-white border-transparent shadow-lg" : "bg-white/5 text-white/80 border-white/20 hover:border-purple-400 hover:bg-white/10"}`}
                >
                  📺 Shows
                </button>
              </div>
            </div>

            {/* Obscurity Selection */}
            <div>
              <label className="block text-white/80 text-sm mb-2 font-semibold">Obscurity</label>
              <div className="flex flex-wrap gap-2">
                {["very_obscure", "obscure", "balanced", "popular", "mainstream"].map(o => (
                  <button 
                    key={o} 
                    onClick={() => setDiscovery(o)} 
                    className={`min-h-[44px] px-4 py-2 rounded-xl text-sm border transition-all ${discovery === o ? "bg-gradient-to-r from-purple-500 to-pink-500 text-white border-transparent shadow-lg" : "bg-white/5 text-white/80 border-white/20 hover:border-purple-400 hover:bg-white/10"}`}
                  >
                    {o.replace("_", " ")}
                  </button>
                ))}
              </div>
              <p className="text-white/50 text-xs mt-2">💡 Tip: Obscurity controls discovery (popular ↔ obscure)</p>
            </div>

            {/* Genres Selection */}
            <div>
              <label className="block text-white/80 text-sm mb-2 font-semibold">Genres</label>
              <div className="flex flex-wrap gap-2 mb-2">
                {availableGenres.map(genre => (
                  <button 
                    key={genre} 
                    onClick={() => toggleGenre(genre.toLowerCase())} 
                    className={`min-h-[44px] px-4 py-2 rounded-xl text-sm border transition-all ${genres.includes(genre.toLowerCase()) ? "bg-gradient-to-r from-purple-500 to-pink-500 text-white border-transparent shadow-lg" : "bg-white/5 text-white/80 border-white/20 hover:border-purple-400 hover:bg-white/10"}`}
                  >
                    {genre}
                  </button>
                ))}
              </div>
              <div className="flex items-center gap-4">
                <label className="flex items-center gap-2 text-xs text-white/70 cursor-pointer">
                  <input 
                    type="radio" 
                    name="genreMode" 
                    value="any" 
                    checked={genreMode === 'any'} 
                    onChange={() => setGenreMode('any')} 
                    className="accent-purple-500" 
                  />
                  Match <span className="font-semibold text-white">any</span> selected genre
                </label>
                <label className="flex items-center gap-2 text-xs text-white/70 cursor-pointer">
                  <input 
                    type="radio" 
                    name="genreMode" 
                    value="all" 
                    checked={genreMode === 'all'} 
                    onChange={() => setGenreMode('all')} 
                    className="accent-purple-500" 
                  />
                  Match <span className="font-semibold text-white">all</span> selected genres
                </label>
              </div>
            </div>

            {/* Languages Selection */}
            <div>
              <label className="block text-white/80 text-sm mb-2 font-semibold">Languages</label>
              <div className="flex flex-wrap gap-2">
                {availableLanguages.map(lang => (
                  <button 
                    key={lang.code} 
                    onClick={() => toggleLanguage(lang.code)} 
                    className={`min-h-[44px] px-4 py-2 rounded-xl text-sm border transition-all ${languages.includes(lang.code) ? "bg-gradient-to-r from-purple-500 to-pink-500 text-white border-transparent shadow-lg" : "bg-white/5 text-white/80 border-white/20 hover:border-purple-400 hover:bg-white/10"}`}
                  >
                    {lang.name}
                  </button>
                ))}
              </div>
            </div>

            {/* Year Range */}
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-white/80 text-sm mb-2">Year From</label>
                <input
                  type="number"
                  min="1900"
                  max="2100"
                  value={yearFrom || ''}
                  onChange={(e) => setYearFrom(e.target.value ? parseInt(e.target.value) : undefined)}
                  placeholder="e.g., 2000"
                  className="w-full px-4 py-2 rounded-lg bg-white/10 border border-white/20 text-white placeholder-white/40 focus:outline-none focus:ring-2 focus:ring-purple-400"
                />
              </div>

              <div>
                <label className="block text-white/80 text-sm mb-2">Year To</label>
                <input
                  type="number"
                  min="1900"
                  max="2100"
                  value={yearTo || ''}
                  onChange={(e) => setYearTo(e.target.value ? parseInt(e.target.value) : undefined)}
                  placeholder="e.g., 2024"
                  className="w-full px-4 py-2 rounded-lg bg-white/10 border border-white/20 text-white placeholder-white/40 focus:outline-none focus:ring-2 focus:ring-purple-400"
                />
              </div>
            </div>

            <div className="flex items-center gap-3">
              <input
                type="checkbox"
                id="exclude-watched"
                checked={excludeWatched}
                onChange={(e) => setExcludeWatched(e.target.checked)}
                className="w-4 h-4 rounded border-white/20 bg-white/10 text-purple-500 focus:ring-2 focus:ring-purple-400"
              />
              <label htmlFor="exclude-watched" className="text-white/80 text-sm cursor-pointer">
                Exclude watched items from list
              </label>
            </div>

            <div className="flex gap-3 pt-2">
              <button
                onClick={handleSave}
                disabled={saving || !title.trim()}
                className="flex-1 px-4 py-2.5 rounded-lg bg-gradient-to-r from-purple-500 to-pink-500 hover:from-purple-600 hover:to-pink-600 text-white font-semibold disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {saving ? 'Saving...' : 'Save Changes'}
              </button>
              <button
                onClick={onClose}
                className="px-4 py-2.5 rounded-lg bg-white/10 hover:bg-white/20 text-white border border-white/20"
              >
                Cancel
              </button>
            </div>
          </div>
        )}
      </motion.div>
    </motion.div>
  );
}
