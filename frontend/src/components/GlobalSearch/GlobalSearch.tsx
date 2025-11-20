import React, { useState, useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { Search, X, Filter, SlidersHorizontal } from "lucide-react";
import { apiGet } from "../../api/client";
import { motion, AnimatePresence } from "framer-motion";

interface SearchResult {
  tmdb_id: number;
  media_type: string;
  title: string;
  original_title?: string;
  year?: number;
  overview?: string;
  poster_path?: string;
  vote_average?: number;
  relevance_score?: number;
  genres?: string;
}

export default function GlobalSearch() {
  const [isOpen, setIsOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [showFilters, setShowFilters] = useState(false);
  
  // Filters
  const [mediaType, setMediaType] = useState<string>("");
  const [genre, setGenre] = useState<string>("");
  const [minYear, setMinYear] = useState<string>("");
  const [maxYear, setMaxYear] = useState<string>("");
  const [sortBy, setSortBy] = useState<string>("relevance");
  
  const inputRef = useRef<HTMLInputElement>(null);
  const modalRef = useRef<HTMLDivElement>(null);

  // Focus input when modal opens
  useEffect(() => {
    if (isOpen && inputRef.current) {
      inputRef.current.focus();
    }
  }, [isOpen]);

  // Close on escape key
  useEffect(() => {
    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === "Escape" && isOpen) {
        setIsOpen(false);
      }
    };
    
    document.addEventListener("keydown", handleEscape);
    return () => document.removeEventListener("keydown", handleEscape);
  }, [isOpen]);

  // Search with debounce
  useEffect(() => {
    if (!query.trim()) {
      setResults([]);
      return;
    }

    const timer = setTimeout(async () => {
      setLoading(true);
      try {
        let url = `/search?q=${encodeURIComponent(query)}&limit=50`;
        if (mediaType) url += `&media_type=${mediaType}`;
        if (genre) url += `&genre=${encodeURIComponent(genre)}`;
        if (minYear) url += `&min_year=${minYear}`;
        if (maxYear) url += `&max_year=${maxYear}`;
        
        const data = await apiGet(url);
        let processedResults = Array.isArray(data) ? data : [];
        
        // Apply client-side sorting
        if (sortBy === "year_desc") {
          processedResults.sort((a, b) => (b.year || 0) - (a.year || 0));
        } else if (sortBy === "year_asc") {
          processedResults.sort((a, b) => (a.year || 0) - (b.year || 0));
        } else if (sortBy === "rating") {
          processedResults.sort((a, b) => (b.vote_average || 0) - (a.vote_average || 0));
        } else if (sortBy === "title") {
          processedResults.sort((a, b) => (a.title || "").localeCompare(b.title || ""));
        }
        // Default "relevance" - already sorted by API
        
        setResults(processedResults);
      } catch (error) {
        console.error("Search failed:", error);
        setResults([]);
      } finally {
        setLoading(false);
      }
    }, 300);

    return () => clearTimeout(timer);
  }, [query, mediaType, genre, minYear, maxYear, sortBy]);

  const handleItemClick = (item: SearchResult) => {
    window.location.hash = `item/${item.media_type}/${item.tmdb_id}`;
    setIsOpen(false);
    setQuery("");
    setResults([]);
  };

  const clearFilters = () => {
    setMediaType("");
    setGenre("");
    setMinYear("");
    setMaxYear("");
    setSortBy("relevance");
  };

  const hasActiveFilters = Boolean(mediaType || genre || minYear || maxYear || sortBy !== "relevance");

  return (
    <>
      {/* Search Button */}
      <button
        onClick={() => setIsOpen(true)}
        className="flex items-center gap-2 px-4 py-2 rounded-lg bg-white/10 hover:bg-white/20 border border-white/20 text-white transition-colors"
        aria-label="Open search"
      >
        <Search size={18} />
        <span className="hidden md:inline text-sm">Search...</span>
      </button>

      {/* Search Modal - Full Screen (Rendered via Portal) */}
      {isOpen && createPortal(
        <AnimatePresence>
          <motion.div
            ref={modalRef}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-[9999] bg-gradient-to-br from-indigo-950 via-purple-950 to-fuchsia-950 flex flex-col overflow-hidden"
            onClick={(e) => e.stopPropagation()}
          >
            {/* Full screen container */}
            <div className="w-full h-full flex flex-col overflow-hidden">
                {/* Search Input */}
                <div className="p-4 md:p-6 border-b border-white/20 flex-shrink-0">
                  <div className="flex items-center gap-3">
                    <Search className="text-white/60" size={20} />
                    <input
                      ref={inputRef}
                      type="text"
                      value={query}
                      onChange={(e) => setQuery(e.target.value)}
                      placeholder="Search movies and shows..."
                      className="flex-1 bg-transparent text-white placeholder-white/50 outline-none text-lg"
                    />
                    <button
                      onClick={() => setShowFilters(!showFilters)}
                      className={`p-2 rounded-lg transition-colors ${
                        hasActiveFilters || showFilters
                          ? "bg-purple-500/30 text-purple-300"
                          : "bg-white/10 text-white/60 hover:bg-white/20"
                      }`}
                      aria-label="Toggle filters"
                    >
                      <SlidersHorizontal size={18} />
                    </button>
                    <button
                      onClick={() => setIsOpen(false)}
                      className="p-2 rounded-lg bg-white/10 hover:bg-white/20 text-white/60"
                      aria-label="Close search"
                    >
                      <X size={18} />
                    </button>
                  </div>

                  {/* Filters */}
                  {showFilters && (
                    <motion.div
                      initial={{ height: 0, opacity: 0 }}
                      animate={{ height: "auto", opacity: 1 }}
                      exit={{ height: 0, opacity: 0 }}
                      className="mt-4 pt-4 border-t border-white/20 space-y-3"
                    >
                      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                        <select
                          value={mediaType}
                          onChange={(e) => setMediaType(e.target.value)}
                          className="px-3 py-2 rounded-lg bg-white/10 border border-white/20 text-white text-sm outline-none"
                        >
                          <option value="">All Types</option>
                          <option value="movie">Movies</option>
                          <option value="tv">TV Shows</option>
                        </select>

                        <input
                          type="text"
                          value={genre}
                          onChange={(e) => setGenre(e.target.value)}
                          placeholder="Genre..."
                          className="px-3 py-2 rounded-lg bg-white/10 border border-white/20 text-white text-sm placeholder-white/50 outline-none"
                        />

                        <input
                          type="number"
                          value={minYear}
                          onChange={(e) => setMinYear(e.target.value)}
                          placeholder="Min Year"
                          className="px-3 py-2 rounded-lg bg-white/10 border border-white/20 text-white text-sm placeholder-white/50 outline-none"
                        />

                        <input
                          type="number"
                          value={maxYear}
                          onChange={(e) => setMaxYear(e.target.value)}
                          placeholder="Max Year"
                          className="px-3 py-2 rounded-lg bg-white/10 border border-white/20 text-white text-sm placeholder-white/50 outline-none"
                        />
                      </div>

                      <div className="grid grid-cols-2 gap-3">
                        <select
                          value={sortBy}
                          onChange={(e) => setSortBy(e.target.value)}
                          className="px-3 py-2 rounded-lg bg-white/10 border border-white/20 text-white text-sm outline-none"
                        >
                          <option value="relevance">Sort: Relevance</option>
                          <option value="year_desc">Sort: Newest First</option>
                          <option value="year_asc">Sort: Oldest First</option>
                          <option value="rating">Sort: Rating</option>
                          <option value="title">Sort: Title (A-Z)</option>
                        </select>

                        {hasActiveFilters && (
                          <button
                            onClick={clearFilters}
                            className="px-3 py-2 rounded-lg bg-red-500/20 hover:bg-red-500/30 text-red-300 text-sm transition-colors"
                          >
                            Clear All
                          </button>
                        )}
                      </div>
                    </motion.div>
                  )}
                </div>

                {/* Results - Full Screen with Flex Grow */}
                <div className="flex-1 overflow-y-auto p-4 md:p-6">
                  {loading ? (
                    <div className="text-center py-8 text-white/60">Searching...</div>
                  ) : !query.trim() ? (
                    <div className="text-center py-8 text-white/60">
                      Type to search across 20,000+ movies and shows
                    </div>
                  ) : results.length === 0 ? (
                    <div className="text-center py-8 text-white/60">No results found</div>
                  ) : (
                    <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-3 md:gap-4">
                      {results.map((item) => (
                        <div
                          key={`${item.media_type}-${item.tmdb_id}`}
                          className="group cursor-pointer"
                          onClick={() => handleItemClick(item)}
                        >
                          <div className="relative aspect-[2/3] mb-2 rounded-lg overflow-hidden bg-white/10 border border-white/20 group-hover:ring-2 group-hover:ring-purple-500 transition-all">
                            {item.poster_path ? (
                              <img
                                src={`https://image.tmdb.org/t/p/w342${item.poster_path}`}
                                alt={item.title}
                                className="w-full h-full object-cover"
                                loading="lazy"
                              />
                            ) : (
                              <div className="w-full h-full flex items-center justify-center text-white/40 text-xs">
                                No Image
                              </div>
                            )}
                            
                            {item.relevance_score && (
                              <div className="absolute top-2 left-2 bg-black/80 text-white text-xs px-2 py-1 rounded-full">
                                {Math.round(item.relevance_score * 100)}%
                              </div>
                            )}
                          </div>

                          <h3 className="text-white text-sm font-semibold line-clamp-2 group-hover:text-purple-300 transition-colors">
                            {item.title}
                          </h3>

                          <div className="flex items-center gap-2 text-xs text-white/60 mt-1">
                            {item.year && <span>{item.year}</span>}
                            {item.vote_average && (
                              <>
                                <span>•</span>
                                <span>⭐ {item.vote_average.toFixed(1)}</span>
                              </>
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
          </motion.div>
        </AnimatePresence>,
        document.body
      )}
    </>
  );
}
