import React, { useEffect, useState } from "react";
import { apiGet } from "../../api/client";

interface SimilarItem {
  tmdb_id: number;
  media_type: string;
  title: string;
  year?: number;
  poster_path?: string;
  vote_average?: number;
  similarity_score: number;
  is_watched?: boolean;
  user_rating?: number | null;
}

interface ItemSuggestionsProps {
  mediaType: string;
  tmdbId: number;
  userId?: number;
}

export default function ItemSuggestions({ mediaType, tmdbId, userId = 1 }: ItemSuggestionsProps) {
  const [items, setItems] = useState<SimilarItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [isVisible, setIsVisible] = useState(false);
  const containerRef = React.useRef<HTMLDivElement>(null);

  // Intersection Observer for lazy loading
  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting && !isVisible) {
            setIsVisible(true);
          }
        });
      },
      {
        rootMargin: '200px', // Start loading 200px before component enters viewport
      }
    );

    if (containerRef.current) {
      observer.observe(containerRef.current);
    }

    return () => {
      if (containerRef.current) {
        observer.unobserve(containerRef.current);
      }
    };
  }, []);

  // Only load data when component becomes visible
  useEffect(() => {
    if (!isVisible) return;

    async function loadSimilar() {
      setLoading(true);
      try {
        const response = await apiGet(
          `/items/${mediaType}/${tmdbId}/similar?top_k=20&same_type_only=true&user_id=${userId}`
        );
        setItems(response.items || []);
      } catch (error) {
        console.error("Failed to load similar items:", error);
        setItems([]);
      } finally {
        setLoading(false);
      }
    }

    loadSimilar();
  }, [isVisible, mediaType, tmdbId, userId]);

  const handleItemClick = (item: SimilarItem) => {
    window.location.hash = `item/${item.media_type}/${item.tmdb_id}`;
  };

  // Show placeholder while waiting for visibility
  if (!isVisible) {
    return (
      <div ref={containerRef} className="mt-8 min-h-[200px]">
        <h2 className="text-2xl font-bold text-white mb-4">Similar {mediaType === 'movie' ? 'Movies' : 'Shows'}</h2>
      </div>
    );
  }

  if (loading) {
    return (
      <div ref={containerRef} className="mt-8">
        <h2 className="text-2xl font-bold text-white mb-4">Similar {mediaType === 'movie' ? 'Movies' : 'Shows'}</h2>
        <div className="text-white/60">Loading similar items...</div>
      </div>
    );
  }

  if (items.length === 0) {
    return null;
  }

  return (
    <div className="mt-8">
      <h2 className="text-2xl font-bold text-white mb-4">
        Similar {mediaType === 'movie' ? 'Movies' : 'Shows'}
      </h2>
      
      <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-4">
        {items.map((item) => (
          <div
            key={`${item.media_type}-${item.tmdb_id}`}
            className="group cursor-pointer"
            onClick={() => handleItemClick(item)}
          >
            {/* Poster */}
            <div className="relative aspect-[2/3] mb-2 rounded-lg overflow-hidden bg-white/10 border border-white/20 group-hover:ring-2 group-hover:ring-purple-500 transition-all">
              {item.poster_path ? (
                <img
                  src={`https://image.tmdb.org/t/p/w342${item.poster_path}`}
                  alt={item.title}
                  className="w-full h-full object-cover"
                  loading="lazy"
                />
              ) : (
                <div className="w-full h-full flex items-center justify-center text-white/40">
                  No Image
                </div>
              )}
              
              {/* Watched badge */}
              {item.is_watched && (
                <div className="absolute top-2 right-2 bg-emerald-500/90 text-white text-xs px-2 py-1 rounded-full">
                  ✓ Watched
                </div>
              )}
              
              {/* Similarity score badge */}
              <div className="absolute bottom-2 left-2 bg-black/80 text-white text-xs px-2 py-1 rounded-full">
                {Math.round(item.similarity_score * 100)}% match
              </div>
            </div>

            {/* Title */}
            <h3 className="text-white font-semibold text-sm line-clamp-2 group-hover:text-purple-300 transition-colors">
              {item.title}
            </h3>

            {/* Year and Rating */}
            <div className="flex items-center gap-2 text-xs text-white/60 mt-1">
              {item.year && <span>{item.year}</span>}
              {item.vote_average && (
                <>
                  <span>•</span>
                  <span className="flex items-center gap-1">
                    ⭐ {item.vote_average.toFixed(1)}
                  </span>
                </>
              )}
            </div>

            {/* User rating */}
            {item.user_rating && (
              <div className="mt-1">
                {item.user_rating === 1 ? (
                  <span className="text-emerald-400 text-xs">👍 Liked</span>
                ) : item.user_rating === -1 ? (
                  <span className="text-red-400 text-xs">👎 Disliked</span>
                ) : null}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
