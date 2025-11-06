import React, { useState, useEffect } from "react";
import { TrendingUp, Star, Eye, Heart, Film, Tv, Calendar, Award } from "lucide-react";
import { api } from "../hooks/useApi";
import { motion } from "framer-motion";

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

export default function HomePage() {
  const [stats, setStats] = useState<UserStats | null>(null);
  const [loading, setLoading] = useState(true);
  // Phases
  const [PhaseStrip, setPhaseStrip] = useState<React.ComponentType | null>(null);

  useEffect(() => {
    async function loadStats() {
      try {
        setLoading(true);
        // Try to fetch user statistics
        const response = await api.get('/ratings/stats?user_id=1');
        setStats(response.data || response);
      } catch (e) {
        console.error('Failed to load stats:', e);
        // Use mock data for now
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
        setLoading(false);
      }
    }
    loadStats();
    // Lazy-load PhaseStrip to keep initial bundle small
    import('./phases/PhaseStrip').then(mod => setPhaseStrip(() => mod.default)).catch(() => setPhaseStrip(null));
  }, []);

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-[400px]">
        <div className="text-white/70">Loading your profile...</div>
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
    <div className="space-y-6">
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

      {/* Stats Grid - Trakt Stats */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        {/* Movies Watched on Trakt */}
        <motion.div
          initial={{ opacity: 0, scale: 0.9 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ delay: 0.1 }}
          className="bg-white/5 backdrop-blur-sm border border-white/10 rounded-2xl p-6 hover:bg-white/10 transition-colors"
        >
          <div className="flex items-center justify-between mb-2">
            <Film className="text-blue-400" size={24} />
            <span className="text-white/50 text-sm">Movies Watched</span>
          </div>
          <div className="text-3xl font-bold text-white">{traktMoviesWatched}</div>
          <div className="text-white/50 text-xs mt-1">On Trakt</div>
        </motion.div>

        {/* Shows Watched on Trakt */}
        <motion.div
          initial={{ opacity: 0, scale: 0.9 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ delay: 0.2 }}
          className="bg-white/5 backdrop-blur-sm border border-white/10 rounded-2xl p-6 hover:bg-white/10 transition-colors"
        >
          <div className="flex items-center justify-between mb-2">
            <Tv className="text-purple-400" size={24} />
            <span className="text-white/50 text-sm">Shows Watched</span>
          </div>
          <div className="text-3xl font-bold text-white">{traktShowsWatched}</div>
          <div className="text-white/50 text-xs mt-1">On Trakt</div>
        </motion.div>

        {/* Trakt Ratings Count */}
        <motion.div
          initial={{ opacity: 0, scale: 0.9 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ delay: 0.3 }}
          className="bg-white/5 backdrop-blur-sm border border-white/10 rounded-2xl p-6 hover:bg-white/10 transition-colors"
        >
          <div className="flex items-center justify-between mb-2">
            <Star className="text-yellow-400" size={24} />
            <span className="text-white/50 text-sm">Trakt Ratings</span>
          </div>
          <div className="text-3xl font-bold text-white">{traktRatingsCount}</div>
          <div className="text-white/50 text-xs mt-1">All time on Trakt</div>
        </motion.div>

        {/* Top Genre on Trakt */}
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
            {traktTopGenre?.genre || 'N/A'}
          </div>
          <div className="text-white/50 text-xs mt-1">
            {traktTopGenre ? `${traktTopGenre.count} watched` : 'On Trakt'}
          </div>
        </motion.div>
      </div>

      {/* Local WatchBuddy Stats Header */}
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: 0.5 }}
        className="pt-4"
      >
        <h2 className="text-2xl font-bold text-white mb-4">Your WatchBuddy Activity</h2>
      </motion.div>

      {/* Local Stats Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {/* Total Ratings */}
        <motion.div
          initial={{ opacity: 0, scale: 0.9 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ delay: 0.6 }}
          className="bg-white/5 backdrop-blur-sm border border-white/10 rounded-2xl p-6 hover:bg-white/10 transition-colors"
        >
          <div className="flex items-center justify-between mb-2">
            <Heart className="text-pink-400" size={24} />
            <span className="text-white/50 text-sm">Local Ratings</span>
          </div>
          <div className="text-3xl font-bold text-white">{totalRatings}</div>
          <div className="text-white/50 text-xs mt-1">Thumbs up/down</div>
        </motion.div>

        {/* Movies Rated */}
        <motion.div
          initial={{ opacity: 0, scale: 0.9 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ delay: 0.7 }}
          className="bg-white/5 backdrop-blur-sm border border-white/10 rounded-2xl p-6 hover:bg-white/10 transition-colors"
        >
          <div className="flex items-center justify-between mb-2">
            <Film className="text-blue-300" size={24} />
            <span className="text-white/50 text-sm">Movies Rated</span>
          </div>
          <div className="text-3xl font-bold text-white">{moviesCount}</div>
          <div className="text-white/60 text-sm mt-1">
            {avgRating > 0 ? `${avgRating.toFixed(1)} avg` : 'No ratings yet'}
          </div>
        </motion.div>

        {/* Movies */}
        <motion.div
          initial={{ opacity: 0, scale: 0.9 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ delay: 0.2 }}
          className="bg-white/5 backdrop-blur-sm border border-white/10 rounded-2xl p-6 hover:bg-white/10 transition-colors"
        >
          <div className="flex items-center justify-between mb-2">
            <Film className="text-purple-400" size={24} />
            <span className="text-white/50 text-sm">Movies</span>
          </div>
          <div className="text-3xl font-bold text-white">{moviesCount}</div>
          <div className="text-white/60 text-sm mt-1">
            {totalRatings > 0 ? `${Math.round((moviesCount / totalRatings) * 100)}% of ratings` : 'Start rating'}
          </div>
        </motion.div>

        {/* Shows */}
        <motion.div
          initial={{ opacity: 0, scale: 0.9 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ delay: 0.3 }}
          className="bg-white/5 backdrop-blur-sm border border-white/10 rounded-2xl p-6 hover:bg-white/10 transition-colors"
        >
          <div className="flex items-center justify-between mb-2">
            <Tv className="text-blue-400" size={24} />
            <span className="text-white/50 text-sm">TV Shows</span>
          </div>
          <div className="text-3xl font-bold text-white">{showsCount}</div>
          <div className="text-white/60 text-sm mt-1">
            {totalRatings > 0 ? `${Math.round((showsCount / totalRatings) * 100)}% of ratings` : 'Start rating'}
          </div>
        </motion.div>

        {/* Top Genre */}
        <motion.div
          initial={{ opacity: 0, scale: 0.9 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ delay: 0.4 }}
          className="bg-white/5 backdrop-blur-sm border border-white/10 rounded-2xl p-6 hover:bg-white/10 transition-colors"
        >
          <div className="flex items-center justify-between mb-2">
            <Award className="text-pink-400" size={24} />
            <span className="text-white/50 text-sm">Top Genre</span>
          </div>
          <div className="text-2xl font-bold text-white truncate">
            {topGenres[0]?.genre || 'No data yet'}
          </div>
          <div className="text-white/60 text-sm mt-1">
            {topGenres[0]?.count ? `${topGenres[0].count} items` : 'Start exploring'}
          </div>
        </motion.div>
      </div>

      {/* Top Genres Section */}
      {topGenres.length > 0 && (
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.5 }}
          className="bg-white/5 backdrop-blur-sm border border-white/10 rounded-2xl p-6"
        >
          <h2 className="text-2xl font-bold text-white mb-4 flex items-center gap-2">
            <TrendingUp size={24} className="text-purple-400" />
            Your Favorite Genres
          </h2>
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3">
            {topGenres.slice(0, 10).map((genre, idx) => (
              <div
                key={idx}
                className="bg-white/5 border border-white/10 rounded-lg px-4 py-3 text-center hover:bg-white/10 transition-colors"
              >
                <div className="text-white font-semibold text-sm">{genre.genre}</div>
                <div className="text-white/60 text-xs mt-1">{genre.count} items</div>
              </div>
            ))}
          </div>
        </motion.div>
      )}

      {/* Phase Strip */}
      {PhaseStrip && (
        <motion.div initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }} className="space-y-3">
          <div className="text-center">
            <h2 className="text-2xl font-bold text-white mb-2 flex items-center justify-center gap-2">
              <span>🎬</span>
              <span>Your Viewing Phases</span>
            </h2>
            <p className="text-white/70 text-sm max-w-2xl mx-auto">
              We've analyzed your watch history and identified distinct phases in your viewing patterns. 
              Each phase represents a period where you explored similar themes, genres, or franchises. 
              See what you're currently into and predict what's coming next!
            </p>
            <div className="mt-3">
              <a href="#timeline" className="inline-flex items-center px-4 py-2 rounded-lg bg-white/10 hover:bg-white/20 border border-white/10 text-white text-sm">
                View timeline
              </a>
            </div>
          </div>
          {/* @ts-ignore dynamic component */}
          <PhaseStrip />
        </motion.div>
      )}

      {/* Getting Started Section */}
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.6 }}
        className="bg-gradient-to-r from-purple-500/10 to-pink-500/10 backdrop-blur-sm border border-white/10 rounded-2xl p-8"
      >
        <h2 className="text-2xl font-bold text-white mb-4">
          {totalRatings > 0 ? 'Keep Exploring' : 'Get Started'}
        </h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <a
            href="#"
            onClick={(e) => { e.preventDefault(); window.location.hash = ''; }}
            className="bg-white/5 border border-white/10 rounded-xl p-6 hover:bg-white/10 transition-colors group"
          >
            <div className="w-12 h-12 bg-purple-500/20 rounded-lg flex items-center justify-center mb-3 group-hover:scale-110 transition-transform">
              <Film size={24} className="text-purple-400" />
            </div>
            <h3 className="text-white font-semibold mb-2">Smart Lists</h3>
            <p className="text-white/60 text-sm">
              Create personalized recommendation lists based on your preferences
            </p>
          </a>

          <a
            href="#dynamic"
            className="bg-white/5 border border-white/10 rounded-xl p-6 hover:bg-white/10 transition-colors group"
          >
            <div className="w-12 h-12 bg-pink-500/20 rounded-lg flex items-center justify-center mb-3 group-hover:scale-110 transition-transform">
              <Star size={24} className="text-pink-400" />
            </div>
            <h3 className="text-white font-semibold mb-2">AI Lists</h3>
            <p className="text-white/60 text-sm">
              Get AI-powered recommendations tailored to your mood and taste
            </p>
          </a>

          <a
            href="#myLists"
            className="bg-white/5 border border-white/10 rounded-xl p-6 hover:bg-white/10 transition-colors group"
          >
            <div className="w-12 h-12 bg-blue-500/20 rounded-lg flex items-center justify-center mb-3 group-hover:scale-110 transition-transform">
              <Heart size={24} className="text-blue-400" />
            </div>
            <h3 className="text-white font-semibold mb-2">My Lists</h3>
            <p className="text-white/60 text-sm">
              Organize your favorite content into custom collections
            </p>
          </a>
        </div>
      </motion.div>

      {/* Quick Tip */}
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: 0.7 }}
        className="text-center text-white/50 text-sm"
      >
        💡 Tip: The more you rate and organize content, the better your recommendations become!
      </motion.div>
    </div>
  );
}
