import React, { useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import { 
  Calendar, Clock, Star, Film, Tv, Play, 
  ThumbsUp, ThumbsDown, Check, ArrowLeft
} from 'lucide-react';
import { apiGet, apiPost } from '../../api/client';
import AddToIndividualList from '../AddToIndividualList';
import ItemSuggestions from './ItemSuggestions';
import ItemFranchise from './ItemFranchise';
import { toast } from '../../utils/toast';

interface ItemDetails {
  tmdb_id: number;
  trakt_id: number | null;
  media_type: string;
  title: string;
  original_title: string;
  year: number;
  release_date: string;
  overview: string;
  tagline: string;
  poster_path: string;
  backdrop_path: string;
  genres: string[];
  keywords: string[];
  cast: string[];
  vote_average: number;
  vote_count: number;
  popularity: number;
  runtime: number;
  language: string;
  status: string;
  homepage: string;
  budget: number;
  revenue: number;
  production_companies: string[];
  number_of_seasons?: number;
  number_of_episodes?: number;
  first_air_date?: string;
  last_air_date?: string;
  episode_run_time?: number[];
  in_production?: boolean;
  networks?: string[];
  obscurity_score?: number;
  mainstream_score?: number;
  freshness_score?: number;
  watched: boolean;
  watched_at: string | null;
  user_rating: number | null;
  trailer_url: string | null;
  last_refreshed: string | null;
}

interface ItemPageProps {
  mediaType: string;
  tmdbId: number;
  onBack?: () => void;
}

export default function ItemPage({ mediaType, tmdbId, onBack }: ItemPageProps) {
  const [item, setItem] = useState<ItemDetails | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showTrailer, setShowTrailer] = useState(false);
  const [ratingInProgress, setRatingInProgress] = useState(false);
  const [aiRationale, setAiRationale] = useState<string>('');
  const [rationaleLoading, setRationaleLoading] = useState(false);
  const [trailers, setTrailers] = useState<any[]>([]);
  const [trailersLoading, setTrailersLoading] = useState(false);

  useEffect(() => {
    // Reset state when navigating to a different item
    setItem(null);
    setAiRationale('');
    setTrailers([]);
    setShowTrailer(false);
    setError(null);
    
    // Only load essential item data immediately
    loadItem();
    // Defer heavy operations - they'll be triggered by scroll/visibility
  }, [mediaType, tmdbId]);

  // Lazy load AI rationale after main content loads (non-blocking)
  useEffect(() => {
    if (!item || aiRationale || rationaleLoading) return;
    
    const rationaleTimer = setTimeout(() => {
      loadRationale();
    }, 1000); // Delay by 1s to prioritize main content rendering
    
    return () => clearTimeout(rationaleTimer);
  }, [item]);

  // Lazy load trailers when user hovers over or clicks trailer button
  const handleTrailerButtonClick = () => {
    if (trailers.length === 0 && !trailersLoading) {
      loadTrailers();
    }
    setShowTrailer(true);
  };

  const loadRationale = async () => {
    setRationaleLoading(true);
    try {
      // Use 60 second timeout for Ollama LLM generation
      const data = await apiGet(`/items/${mediaType}/${tmdbId}/rationale?user_id=1`, 60000) as { rationale: string };
      setAiRationale(data.rationale);
    } catch (err: any) {
      console.error('Failed to load AI rationale:', err);
      // Silently fail - rationale is optional
    } finally {
      setRationaleLoading(false);
    }
  };

  const loadTrailers = async () => {
    setTrailersLoading(true);
    try {
      const data = await apiGet(`/items/${mediaType}/${tmdbId}/trailers`) as { videos: any[] };
      setTrailers(data.videos || []);
    } catch (err: any) {
      console.error('Failed to load trailers:', err);
      setTrailers([]);
    } finally {
      setTrailersLoading(false);
    }
  };

  const loadItem = async () => {
    setLoading(true);
    setError(null);
    
    try {
      const data = await apiGet(`/items/${mediaType}/${tmdbId}?user_id=1`) as ItemDetails;
      setItem(data);
    } catch (err: any) {
      setError(err.message || 'Failed to load item details');
    } finally {
      setLoading(false);
    }
  };

  const handleRate = async (rating: number) => {
    if (!item?.trakt_id) {
      toast.error('Cannot rate item without Trakt ID');
      return;
    }

    setRatingInProgress(true);
    try {
      const newRating = item.user_rating === rating ? 0 : rating;
      
      await apiPost('/ratings/rate', {
        user_id: 1,
        trakt_id: item.trakt_id,
        media_type: item.media_type,
        rating: newRating
      });
      
      setItem(prev => prev ? { ...prev, user_rating: newRating } : null);
      
      if (newRating === 0) {
        toast.info('Rating removed');
      } else {
        toast.success(newRating === 1 ? 'Rated thumbs up!' : 'Rated thumbs down');
      }
    } catch (err: any) {
      toast.error('Failed to save rating');
    } finally {
      setRatingInProgress(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="text-center">
          <div className="animate-spin rounded-full h-12 w-12 border-t-2 border-b-2 border-purple-500 mx-auto mb-4"></div>
          <p className="text-gray-300">Loading details...</p>
        </div>
      </div>
    );
  }

  if (error || !item) {
    return (
      <div className="p-8 max-w-4xl mx-auto">
        <div className="bg-red-900/20 border border-red-500 rounded-lg p-6">
          <h2 className="text-xl font-bold text-red-400 mb-2">Error</h2>
          <p className="text-gray-300">{error || 'Item not found'}</p>
          <button
            onClick={() => window.history.back()}
            className="mt-4 px-4 py-2 bg-gray-700 hover:bg-gray-600 text-white rounded"
          >
            Go Back
          </button>
        </div>
      </div>
    );
  }

  const posterUrl = item.poster_path 
    ? `https://image.tmdb.org/t/p/w500${item.poster_path}`
    : null;
  
  const backdropUrl = item.backdrop_path
    ? `https://image.tmdb.org/t/p/original${item.backdrop_path}`
    : null;

  return (
    <div className="min-h-screen bg-gradient-to-br from-indigo-950 via-purple-950 to-fuchsia-950">
      {/* Back Button */}
      <div className="max-w-7xl mx-auto px-6 pt-4">
        <button
          onClick={() => window.history.back()}
          className="flex items-center gap-2 text-gray-300 hover:text-white transition-colors"
        >
          <ArrowLeft className="w-5 h-5" />
          Back
        </button>
      </div>

      {/* Backdrop Header */}
      {backdropUrl && (
        <div className="relative h-96 overflow-hidden">
          <img 
            src={backdropUrl} 
            alt={item.title}
            className="w-full h-full object-cover opacity-40"
          />
          <div className="absolute inset-0 bg-gradient-to-t from-indigo-950 via-purple-950/80 to-transparent" />
        </div>
      )}

      {/* Main Content */}
      <div className="max-w-7xl mx-auto px-6 -mt-64 relative z-10">
        {/* Header Section - IMDb Style */}
        <div className="flex flex-col lg:flex-row gap-8 mb-8">
          {/* Left Column: Poster */}
          <div className="flex-shrink-0 w-full lg:w-80">
            {posterUrl && (
              <motion.div
                initial={{ opacity: 0, scale: 0.9 }}
                animate={{ opacity: 1, scale: 1 }}
              >
                <img
                  src={posterUrl}
                  alt={item.title}
                  className="w-full rounded-xl shadow-2xl border-4 border-white/10"
                />
              </motion.div>
            )}
            
            {/* Tagline Card */}
            {item.tagline && (
              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                transition={{ delay: 0.2 }}
                className="mt-4 bg-gradient-to-r from-purple-900/40 to-indigo-900/40 p-4 rounded-lg border border-purple-500/30"
              >
                <p className="text-sm italic text-purple-200 text-center leading-relaxed">
                  "{item.tagline}"
                </p>
              </motion.div>
            )}

            {/* Quick Info Sidebar */}
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ delay: 0.3 }}
              className="mt-6 bg-gray-900/60 rounded-lg p-5 border border-gray-700 space-y-4"
            >
              {/* Rating Score - Prominent */}
              <div className="text-center pb-4 border-b border-gray-700">
                <div className="flex items-center justify-center gap-2 mb-2">
                  <Star className="w-8 h-8 fill-yellow-400 text-yellow-400" />
                  <span className="text-4xl font-bold text-white">
                    {item.vote_average?.toFixed(1) || 'N/A'}
                  </span>
                  <span className="text-xl text-gray-400">/10</span>
                </div>
                <p className="text-sm text-gray-400">{item.vote_count?.toLocaleString()} votes</p>
              </div>

              {/* Quick Stats */}
              <div className="space-y-3 text-sm">
                {item.year && (
                  <div className="flex justify-between">
                    <span className="text-gray-400">Year</span>
                    <span className="text-white font-medium">{item.year}</span>
                  </div>
                )}
                
                {item.runtime && (
                  <div className="flex justify-between">
                    <span className="text-gray-400">Runtime</span>
                    <span className="text-white font-medium">
                      {Math.floor(item.runtime / 60)}h {item.runtime % 60}m
                    </span>
                  </div>
                )}

                {item.media_type === 'tv' && item.episode_run_time && item.episode_run_time.length > 0 && (
                  <div className="flex justify-between">
                    <span className="text-gray-400">Episode</span>
                    <span className="text-white font-medium">{item.episode_run_time[0]}min</span>
                  </div>
                )}

                {item.status && (
                  <div className="flex justify-between">
                    <span className="text-gray-400">Status</span>
                    <span className={`font-medium ${
                      item.status === 'Released' || item.status === 'Ended' 
                        ? 'text-green-300' 
                        : item.in_production 
                        ? 'text-blue-300' 
                        : 'text-gray-300'
                    }`}>
                      {item.status}
                    </span>
                  </div>
                )}

                {item.language && (
                  <div className="flex justify-between">
                    <span className="text-gray-400">Language</span>
                    <span className="text-white font-medium">{item.language.toUpperCase()}</span>
                  </div>
                )}

                {item.media_type === 'tv' && item.number_of_seasons && (
                  <div className="flex justify-between">
                    <span className="text-gray-400">Seasons</span>
                    <span className="text-white font-medium">{item.number_of_seasons}</span>
                  </div>
                )}

                {item.media_type === 'tv' && item.number_of_episodes && (
                  <div className="flex justify-between">
                    <span className="text-gray-400">Episodes</span>
                    <span className="text-white font-medium">{item.number_of_episodes}</span>
                  </div>
                )}
              </div>
            </motion.div>
          </div>

          {/* Right Column: Main Content */}
          <div className="flex-1 pt-0 lg:pt-32">
            <motion.div
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
            >
              {/* Title */}
              <h1 className="text-4xl md:text-5xl font-bold text-white mb-2">
                {item.title}
              </h1>
              
              {item.original_title && item.original_title !== item.title && (
                <p className="text-lg text-gray-400 mb-4">
                  Original Title: <span className="italic">{item.original_title}</span>
                </p>
              )}

              {/* Genres & Watched Status */}
              <div className="flex flex-wrap items-center gap-2 mb-6">
                {item.genres.map(genre => (
                  <span
                    key={genre}
                    className="px-3 py-1 bg-purple-600/30 text-purple-200 rounded-full text-sm border border-purple-500/50"
                  >
                    {genre}
                  </span>
                ))}
                {item.watched && (
                  <span className="px-3 py-1 bg-green-600/30 text-green-300 rounded-full text-sm border border-green-500/50 ml-2">
                    <Check className="w-4 h-4 inline mr-1" />
                    Watched
                  </span>
                )}
              </div>

              {/* Action Buttons */}
              <div className="flex flex-wrap gap-3 mb-8">
                {/* Rate Buttons */}
                {item.trakt_id && (
                  <>
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        handleRate(1);
                      }}
                      disabled={ratingInProgress}
                      className={`px-6 py-3 rounded-lg border-2 transition-all font-medium ${
                        item.user_rating === 1
                          ? 'bg-green-600 border-green-500 text-white shadow-lg shadow-green-500/30'
                          : 'bg-gray-800/50 border-gray-600 text-gray-300 hover:border-green-500 hover:bg-gray-800'
                      }`}
                    >
                      <ThumbsUp className="w-5 h-5 inline mr-2" />
                      {item.user_rating === 1 ? 'Liked' : 'Like'}
                    </button>

                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        handleRate(-1);
                      }}
                      disabled={ratingInProgress}
                      className={`px-6 py-3 rounded-lg border-2 transition-all font-medium ${
                        item.user_rating === -1
                          ? 'bg-red-600 border-red-500 text-white shadow-lg shadow-red-500/30'
                          : 'bg-gray-800/50 border-gray-600 text-gray-300 hover:border-red-500 hover:bg-gray-800'
                      }`}
                    >
                      <ThumbsDown className="w-5 h-5 inline mr-2" />
                      {item.user_rating === -1 ? 'Disliked' : 'Dislike'}
                    </button>
                  </>
                )}

                {/* Add to List */}
                <AddToIndividualList
                  item={{
                    tmdb_id: item.tmdb_id,
                    trakt_id: item.trakt_id,
                    media_type: (item.media_type === 'tv' ? 'show' : item.media_type) as 'movie' | 'show',
                    title: item.title,
                    year: item.year,
                    overview: item.overview,
                    poster_path: item.poster_path,
                    genres: item.genres
                  }}
                  buttonClassName="px-6 py-3 rounded-lg border-2 bg-purple-600 border-purple-500 text-white hover:bg-purple-700 font-medium shadow-lg shadow-purple-500/30 transition-all"
                />

                {/* Trailer - Lazy loaded on hover/click */}
                {(trailers.length > 0 || item?.trailer_url || !trailersLoading) && (
                  <button
                    onMouseEnter={() => {
                      if (trailers.length === 0 && !trailersLoading) {
                        loadTrailers();
                      }
                    }}
                    onClick={handleTrailerButtonClick}
                    className="px-6 py-3 rounded-lg border-2 bg-red-600 border-red-500 text-white hover:bg-red-700 font-medium shadow-lg shadow-red-500/30 transition-all disabled:opacity-50"
                    disabled={trailersLoading}
                  >
                    <Play className="w-5 h-5 inline mr-2" />
                    {trailersLoading ? 'Loading...' : 'Watch Trailer'}
                  </button>
                )}

                {/* Homepage Link */}
                {item.homepage && (
                  <a
                    href={item.homepage}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="px-6 py-3 rounded-lg border-2 bg-gray-800/50 border-gray-600 text-gray-300 hover:border-blue-500 hover:bg-gray-800 font-medium transition-all"
                  >
                    Official Site
                  </a>
                )}
              </div>

              {/* AI Personalized Rationale */}
              {(aiRationale || rationaleLoading) && (
                <motion.div
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  className="mb-8 bg-gradient-to-r from-purple-900/40 via-indigo-900/40 to-purple-900/40 rounded-xl p-6 border-2 border-purple-500/40 shadow-lg shadow-purple-500/20"
                >
                  <div className="flex items-start gap-3">
                    <div className="flex-shrink-0">
                      <div className="w-10 h-10 bg-purple-600/30 rounded-full flex items-center justify-center">
                        <Star className="w-5 h-5 text-purple-300" />
                      </div>
                    </div>
                    <div className="flex-1">
                      <h3 className="text-lg font-semibold text-purple-200 mb-2">Why You'll Love This</h3>
                      {rationaleLoading ? (
                        <div className="flex items-center gap-2 text-purple-300">
                          <div className="animate-spin rounded-full h-4 w-4 border-2 border-purple-300 border-t-transparent"></div>
                          <span className="text-sm">Analyzing your preferences...</span>
                        </div>
                      ) : (
                        <p className="text-purple-100 text-base leading-relaxed italic">
                          "{aiRationale}"
                        </p>
                      )}
                    </div>
                  </div>
                </motion.div>
              )}

              {/* Plot Summary */}
              <div className="mb-8">
                <h2 className="text-2xl font-bold text-white mb-3">Plot</h2>
                <p className="text-gray-300 text-base leading-relaxed">
                  {item.overview || 'No plot summary available.'}
                </p>
              </div>

              {/* Details Grid - Spread Out */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mb-8">
                {/* Left Column */}
                <div className="space-y-6">
                  {/* Release Info */}
                  {(item.release_date || item.first_air_date) && (
                    <div>
                      <h3 className="text-lg font-semibold text-white mb-3 flex items-center gap-2">
                        <Calendar className="w-5 h-5 text-purple-400" />
                        Release Information
                      </h3>
                      <div className="bg-gray-900/40 rounded-lg p-4 space-y-2">
                        {item.media_type === 'movie' && item.release_date && (
                          <div className="flex justify-between">
                            <span className="text-gray-400">Release Date</span>
                            <span className="text-white font-medium">{item.release_date}</span>
                          </div>
                        )}
                        {item.media_type === 'tv' && item.first_air_date && (
                          <div className="flex justify-between">
                            <span className="text-gray-400">First Aired</span>
                            <span className="text-white font-medium">{item.first_air_date}</span>
                          </div>
                        )}
                        {item.media_type === 'tv' && item.last_air_date && (
                          <div className="flex justify-between">
                            <span className="text-gray-400">Last Aired</span>
                            <span className="text-white font-medium">{item.last_air_date}</span>
                          </div>
                        )}
                      </div>
                    </div>
                  )}

                  {/* Box Office (Movies) */}
                  {item.media_type === 'movie' && (item.budget > 0 || item.revenue > 0) && (
                    <div>
                      <h3 className="text-lg font-semibold text-white mb-3">Box Office</h3>
                      <div className="bg-gray-900/40 rounded-lg p-4 space-y-2">
                        {item.budget > 0 && (
                          <div className="flex justify-between">
                            <span className="text-gray-400">Budget</span>
                            <span className="text-white font-medium">${(item.budget / 1000000).toFixed(1)}M</span>
                          </div>
                        )}
                        {item.revenue > 0 && (
                          <div className="flex justify-between">
                            <span className="text-gray-400">Revenue</span>
                            <span className="text-green-300 font-medium">${(item.revenue / 1000000).toFixed(1)}M</span>
                          </div>
                        )}
                        {item.budget > 0 && item.revenue > 0 && (
                          <div className="flex justify-between pt-2 border-t border-gray-700">
                            <span className="text-gray-400">Profit</span>
                            <span className={`font-medium ${item.revenue > item.budget ? 'text-green-300' : 'text-red-300'}`}>
                              ${((item.revenue - item.budget) / 1000000).toFixed(1)}M
                            </span>
                          </div>
                        )}
                      </div>
                    </div>
                  )}

                  {/* Production */}
                  {item.production_companies && item.production_companies.length > 0 && (
                    <div>
                      <h3 className="text-lg font-semibold text-white mb-3">Production Companies</h3>
                      <div className="flex flex-wrap gap-2">
                        {item.production_companies.slice(0, 5).map((company, idx) => (
                          <span key={idx} className="px-3 py-2 bg-gray-900/40 text-gray-300 rounded-lg text-sm border border-gray-700">
                            {company}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}
                </div>

                {/* Right Column */}
                <div className="space-y-6">
                  {/* Networks (TV) */}
                  {item.media_type === 'tv' && item.networks && item.networks.length > 0 && (
                    <div>
                      <h3 className="text-lg font-semibold text-white mb-3">Networks</h3>
                      <div className="flex flex-wrap gap-2">
                        {item.networks.map((network, idx) => (
                          <span key={idx} className="px-3 py-2 bg-blue-600/20 text-blue-200 rounded-lg text-sm border border-blue-500/50">
                            {network}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* WatchBuddy Stats */}
                  <div>
                    <h3 className="text-lg font-semibold text-white mb-3">WatchBuddy Scores</h3>
                    <div className="bg-gray-900/40 rounded-lg p-4 space-y-3">
                      {item.obscurity_score !== undefined && (
                        <div className="flex justify-between items-center">
                          <span className="text-gray-400">Obscurity</span>
                          <div className="flex items-center gap-2">
                            <div className="w-32 h-2 bg-gray-700 rounded-full overflow-hidden">
                              <div 
                                className="h-full bg-purple-500"
                                style={{ width: `${item.obscurity_score * 100}%` }}
                              />
                            </div>
                            <span className="text-purple-300 font-medium w-12 text-right">
                              {(item.obscurity_score * 100).toFixed(0)}%
                            </span>
                          </div>
                        </div>
                      )}
                      {item.mainstream_score !== undefined && (
                        <div className="flex justify-between items-center">
                          <span className="text-gray-400">Mainstream</span>
                          <div className="flex items-center gap-2">
                            <div className="w-32 h-2 bg-gray-700 rounded-full overflow-hidden">
                              <div 
                                className="h-full bg-blue-500"
                                style={{ width: `${item.mainstream_score * 100}%` }}
                              />
                            </div>
                            <span className="text-blue-300 font-medium w-12 text-right">
                              {(item.mainstream_score * 100).toFixed(0)}%
                            </span>
                          </div>
                        </div>
                      )}
                      {item.popularity && (
                        <div className="flex justify-between">
                          <span className="text-gray-400">Popularity</span>
                          <span className="text-white font-medium">{item.popularity.toFixed(1)}</span>
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              </div>

              {/* Cast */}
              {item.cast.length > 0 && (
                <div className="mb-8">
                  <h3 className="text-xl font-semibold text-white mb-4">Top Cast</h3>
                  <div className="flex flex-wrap gap-3">
                    {item.cast.map((actor, idx) => (
                      <span
                        key={idx}
                        className="px-4 py-2 bg-gray-900/60 text-gray-200 rounded-lg border border-gray-700 hover:border-purple-500 transition-colors"
                      >
                        {actor}
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </motion.div>
          </div>
        </div>

        {/* Franchise/Collection Section */}
        <ItemFranchise mediaType={mediaType} tmdbId={tmdbId} userId={1} />

        {/* Similar Items Section */}
        <ItemSuggestions mediaType={mediaType} tmdbId={tmdbId} userId={1} />
      </div>

      {/* Trailer Modal */}
      {showTrailer && trailers.length > 0 && (
        <div 
          className="fixed inset-0 bg-black/90 z-50 flex items-center justify-center p-4"
          onClick={() => setShowTrailer(false)}
        >
          <div className="relative w-full max-w-6xl" onClick={(e) => e.stopPropagation()}>
            <button
              onClick={() => setShowTrailer(false)}
              className="absolute -top-12 right-0 text-white hover:text-gray-300 text-xl font-bold"
            >
              ✕ Close
            </button>
            
            {/* Primary Trailer */}
            <div className="aspect-video rounded-lg overflow-hidden mb-4">
              <iframe
                src={`https://www.youtube.com/embed/${trailers[0].key}?autoplay=1`}
                className="w-full h-full"
                allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
                allowFullScreen
              />
            </div>
            
            {/* Additional Videos */}
            {trailers.length > 1 && (
              <div className="grid grid-cols-3 gap-4 max-h-48 overflow-y-auto">
                {trailers.slice(1, 7).map((video, idx) => (
                  <div 
                    key={video.key}
                    className="relative aspect-video rounded overflow-hidden cursor-pointer hover:ring-2 hover:ring-purple-500 transition-all bg-gray-900"
                    onClick={(e) => {
                      e.stopPropagation();
                      // Replace main video
                      const firstVideo = trailers[0];
                      const updatedTrailers = [video, firstVideo, ...trailers.filter((_, i) => i !== 0 && i !== idx + 1)];
                      setTrailers(updatedTrailers);
                    }}
                  >
                    <img 
                      src={`https://img.youtube.com/vi/${video.key}/mqdefault.jpg`}
                      alt={video.name}
                      className="w-full h-full object-cover"
                    />
                    <div className="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-black to-transparent p-2">
                      <p className="text-white text-xs font-medium truncate">{video.name}</p>
                      <p className="text-gray-400 text-xs">{video.type}</p>
                    </div>
                    <div className="absolute inset-0 flex items-center justify-center">
                      <Play className="w-8 h-8 text-white opacity-80" />
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
