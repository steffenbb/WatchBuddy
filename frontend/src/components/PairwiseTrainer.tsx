import React, { useState, useEffect } from 'react';
import { apiPost, apiGet } from '../api/client';

interface Candidate {
  id: number;
  trakt_id: number | null;
  tmdb_id: number | null;
  media_type: string;
  title: string;
  year: number | null;
  overview: string | null;
  genres: string | null;
  poster_path: string | null;
  backdrop_path: string | null;
  vote_average: number | null;
  vote_count: number | null;
  popularity: number | null;
}

interface TrainingSession {
  session_id: number;
  total_pairs: number;
  status: string;
  message: string;
}

interface NextPair {
  session_id: number;
  candidate_a: Candidate;
  candidate_b: Candidate;
  progress: number;
}

interface SessionStatus {
  id: number;
  user_id: number;
  prompt: string;
  list_type: string;
  total_pairs: number;
  completed_pairs: number;
  progress: number;
  status: string;
  started_at: string | null;
  completed_at: string | null;
}

export default function PairwiseTrainer() {
  const [sessionId, setSessionId] = useState<number | null>(null);
  const [currentPair, setCurrentPair] = useState<NextPair | null>(null);
  const [sessionStatus, setSessionStatus] = useState<SessionStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [userPrompt, setUserPrompt] = useState('');
  const [isCreating, setIsCreating] = useState(false);
  const [judging, setJudging] = useState(false);
  const [startTime, setStartTime] = useState<number | null>(null);

  // Load session status periodically
  useEffect(() => {
    if (sessionId && sessionStatus?.status === 'active') {
      const interval = setInterval(() => {
        fetchSessionStatus(sessionId);
      }, 5000); // Poll every 5 seconds
      
      return () => clearInterval(interval);
    }
  }, [sessionId, sessionStatus?.status]);

  const fetchSessionStatus = async (sid: number) => {
    try {
      const status = await apiGet(`/pairwise/session/${sid}/status?user_id=1`) as SessionStatus;
      setSessionStatus(status);
    } catch (err: any) {
      console.error('Failed to fetch session status:', err);
    }
  };

  const createSession = async () => {
    setIsCreating(true);
    setError(null);

    try {
      // Create auto-generated session with diverse candidates from database
      const session = await apiPost('/pairwise/session/create-auto', {
        user_id: 1
      }) as TrainingSession;

      setSessionId(session.session_id);
      
      // Fetch first pair
      await fetchNextPair(session.session_id);
      
      // Fetch session status
      await fetchSessionStatus(session.session_id);

    } catch (err: any) {
      setError(err.message || 'Failed to create training session');
      console.error('Session creation error:', err);
    } finally {
      setIsCreating(false);
    }
  };

  const fetchNextPair = async (sid: number) => {
    setLoading(true);
    setError(null);

    try {
      const pair = await apiGet(`/pairwise/session/${sid}/next?user_id=1`) as NextPair | null;
      
      if (pair === null) {
        // Session complete
        setCurrentPair(null);
        await fetchSessionStatus(sid);
      } else {
        setCurrentPair(pair);
        setStartTime(Date.now());
      }
    } catch (err: any) {
      setError(err.message || 'Failed to fetch next pair');
      console.error('Fetch pair error:', err);
    } finally {
      setLoading(false);
    }
  };

  const submitJudgment = async (winner: 'a' | 'b' | 'skip') => {
    if (!sessionId || !currentPair) return;

    setJudging(true);
    setError(null);

    const responseTimeMs = startTime ? Date.now() - startTime : null;

    try {
      await apiPost('/pairwise/session/judgment', {
        user_id: 1,
        session_id: sessionId,
        candidate_a_id: currentPair.candidate_a.id,
        candidate_b_id: currentPair.candidate_b.id,
        winner: winner,
        confidence: null,
        response_time_ms: responseTimeMs,
        explanation: null
      });

      // Fetch next pair
      await fetchNextPair(sessionId);
      
      // Update session status
      if (sessionId) {
        await fetchSessionStatus(sessionId);
      }

    } catch (err: any) {
      setError(err.message || 'Failed to submit judgment');
      console.error('Submit judgment error:', err);
    } finally {
      setJudging(false);
    }
  };

  const resetSession = () => {
    setSessionId(null);
    setCurrentPair(null);
    setSessionStatus(null);
    setUserPrompt('');
    setError(null);
    setStartTime(null);
  };

  const getPosterUrl = (posterPath: string | null) => {
    if (!posterPath) return 'https://via.placeholder.com/300x450?text=No+Poster';
    return `https://image.tmdb.org/t/p/w500${posterPath}`;
  };

  // Session creation screen
  if (!sessionId) {
    return (
      <div className="max-w-4xl mx-auto p-6">
        <div className="bg-gradient-to-br from-purple-900/20 to-blue-900/20 rounded-xl p-8 border border-purple-500/30">
          <h1 className="text-3xl font-bold mb-4 text-transparent bg-clip-text bg-gradient-to-r from-purple-400 to-blue-400">
            Preference Training
          </h1>
          <p className="text-gray-300 mb-6">
            Help us learn your preferences by comparing pairs of movies/shows. Your choices will improve future recommendations.
          </p>

          {error && (
            <div className="mb-4 p-4 bg-red-900/30 border border-red-500/50 rounded-lg text-red-300">
              {error}
            </div>
          )}

          <button
            onClick={createSession}
            disabled={isCreating}
            className={`w-full px-6 py-3 rounded-lg font-semibold transition-all ${
              isCreating
                ? 'bg-gray-700 text-gray-400 cursor-not-allowed'
                : 'bg-gradient-to-r from-purple-600 to-blue-600 hover:from-purple-500 hover:to-blue-500 text-white'
            }`}
          >
            {isCreating ? 'Loading Candidates...' : 'Start Training'}
          </button>
        </div>
      </div>
    );
  }

  // Session complete screen
  if (sessionStatus && sessionStatus.status === 'completed') {
    return (
      <div className="max-w-4xl mx-auto p-6">
        <div className="bg-gradient-to-br from-green-900/20 to-blue-900/20 rounded-xl p-8 border border-green-500/30 text-center">
          <div className="text-6xl mb-4">🎉</div>
          <h2 className="text-3xl font-bold mb-4 text-transparent bg-clip-text bg-gradient-to-r from-green-400 to-blue-400">
            Training Complete!
          </h2>
          <p className="text-gray-300 mb-6">
            You've completed {sessionStatus.completed_pairs} comparisons. Your preferences have been updated!
          </p>
          
          <div className="flex gap-4 justify-center">
            <button
              onClick={resetSession}
              className="px-6 py-3 bg-gradient-to-r from-purple-600 to-blue-600 hover:from-purple-500 hover:to-blue-500 text-white rounded-lg font-semibold transition-all"
            >
              Start New Session
            </button>
          </div>
        </div>
      </div>
    );
  }

  // Pairwise comparison screen
  return (
    <div className="max-w-7xl mx-auto p-6">
      {/* Progress bar */}
      {sessionStatus && (
        <div className="mb-6">
          <div className="flex justify-between items-center mb-2">
            <span className="text-sm text-gray-400">
              Progress: {sessionStatus.completed_pairs} / {sessionStatus.total_pairs} pairs
            </span>
            <span className="text-sm text-gray-400">
              {Math.round(sessionStatus.progress * 100)}%
            </span>
          </div>
          <div className="w-full h-2 bg-gray-700 rounded-full overflow-hidden">
            <div
              className="h-full bg-gradient-to-r from-purple-500 to-blue-500 transition-all duration-300"
              style={{ width: `${sessionStatus.progress * 100}%` }}
            />
          </div>
        </div>
      )}

      {error && (
        <div className="mb-4 p-4 bg-red-900/30 border border-red-500/50 rounded-lg text-red-300">
          {error}
        </div>
      )}

      {loading ? (
        <div className="text-center py-20">
          <div className="inline-block animate-spin rounded-full h-12 w-12 border-t-2 border-b-2 border-purple-500"></div>
          <p className="mt-4 text-gray-400">Loading next pair...</p>
        </div>
      ) : currentPair ? (
        <div>
          <h2 className="text-2xl font-bold mb-6 text-center text-transparent bg-clip-text bg-gradient-to-r from-purple-400 to-blue-400">
            Which would you rather watch?
          </h2>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mb-6">
            {/* Candidate A */}
            <CandidateCard
              candidate={currentPair.candidate_a}
              onSelect={() => submitJudgment('a')}
              disabled={judging}
              getPosterUrl={getPosterUrl}
            />

            {/* Candidate B */}
            <CandidateCard
              candidate={currentPair.candidate_b}
              onSelect={() => submitJudgment('b')}
              disabled={judging}
              getPosterUrl={getPosterUrl}
            />
          </div>

          {/* Mobile action bar */}
          <div className="md:hidden flex gap-3 mb-4">
            <button
              onClick={() => submitJudgment('a')}
              disabled={judging}
              className={`flex-1 px-4 py-3 rounded-lg font-semibold ${judging ? 'bg-gray-700 text-gray-400' : 'bg-purple-600 text-white'}`}
            >Choose Left</button>
            <button
              onClick={() => submitJudgment('b')}
              disabled={judging}
              className={`flex-1 px-4 py-3 rounded-lg font-semibold ${judging ? 'bg-gray-700 text-gray-400' : 'bg-blue-600 text-white'}`}
            >Choose Right</button>
          </div>

          {/* Skip button */}
          <div className="text-center">
            <button
              onClick={() => submitJudgment('skip')}
              disabled={judging}
              className={`px-6 py-2 rounded-lg font-medium transition-all ${
                judging
                  ? 'bg-gray-700 text-gray-400 cursor-not-allowed'
                  : 'bg-gray-700 hover:bg-gray-600 text-gray-300'
              }`}
            >
              Skip This Pair
            </button>
          </div>
        </div>
      ) : (
        <div className="text-center py-20">
          <p className="text-gray-400">No more pairs available</p>
        </div>
      )}
    </div>
  );
}

