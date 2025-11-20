import React, { useEffect, useState } from "react";
import { apiGet } from "../../api/client";

interface CollectionItem {
  tmdb_id: number;
  title: string;
  year?: number;
  poster_path?: string;
  release_date?: string;
  vote_average?: number;
  is_watched?: boolean;
}

interface ItemFranchiseProps {
  mediaType: string;
  tmdbId: number;
  userId?: number;
}

export default function ItemFranchise({ mediaType, tmdbId, userId = 1 }: ItemFranchiseProps) {
  const [collectionName, setCollectionName] = useState<string | null>(null);
  const [items, setItems] = useState<CollectionItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [isVisible, setIsVisible] = useState(false);
  const containerRef = React.useRef<HTMLDivElement>(null);

  // Intersection Observer for lazy loading
  useEffect(() => {
    // Skip for TV shows
    if (mediaType !== 'movie') return;

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
  }, [mediaType]);

  // Only load data when component becomes visible
  useEffect(() => {
    if (!isVisible || mediaType !== 'movie') return;

    async function loadCollection() {
      setLoading(true);
      try {
        const response = await apiGet(
          `/items/${mediaType}/${tmdbId}/collection?user_id=${userId}`
        );
        
        if (response.collection_name && response.items && response.items.length > 0) {
          setCollectionName(response.collection_name);
          setItems(response.items);
        } else {
          setCollectionName(null);
          setItems([]);
        }
      } catch (error) {
        console.error("Failed to load collection:", error);
        setCollectionName(null);
        setItems([]);
      } finally {
        setLoading(false);
      }
    }

    loadCollection();
  }, [isVisible, mediaType, tmdbId, userId]);

  const handleItemClick = (item: CollectionItem) => {
    window.location.hash = `item/movie/${item.tmdb_id}`;
  };

  // Show placeholder while waiting for visibility
  if (!isVisible && mediaType === 'movie') {
    return (
      <div ref={containerRef} className="mt-8 min-h-[200px]">
        {/* Placeholder for lazy loading */}
      </div>
    );
  }

  if (loading) {
    return (
      <div ref={containerRef} className="mt-8">
        <h2 className="text-2xl font-bold text-white mb-4">Collection</h2>
        <div className="text-white/60">Loading collection...</div>
      </div>
    );
  }

  if (!collectionName || items.length === 0) {
    return null;
  }

  return (
    <div ref={containerRef} className="mt-8">
      <h2 className="text-2xl font-bold text-white mb-4">
        {collectionName}
      </h2>
      
      <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-4">
        {items.map((item) => (
          <div
            key={item.tmdb_id}
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
              
              {/* Current item indicator */}
              {item.tmdb_id === tmdbId && (
                <div className="absolute bottom-2 left-2 bg-purple-500/90 text-white text-xs px-2 py-1 rounded-full">
                  Currently viewing
                </div>
              )}
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
          </div>
        ))}
      </div>
    </div>
  );
}
