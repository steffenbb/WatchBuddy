import React, { useState, useEffect } from "react";
import { TrendingUp, Star, Eye, Heart, Film, Tv, Calendar, Award } from "lucide-react";
import { api } from "../hooks/useApi";
import { apiPost } from "../api/client";
import { motion } from "framer-motion";
import AddToIndividualList from "./AddToIndividualList";

interface UserStats {
  total_ratings: number;
  average_rating: number;
  top_genres: Array<{ genre: string; count: number }>;
  movies_vs_shows: { movies: number; shows: number };
  recent_activity: Array<{
    title: string;
    media_type: string;
    rating: number;
    timestamp: string;
  }>;
  trakt_stats?: {
    movies_watched: number;
    shows_watched: number;
    ratings_count: number;
    top_genre: { genre: string; count: number } | null;
  };
}

interface OverviewSection {
  type: string;
  priority: number;
  data: any;
  computed_at: string;
  item_count: number;
}

interface OverviewResponse {
  sections: OverviewSection[];
  user_id: number;
  retrieved_at: string;
  message?: string;
  status?: string;
}

export default function UnifiedHome() {
  const [stats, setStats] = useState<UserStats | null>(null);
  const [overview, setOverview] = useState<OverviewResponse | null>(null);
  const [statsLoading, setStatsLoading] = useState(true);
  const [overviewLoading, setOverviewLoading] = useState(true);
  const [PhaseStrip, setPhaseStrip] = useState<React.ComponentType | null>(null);

  useEffect(() => {
    // Load stats
    async function loadStats() {
      try {
        setStatsLoading(true);
        const response = await api.get('/ratings/stats?user_id=1');
        setStats(response.data || response);
      } catch (e) {
        console.error('Failed to load stats:', e);
        setStats({
          total_ratings: 0,
          average_rating: 0,
          top_genres: [],
          movies_vs_shows: { movies: 0, shows: 0 },
          recent_activity: [],
          trakt_stats: {
            movies_watched: 0,
            shows_watched: 0,
            ratings_count: 0,
            top_genre: null
          }
        });
      } finally {
        setStatsLoading(false);
      }
    }

    // Load overview
    async function loadOverview() {
      try {
        setOverviewLoading(true);
        const data = await apiPost("/overview", { user_id: 1 }) as OverviewResponse;
        setOverview(data);
      } catch (err: any) {
        console.error('Failed to load overview:', err);
        setOverview(null);
      } finally {
        setOverviewLoading(false);
      }
    }

    loadStats();
    loadOverview();

    // Lazy-load PhaseStrip
    import('./phases/PhaseStrip').then(mod => setPhaseStrip(() => mod.default)).catch(() => setPhaseStrip(null));
  }, []);

  const triggerRefresh = async () => {
    try {
      await apiPost("/overview/refresh", { user_id: 1 });
      alert("Overview refresh queued. Check back in a few minutes!");
    } catch (err: any) {
      alert("Failed to trigger refresh: " + err.message);
    }
  };

  if (statsLoading && overviewLoading) {
    return (
      <div className="flex items-center justify-center min-h-[400px]">
        <div className="text-white/70">Loading your dashboard...</div>
      </div>
    );
  }

  const topGenres = stats?.top_genres || [];
  const totalRatings = stats?.total_ratings || 0;
  const avgRating = stats?.average_rating || 0;
  const moviesCount = stats?.movies_vs_shows?.movies || 0;
  const showsCount = stats?.movies_vs_shows?.shows || 0;
  
  // Trakt stats
  const traktMoviesWatched = stats?.trakt_stats?.movies_watched || 0;
  const traktShowsWatched = stats?.trakt_stats?.shows_watched || 0;
  const traktRatingsCount = stats?.trakt_stats?.ratings_count || 0;
  const traktTopGenre = stats?.trakt_stats?.top_genre;

  return (
    <div className="space-y-8 pb-8">
      {/* Welcome Header */}
      <motion.div
        initial={{ opacity: 0, y: -20 }}
        animate={{ opacity: 1, y: 0 }}
        className="text-center py-8"
      >
        <h1 className="text-4xl md:text-5xl font-bold text-white mb-3">
          Welcome to WatchBuddy
        </h1>
        <p className="text-white/70 text-lg">
          Your personalized movie and TV show recommendation platform
        </p>
      </motion.div>

      {/* Trakt Stats Grid */}
      <div>
        <h2 className="text-2xl font-bold text-white mb-4">Your Watch Stats</h2>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
          {/* Movies Watched */}
          <motion.div
            initial={{ opacity: 0, scale: 0.9 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ delay: 0.1 }}
            className="bg-white/5 backdrop-blur-sm border border-white/10 rounded-2xl p-6 hover:bg-white/10 transition-colors"
          >
            <div className="flex items-center justify-between mb-2">
              <Film className="text-blue-400" size={24} />
              <span className="text-white/50 text-sm">Movies</span>
            </div>
            <div className="text-3xl font-bold text-white">{traktMoviesWatched}</div>
            <div className="text-white/50 text-xs mt-1">Watched on Trakt</div>
          </motion.div>

          {/* Shows Watched */}
          <motion.div
            initial={{ opacity: 0, scale: 0.9 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ delay: 0.2 }}
            className="bg-white/5 backdrop-blur-sm border border-white/10 rounded-2xl p-6 hover:bg-white/10 transition-colors"
          >
            <div className="flex items-center justify-between mb-2">
              <Tv className="text-purple-400" size={24} />
              <span className="text-white/50 text-sm">Shows</span>
            </div>
            <div className="text-3xl font-bold text-white">{traktShowsWatched}</div>
            <div className="text-white/50 text-xs mt-1">Watched on Trakt</div>
          </motion.div>

          {/* Ratings Count */}
          <motion.div
            initial={{ opacity: 0, scale: 0.9 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ delay: 0.3 }}
            className="bg-white/5 backdrop-blur-sm border border-white/10 rounded-2xl p-6 hover:bg-white/10 transition-colors"
          >
            <div className="flex items-center justify-between mb-2">
              <Heart className="text-pink-400" size={24} />
              <span className="text-white/50 text-sm">Ratings</span>
            </div>
            <div className="text-3xl font-bold text-white">{totalRatings}</div>
            <div className="text-white/50 text-xs mt-1">Local thumbs up/down</div>
          </motion.div>

          {/* Top Genre */}
          <motion.div
            initial={{ opacity: 0, scale: 0.9 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ delay: 0.4 }}
            className="bg-white/5 backdrop-blur-sm border border-white/10 rounded-2xl p-6 hover:bg-white/10 transition-colors"
          >
            <div className="flex items-center justify-between mb-2">
              <Award className="text-emerald-400" size={24} />
              <span className="text-white/50 text-sm">Top Genre</span>
            </div>
            <div className="text-2xl font-bold text-white truncate">
              {traktTopGenre?.genre || topGenres[0]?.genre || 'N/A'}
            </div>
            <div className="text-white/50 text-xs mt-1">
              {traktTopGenre ? `${traktTopGenre.count} watched` : 'From your history'}
            </div>
          </motion.div>
        </div>
      </div>

      {/* Phase Strip */}
      {PhaseStrip && <PhaseStrip />}

      {/* Overview Sections */}
      {!overviewLoading && overview && overview.sections && overview.sections.length > 0 ? (
        <div className="space-y-8">
          <div className="flex justify-between items-center">
            <h2 className="text-3xl font-bold text-white">Personalized Recommendations</h2>
            <button
              onClick={triggerRefresh}
              className="px-4 py-2 bg-white/10 hover:bg-white/20 text-white rounded-lg text-sm border border-white/20 transition-colors"
            >
              Refresh Now
            </button>
          </div>

          {overview.sections.map((section, idx) => (
            <OverviewModule key={`${section.type}-${idx}`} section={section} />
          ))}
        </div>
      ) : !overviewLoading ? (
        <div className="bg-white/5 border border-white/10 rounded-2xl p-8 text-center">
          <h3 className="text-2xl font-bold text-white mb-4">Your Overview is Being Prepared</h3>
          <p className="text-white/60 mb-6">
            Your personalized recommendations will be computed nightly. Check back tomorrow!
          </p>
          <button
            onClick={triggerRefresh}
            className="px-6 py-3 bg-purple-600 hover:bg-purple-700 text-white rounded-lg font-medium transition-colors"
          >
            Compute Now
          </button>
        </div>
      ) : null}
    </div>
  );
}