interface CandidateCardProps {
  candidate: Candidate;
  onSelect: () => void;
  disabled: boolean;
  getPosterUrl: (posterPath: string | null) => string;
}

function CandidateCard({ candidate, onSelect, disabled, getPosterUrl }: CandidateCardProps) {
  return (
    <div
      onClick={disabled ? undefined : onSelect}
      className={`group relative bg-gray-800/50 rounded-xl overflow-hidden border border-gray-700 transition-all ${
        disabled
          ? 'cursor-not-allowed opacity-60'
          : 'cursor-pointer hover:border-purple-500 hover:shadow-lg hover:shadow-purple-500/20 hover:scale-105'
      }`}
    >
      {/* Poster */}
      <div className="aspect-[2/3] overflow-hidden bg-gray-900">
        <img
          src={getPosterUrl(candidate.poster_path)}
          alt={candidate.title}
          loading="lazy"
          className="w-full h-full object-cover"
        />
      </div>

      {/* Info */}
      <div className="p-4">
        <h3 className="text-lg font-bold text-white mb-1 line-clamp-2">
          {candidate.title}
        </h3>
        
        <div className="flex items-center gap-2 text-sm text-gray-400 mb-2">
          <span className="px-2 py-1 bg-gray-700 rounded text-xs uppercase">
            {candidate.media_type}
          </span>
          {candidate.year && <span>{candidate.year}</span>}
        </div>

        {candidate.genres && (
          <p className="text-xs text-gray-400 mb-2 line-clamp-1">
            {candidate.genres}
          </p>
        )}

        {candidate.overview && (
          <p className="text-sm text-gray-300 line-clamp-3 mb-3">
            {candidate.overview}
          </p>
        )}

        {candidate.vote_average && (
          <div className="flex items-center gap-2 text-sm">
            <span className="text-yellow-400">★</span>
            <span className="text-gray-300">
              {candidate.vote_average.toFixed(1)} / 10
            </span>
            {candidate.vote_count && (
              <span className="text-gray-500 text-xs">
                ({candidate.vote_count.toLocaleString()} votes)
              </span>
            )}
          </div>
        )}
      </div>

      {/* Hover overlay */}
      {!disabled && (
        <div className="absolute inset-0 bg-purple-600/0 group-hover:bg-purple-600/20 transition-all flex items-center justify-center opacity-0 group-hover:opacity-100">
          <div className="text-white text-xl font-bold">Click to Choose</div>
        </div>
      )}
    </div>
  );
}
