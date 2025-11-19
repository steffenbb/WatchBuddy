import React, { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Star, Calendar, Film, Tv } from 'lucide-react';

interface MediaInfo {
  title?: string | null;
  overview?: string | null;
  vote_average?: number | null;
  release_date?: string | null;
  first_air_date?: string | null;
  media_type?: string | null;
  genres?: string[] | null;
  runtime?: number | null;
}

interface HoverInfoCardProps {
  tmdbId?: number | null;
  traktId?: number | null;
  mediaType?: string | null;
  children: React.ReactNode;
  fallbackInfo?: MediaInfo; // Use if no IDs available
}

/**
 * HoverInfoCard: Shows detailed movie/show info on hover
 * Fetches metadata from backend and displays in a tooltip-style card
 */
export default function HoverInfoCard({ 
  tmdbId, 
  traktId, 
  mediaType, 
  children,
  fallbackInfo 
}: HoverInfoCardProps) {
  const [isHovering, setIsHovering] = useState(false);
  const [info, setInfo] = useState<MediaInfo | null>(null);
  const [loading, setLoading] = useState(false);
  const [position, setPosition] = useState({ x: 0, y: 0 });

  // Fetch metadata when hovering starts
  useEffect(() => {
    if (!isHovering) return;
    
    // If no IDs, use fallback info immediately
    if (!tmdbId && !traktId) {
      if (fallbackInfo && Object.keys(fallbackInfo).length > 0) {
        setInfo(fallbackInfo);
      }
      return;
    }

    // Always fetch from API if we have IDs (even if fallbackInfo exists)
    let mounted = true;
    const fetchInfo = async () => {
      setLoading(true);
      try {
        // Try TMDB first (faster, more complete)
        if (tmdbId && mediaType) {
          const response = await fetch(
            `/api/metadata/tmdb/${mediaType}/${tmdbId}?user_id=1`
          );
          if (response.ok) {
            const data = await response.json();
            if (mounted && data && Object.keys(data).length > 0) {
              setInfo(data);
            } else if (fallbackInfo && Object.keys(fallbackInfo).length > 0) {
              // Use fallback if API returned empty data
              if (mounted) setInfo(fallbackInfo);
            }
          } else {
            console.debug(`TMDB API fetch failed for ${mediaType}/${tmdbId}:`, response.status);
            if (fallbackInfo && Object.keys(fallbackInfo).length > 0) {
              // Use fallback if API fetch failed
              if (mounted) setInfo(fallbackInfo);
            }
          }
        } else if (traktId) {
          // Fallback to Trakt
          const response = await fetch(
            `/api/metadata/trakt/${traktId}?user_id=1`
          );
          if (response.ok) {
            const data = await response.json();
            if (mounted && data && Object.keys(data).length > 0) {
              setInfo(data);
            } else if (fallbackInfo && Object.keys(fallbackInfo).length > 0) {
              // Use fallback if API returned empty data
              if (mounted) setInfo(fallbackInfo);
            }
          } else {
            console.debug(`Trakt API fetch failed for ${traktId}:`, response.status);
            if (fallbackInfo && Object.keys(fallbackInfo).length > 0) {
              // Use fallback if API fetch failed
              if (mounted) setInfo(fallbackInfo);
            }
          }
        }
      } catch (error) {
        console.debug('Failed to fetch hover info:', error);
        // Use fallback on error
        if (fallbackInfo && Object.keys(fallbackInfo).length > 0) {
          if (mounted) setInfo(fallbackInfo);
        }
      } finally {
        if (mounted) setLoading(false);
      }
    };

    // Debounce fetch by 300ms to avoid excessive requests
    const timer = setTimeout(fetchInfo, 300);
    return () => {
      mounted = false;
      clearTimeout(timer);
    };
  }, [isHovering, tmdbId, traktId, mediaType, fallbackInfo]);

  const handleMouseEnter = (e: React.MouseEvent) => {
    setIsHovering(true);
    updatePosition(e);
  };

  const handleMouseMove = (e: React.MouseEvent) => {
    updatePosition(e);
  };

  const handleMouseLeave = () => {
    setIsHovering(false);
    setInfo(null); // Clear for next hover
  };

  const updatePosition = (e: React.MouseEvent) => {
    const rect = e.currentTarget.getBoundingClientRect();
    
    // Card dimensions (approximate)
    const cardWidth = 384; // max-w-sm = 24rem = 384px
    const cardHeight = 300; // approximate height
    const padding = 16; // safety padding
    
    // Viewport dimensions
    const viewportWidth = window.innerWidth;
    const viewportHeight = window.innerHeight;
    
    // Calculate initial position (centered below element)
    let x = rect.left + rect.width / 2;
    let y = rect.bottom + 10;
    
    // Adjust horizontal position to keep card in viewport
    const cardLeft = x - cardWidth / 2;
    const cardRight = x + cardWidth / 2;
    
    if (cardLeft < padding) {
      // Too far left, align to left edge
      x = cardWidth / 2 + padding;
    } else if (cardRight > viewportWidth - padding) {
      // Too far right, align to right edge
      x = viewportWidth - cardWidth / 2 - padding;
    }
    
    // Adjust vertical position if card extends below viewport
    if (y + cardHeight > viewportHeight - padding) {
      // Position above element instead
      y = rect.top - 10;
    }
    
    setPosition({ x, y });
  };

  const formatDate = (date: string | null | undefined) => {
    if (!date) return 'N/A';
    try {
      return new Date(date).getFullYear().toString();
    } catch {
      return 'N/A';
    }
  };

  const formatRuntime = (minutes: number | null | undefined) => {
    if (!minutes) return null;
    const hours = Math.floor(minutes / 60);
    const mins = minutes % 60;
    if (hours > 0) {
      return `${hours}h ${mins}m`;
    }
    return `${mins}m`;
  };

  return (
    <div
      onMouseEnter={handleMouseEnter}
      onMouseMove={handleMouseMove}
      onMouseLeave={handleMouseLeave}
      className="relative"
    >
      {children}
      
      <AnimatePresence>
        {isHovering && (info || loading) && (
          <motion.div
            initial={{ opacity: 0, y: 10, scale: 0.95 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 10, scale: 0.95 }}
            transition={{ duration: 0.15 }}
            className="fixed z-[100] pointer-events-none"
            style={{
              left: `${position.x}px`,
              top: `${position.y}px`,
              transform: position.y < (typeof window !== 'undefined' ? window.innerHeight / 2 : 400)
                ? 'translate(-50%, 0)' 
                : 'translate(-50%, -100%)',
            }}
          >
            <div className="bg-black/95 backdrop-blur-xl border border-white/20 rounded-xl shadow-2xl p-4 max-w-[calc(100vw-2rem)] sm:max-w-sm">
              {loading ? (
                <div className="flex items-center gap-2 text-white/70">
                  <div className="animate-spin h-4 w-4 border-2 border-white/30 border-t-white rounded-full" />
                  <span className="text-sm">Loading...</span>
                </div>
              ) : info ? (
                <>
                  {/* Title */}
                  <h4 className="text-white font-bold text-base mb-2 line-clamp-2">
                    {info.title || 'Unknown Title'}
                  </h4>
                  
                  {/* Metadata badges */}
                  <div className="flex items-center gap-2 mb-3 flex-wrap">
                    {/* Rating */}
                    {info.vote_average !== null && info.vote_average !== undefined && (
                      <div className="flex items-center gap-1 px-2 py-1 bg-yellow-500/20 border border-yellow-400/30 rounded-lg">
                        <Star size={12} className="text-yellow-400 fill-yellow-400" />
                        <span className="text-white text-xs font-semibold">
                          {info.vote_average.toFixed(1)}
                        </span>
                      </div>
                    )}
                    
                    {/* Year */}
                    {(info.release_date || info.first_air_date) && (
                      <div className="flex items-center gap-1 px-2 py-1 bg-blue-500/20 border border-blue-400/30 rounded-lg">
                        <Calendar size={12} className="text-blue-400" />
                        <span className="text-white text-xs">
                          {formatDate(info.release_date || info.first_air_date)}
                        </span>
                      </div>
                    )}
                    
                    {/* Media Type */}
                    {info.media_type && (
                      <div className="flex items-center gap-1 px-2 py-1 bg-purple-500/20 border border-purple-400/30 rounded-lg">
                        {info.media_type === 'movie' ? (
                          <Film size={12} className="text-purple-400" />
                        ) : (
                          <Tv size={12} className="text-purple-400" />
                        )}
                        <span className="text-white text-xs capitalize">
                          {info.media_type}
                        </span>
                      </div>
                    )}
                    
                    {/* Runtime */}
                    {info.runtime && (
                      <div className="px-2 py-1 bg-gray-500/20 border border-gray-400/30 rounded-lg">
                        <span className="text-white text-xs">
                          {formatRuntime(info.runtime)}
                        </span>
                      </div>
                    )}
                  </div>
                  
                  {/* Genres */}
                  {info.genres && info.genres.length > 0 && (
                    <div className="flex flex-wrap gap-1 mb-3">
                      {info.genres.slice(0, 3).map((genre, i) => (
                        <span
                          key={i}
                          className="px-2 py-0.5 bg-emerald-500/20 border border-emerald-400/30 rounded-full text-emerald-300 text-xs"
                        >
                          {genre}
                        </span>
                      ))}
                    </div>
                  )}
                  
                  {/* Overview */}
                  {info.overview && (
                    <p className="text-white/80 text-xs leading-relaxed line-clamp-4">
                      {info.overview}
                    </p>
                  )}
                </>
              ) : null}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