function OverviewModule({ section }: { section: OverviewSection }) {
  const moduleTitle = {
    new_shows: "🆕 New Shows & Movies for You",
    trending: "🔥 Trending Now for You",
    upcoming: "📅 Coming Soon You'll Love",
    investment_tracker: "🎬 Your Investment Tracker"
  }[section.type] || section.type;

  const items = section.data?.items || [];
  if (items.length === 0) return null;

  return (
    <div className="bg-white/5 border border-white/10 rounded-2xl p-6">
      <h2 className="text-2xl font-bold text-white mb-4">{moduleTitle}</h2>
      
      <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-4">
        {items.slice(0, 12).map((item: any) => (
          <div
            key={`${item.media_type}-${item.tmdb_id}`}
            className="group cursor-pointer"
            onClick={() => {
              if (item.tmdb_id) {
                window.location.hash = `item/${item.media_type}/${item.tmdb_id}`;
              }
            }}
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
                <div className="w-full h-full flex items-center justify-center text-white/40">
                  No Image
                </div>
              )}
              
              {item.score && (
                <div className="absolute top-2 left-2 bg-black/80 text-white text-xs px-2 py-1 rounded-full">
                  {Math.round(item.score * 100)}% match
                </div>
              )}

              {item.release_badge && (
                <div className="absolute top-2 right-2 bg-purple-500/90 text-white text-xs px-2 py-1 rounded-full">
                  {item.release_badge}
                </div>
              )}

              {item.trending_badge && (
                <div className="absolute top-2 right-2 bg-red-500/90 text-white text-xs px-2 py-1 rounded-full">
                  🔥 {item.trending_badge}
                </div>
              )}
            </div>

            <h3 className="text-white font-semibold text-sm line-clamp-2 group-hover:text-purple-300 transition-colors">
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
    </div>
  );
}
