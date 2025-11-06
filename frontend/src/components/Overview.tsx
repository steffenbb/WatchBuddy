import React, { useEffect, useMemo, useRef, useState } from "react";
import { apiPost } from "../api/client";
import AddToIndividualList from "./AddToIndividualList";

interface OverviewItem {
  trakt_id: number;
  tmdb_id: number;
  title: string;
  media_type: string;
  year: number;
  poster_path?: string;
  overview?: string;
  genres: string[];
  vote_average?: number;
  score?: number;
  rationale?: string;
  release_badge?: string;
  trending_badge?: string;
  days_until_release?: number;
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

export default function Overview() {
  const [overview, setOverview] = useState<OverviewResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchOverview = async () => {
    setLoading(true);
    setError(null);
    
    try {
      const data = await apiPost("/overview", {
        user_id: 1
      }) as OverviewResponse;
      
      setOverview(data);
    } catch (err: any) {
      setError(err.message || "Failed to load overview");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchOverview();
  }, []);

  const triggerRefresh = async () => {
    try {
      await apiPost("/overview/refresh", { user_id: 1 });
      alert("Overview refresh queued. Check back in a few minutes!");
    } catch (err: any) {
      alert("Failed to trigger refresh: " + err.message);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="text-center">
          <div className="animate-spin rounded-full h-12 w-12 border-t-2 border-b-2 border-purple-500 mx-auto mb-4"></div>
          <p className="text-gray-300">Loading Your Overview...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-8 max-w-4xl mx-auto">
        <div className="bg-red-900/20 border border-red-500 rounded-lg p-6">
          <h2 className="text-xl font-bold text-red-400 mb-2">Error Loading Overview</h2>
          <p className="text-gray-300">{error}</p>
          <button
            onClick={() => fetchOverview()}
            className="mt-4 px-4 py-2 bg-red-600 hover:bg-red-700 text-white rounded"
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  if (!overview || !overview.sections || overview.sections.length === 0) {
    return (
      <div className="p-8 max-w-4xl mx-auto">
        <div className="bg-gray-800/50 border border-gray-700 rounded-lg p-8 text-center">
          <h2 className="text-2xl font-bold text-gray-300 mb-4">Your Overview is Being Prepared</h2>
          <p className="text-gray-400 mb-6">
            {overview?.message || "Your personalized overview will be computed nightly. Check back tomorrow!"}
          </p>
          <button
            onClick={triggerRefresh}
            className="px-6 py-3 bg-purple-600 hover:bg-purple-700 text-white rounded-lg font-medium"
          >
            Compute Now
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="p-6 max-w-7xl mx-auto">
      {/* Header */}
      <div className="mb-8">
        <div className="flex justify-between items-center mb-4">
          <h1 className="text-4xl font-bold bg-gradient-to-r from-purple-400 to-pink-400 bg-clip-text text-transparent">
            Your Overview
          </h1>
          <button
            onClick={triggerRefresh}
            className="px-4 py-2 bg-gray-700 hover:bg-gray-600 text-white rounded-lg text-sm"
          >
            Refresh Now
          </button>
        </div>
        <p className="text-gray-400">
          Last updated: {new Date(overview.sections[0]?.computed_at || overview.retrieved_at).toLocaleString()}
        </p>
      </div>

      {/* Overview Sections */}
      <div className="space-y-8">
        {overview.sections
          .filter(section => section.type !== 'investment')  // Filter out old 'investment' module
          .map((section, idx) => (
          <OverviewModule key={`${section.type}-${idx}`} section={section} />
        ))}
      </div>
    </div>
  );
}

function OverviewModule({ section }: { section: OverviewSection }) {
  const moduleTitle = {
    investment_tracker: "📊 Your Watch Investment",
    new_shows: "🆕 New Shows & Movies for You",
    trending: "🔥 Trending Now for You",
    upcoming: "📅 Coming Soon You'll Love"
  }[section.type] || section.type;

  return (
    <div className="bg-gray-800/50 border border-gray-700 rounded-lg p-6">
      <h2 className="text-2xl font-bold text-gray-200 mb-4">{moduleTitle}</h2>
      
      {section.type === "investment_tracker" && (
        <InvestmentTrackerModule data={section.data} />
      )}
      
      {(section.type === "new_shows" || section.type === "trending" || section.type === "upcoming") && (
        <RecommendationsModule data={section.data} type={section.type} />
      )}
    </div>
  );
}

function InvestmentTrackerModule({ data }: { data: any }) {
  // Prefer rich continuation objects if available
  const continuations: any[] = (data.continuations && data.continuations.length > 0)
    ? data.continuations
    : (data.upcoming_continuations || []);

  // Helpers
  const FORGOT_THRESHOLD = 45; // days
  const BADGE_THRESHOLD = 60; // days
  const nextEpisodeLabel = (item: any) => {
    const s = item.next_season ?? item.next_episode_season;
    const e = item.next_episode ?? item.next_episode_number;
    if (s && e) return `Next: S${s}E${e}`;
    if (s && !e) return `Next: S${s}`;
    if (!s && e) return `Next: E${e}`;
    return "Next up";
  };

  const daysSince = (iso?: string) => {
    if (!iso) return null;
    const then = new Date(iso);
    if (isNaN(then.getTime())) return null;
    const now = new Date();
    const diff = Math.floor((now.getTime() - then.getTime()) / (1000 * 60 * 60 * 24));
    return diff < 0 ? 0 : diff;
  };

  const continuationTagline = (item: any) => {
    const behind = typeof item.episodes_behind === 'number' ? item.episodes_behind : null;
    const since = daysSince(item.last_watched_at);
    if (behind !== null && behind >= 0 && behind <= 5) return `Only ${behind} to go`;
    if (behind !== null && behind > 5) return `${behind} behind`;
    if (since !== null && since >= 45) return `Did you forget? • ${since} days ago`;
    if (since !== null) return `${since === 0 ? 'Today' : `${since}d`} since last watch`;
    return undefined;
  };

  // Summary and subsets
  const behindCount = useMemo(
    () => continuations.filter((x) => (typeof x.episodes_behind === 'number' ? x.episodes_behind : 0) > 0).length,
    [continuations]
  );
  const lastWatchedDays = useMemo(() => {
    const last = data.last_watched ? new Date(data.last_watched) : null;
    if (!last || isNaN(last.getTime())) return null;
    const now = new Date();
    return Math.floor((now.getTime() - last.getTime()) / (1000 * 60 * 60 * 24));
  }, [data.last_watched]);

  const forgotList = useMemo(() => {
    const source = Array.isArray((data as any).forgotten_continuations)
      ? (data as any).forgotten_continuations
      : continuations;
    return source.filter((x: any) => {
      const d = daysSince(x.last_watched_at);
      return d !== null && d > FORGOT_THRESHOLD; // strictly older than threshold
    });
  }, [continuations, (data as any).forgotten_continuations]);

  const continueList = useMemo(
    () => continuations.filter((x) => {
      const d = daysSince(x.last_watched_at);
      // Include unknown last_watched_at in Continue Watching by default
      return d === null || d <= FORGOT_THRESHOLD; // within 45 days
    }),
    [continuations]
  );

  // Carousel controls
  const scrollerRef = useRef<HTMLDivElement>(null);
  const forgotScrollerRef = useRef<HTMLDivElement>(null);
  const scrollBy = (ref: React.RefObject<HTMLDivElement>, dir: number) => {
    const el = ref.current;
    if (!el) return;
    const distance = Math.max(280, Math.floor(el.clientWidth * 0.9));
    el.scrollBy({ left: dir * distance, behavior: 'smooth' });
  };

  return (
    <div className="space-y-6">
      {/* Stats Grid */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard
          label="Total Time Invested"
          value={data.total_time_invested || "0h"}
          icon="⏱️"
        />
        <StatCard
          label="Quality Score"
          value={data.quality_investment_score ? `${data.quality_investment_score}/100` : "N/A"}
          icon="⭐"
        />
        <StatCard
          label="Longest Binge"
          value={data.longest_binge_streak ? `${data.longest_binge_streak} days` : "0 days"}
          icon="🔥"
        />
        <StatCard
          label="This Week"
          value={data.watch_time_this_week || "0h"}
          icon="📆"
        />
      </div>

      {/* Top Genres */}
      {data.top_genres && data.top_genres.length > 0 && (
        <div>
          <h3 className="text-lg font-semibold text-gray-300 mb-3">Top Genres by Time</h3>
          <div className="flex flex-wrap gap-2">
            {data.top_genres.slice(0, 5).map((genre: any, idx: number) => (
              <div
                key={idx}
                className="px-4 py-2 bg-purple-900/30 border border-purple-700 rounded-lg"
              >
                <span className="text-purple-300 font-medium">{genre.genre}</span>
                <span className="text-gray-400 ml-2 text-sm">({genre.hours}h)</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Mini summary */}
      {(behindCount > 0 || lastWatchedDays !== null) && (
        <div className="flex flex-wrap items-center gap-3 p-3 bg-gray-900/40 rounded-lg border border-gray-800">
          {behindCount > 0 && (
            <span className="text-sm text-purple-300">You're behind on <span className="font-semibold">{behindCount}</span> shows</span>
          )}
          {lastWatchedDays !== null && (
            <span className="text-sm text-gray-300">Last watched {lastWatchedDays === 0 ? 'today' : `${lastWatchedDays} day${lastWatchedDays === 1 ? '' : 's'}`} ago</span>
          )}
        </div>
      )}

      {/* Most Valuable Show */}
      {data.most_valuable_show && (
        <div>
          <h3 className="text-lg font-semibold text-gray-300 mb-3">Most Valuable Show</h3>
          <div className="flex items-center gap-4 p-4 bg-gray-900/50 rounded-lg">
            {data.most_valuable_show.poster_path && (
              <img
                src={`https://image.tmdb.org/t/p/w154${data.most_valuable_show.poster_path}`}
                alt={data.most_valuable_show.title}
                className="w-16 h-24 object-cover rounded"
              />
            )}
            <div>
              <p className="font-semibold text-white">{data.most_valuable_show.title}</p>
              <p className="text-sm text-gray-400">
                Value Score: {data.most_valuable_show.value_score}
              </p>
              <p className="text-xs text-gray-500 mt-1">
                {data.most_valuable_show.episodes_watched} episodes watched
              </p>
            </div>
          </div>
        </div>
      )}

      {/* Did you forget? — paused 45+ days */}
      {forgotList.length > 0 && (
        <div>
          <div className="flex items-center justify-between mb-2">
            <h3 className="text-lg font-semibold text-amber-200">Did you forget?</h3>
            <div className="flex items-center gap-2">
              <button onClick={() => scrollBy(forgotScrollerRef, -1)} className="px-2 py-1 rounded bg-gray-800 hover:bg-gray-700 text-xs text-gray-300">◀</button>
              <button onClick={() => scrollBy(forgotScrollerRef, 1)} className="px-2 py-1 rounded bg-gray-800 hover:bg-gray-700 text-xs text-gray-300">▶</button>
            </div>
          </div>
          <div className="relative">
            <div ref={forgotScrollerRef} className="overflow-x-auto pb-2 -mx-1">
              <div className="flex gap-3 px-1 snap-x snap-mandatory">
                {forgotList.map((item: any, idx: number) => (
                  <div key={`forgot-${item.trakt_id || idx}`} className="min-w-[11rem] max-w-[11rem] snap-start bg-gray-900/50 rounded-lg p-3 relative">
                    {/* Add to Individual List */}
                    {item.tmdb_id && (
                      <div className="absolute top-2 left-2">
                        <AddToIndividualList
                          item={{
                            tmdb_id: item.tmdb_id,
                            trakt_id: item.trakt_id ?? undefined,
                            media_type: "show",
                            title: item.title,
                            year: item.year,
                            overview: item.overview,
                            poster_path: item.poster_path,
                            genres: item.genres,
                          }}
                        />
                      </div>
                    )}
                    {item.poster_path ? (
                      <img src={`https://image.tmdb.org/t/p/w185${item.poster_path}`} alt={item.title} className="w-full h-40 object-cover rounded mb-2" />
                    ) : (
                      <div className="w-full h-40 mb-2 rounded bg-gradient-to-br from-gray-700 to-gray-800 flex items-center justify-center text-gray-400 text-xs">No poster</div>
                    )}
                    <p className="text-sm font-medium text-white truncate" title={item.title}>{item.title}</p>
                    <p className="text-xs text-gray-400 mt-0.5">{nextEpisodeLabel(item)}</p>
                    <p className="text-xs text-amber-300 mt-1">Paused {daysSince(item.last_watched_at)} days</p>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Continue Watching - horizontally scrollable with chevrons and See all */}
      {continueList && continueList.length > 0 && (
        <div>
          <div className="flex items-center justify-between mb-2">
            <h3 className="text-lg font-semibold text-gray-300">Continue Watching ({continueList.length})</h3>
            <div className="flex items-center gap-2">
              <button onClick={() => scrollBy(scrollerRef, -1)} className="px-2 py-1 rounded bg-gray-800 hover:bg-gray-700 text-xs text-gray-300">◀</button>
              <button onClick={() => scrollBy(scrollerRef, 1)} className="px-2 py-1 rounded bg-gray-800 hover:bg-gray-700 text-xs text-gray-300">▶</button>
              <SeeAllContinuations data={continuations} daysSince={daysSince} nextEpisodeLabel={nextEpisodeLabel} />
            </div>
          </div>
          <div className="relative">
            <div ref={scrollerRef} className="overflow-x-auto pb-2 -mx-1">
              <div className="flex gap-3 px-1 snap-x snap-mandatory">
                {continueList.map((item: any, idx: number) => {
                  const since = daysSince(item.last_watched_at);
                  return (
                    <div
                      key={`${item.trakt_id || idx}`}
                      className="min-w-[11rem] max-w-[11rem] snap-start bg-gray-900/50 rounded-lg p-3 relative"
                    >
                      {/* Add to Individual List */}
                      {item.tmdb_id && (
                        <div className="absolute top-2 left-2">
                          <AddToIndividualList
                            item={{
                              tmdb_id: item.tmdb_id,
                              trakt_id: item.trakt_id ?? undefined,
                              media_type: "show",
                              title: item.title,
                              year: item.year,
                              overview: item.overview,
                              poster_path: item.poster_path,
                              genres: item.genres,
                            }}
                          />
                        </div>
                      )}
                      {since !== null && since >= BADGE_THRESHOLD && (
                        <span className="absolute top-2 right-2 text-[10px] px-2 py-0.5 rounded bg-amber-900/60 text-amber-200 border border-amber-700/60">
                          Did you forget?
                        </span>
                      )}

                      {item.poster_path ? (
                        <img
                          src={`https://image.tmdb.org/t/p/w185${item.poster_path}`}
                          alt={item.title}
                          className="w-full h-40 object-cover rounded mb-2"
                        />
                      ) : (
                        <div className="w-full h-40 mb-2 rounded bg-gradient-to-br from-gray-700 to-gray-800 flex items-center justify-center text-gray-400 text-xs">
                          No poster
                        </div>
                      )}

                      <p className="text-sm font-medium text-white truncate" title={item.title}>{item.title}</p>
                      <p className="text-xs text-gray-400 mt-0.5">{nextEpisodeLabel(item)}</p>
                      {continuationTagline(item) && (
                        <p className="text-xs text-purple-300 mt-1">{continuationTagline(item)}</p>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function RecommendationsModule({ data, type }: { data: any; type: string }) {
  const items: OverviewItem[] = data.items || [];

  if (items.length === 0) {
    return (
      <p className="text-gray-400">{data.message || "No items available"}</p>
    );
  }

  return (
    <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-5 gap-4">
      {items.map((item, idx) => (
        <div key={`${item.tmdb_id}-${idx}`} className="bg-gray-900/50 rounded-lg hover:ring-2 hover:ring-purple-500 transition-all relative">
          {/* Add to Individual List */}
          {item.tmdb_id && (
            <div className="absolute top-2 right-2 z-10">
              <AddToIndividualList
                item={{
                  tmdb_id: item.tmdb_id,
                  trakt_id: item.trakt_id ?? undefined,
                  media_type: (item.media_type === 'tv' ? 'show' : (item.media_type as any)) || 'movie',
                  title: item.title,
                  year: item.year,
                  overview: item.overview,
                  poster_path: item.poster_path,
                  genres: item.genres,
                }}
              />
            </div>
          )}
          {item.poster_path && (
            <img
              src={`https://image.tmdb.org/t/p/w342${item.poster_path}`}
              alt={item.title}
              className="w-full h-64 object-cover"
            />
          )}
          <div className="p-3">
            <p className="font-medium text-white text-sm truncate">{item.title}</p>
            <p className="text-xs text-gray-400">{item.year}</p>
            
            {item.release_badge && (
              <span className="inline-block mt-1 px-2 py-1 bg-blue-900/50 text-blue-300 text-xs rounded">
                {item.release_badge}
              </span>
            )}
            
            {item.trending_badge && (
              <span className="inline-block mt-1 px-2 py-1 bg-red-900/50 text-red-300 text-xs rounded">
                {item.trending_badge}
              </span>
            )}
            
            {item.score !== undefined && (
              <div className="mt-2 flex items-center gap-1">
                <span className="text-xs text-gray-400">Match:</span>
                <span className="text-xs font-semibold text-purple-400">{Math.round(item.score * 100)}%</span>
              </div>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

function StatCard({ label, value, icon }: { label: string; value: string; icon: string }) {
  return (
    <div className="bg-gray-900/50 rounded-lg p-4">
      <div className="text-2xl mb-2">{icon}</div>
      <p className="text-2xl font-bold text-white">{value}</p>
      <p className="text-sm text-gray-400">{label}</p>
    </div>
  );
}

function SeeAllContinuations({
  data,
  daysSince,
  nextEpisodeLabel
}: {
  data: any[];
  daysSince: (iso?: string) => number | null;
  nextEpisodeLabel: (item: any) => string;
}) {
  const [open, setOpen] = useState(false);
  const [sortKey, setSortKey] = useState<'most_behind' | 'most_recent' | 'longest_pause'>('most_behind');

  const sorted = useMemo(() => {
    const arr = [...data];
    if (sortKey === 'most_behind') {
      arr.sort((a, b) => (b.episodes_behind ?? 0) - (a.episodes_behind ?? 0));
    } else if (sortKey === 'most_recent') {
      arr.sort((a, b) => {
        const da = daysSince(a.last_watched_at) ?? 999999;
        const db = daysSince(b.last_watched_at) ?? 999999;
        return da - db; // smaller = more recent
      });
    } else if (sortKey === 'longest_pause') {
      arr.sort((a, b) => {
        const da = daysSince(a.last_watched_at) ?? -1;
        const db = daysSince(b.last_watched_at) ?? -1;
        return db - da; // larger first
      });
    }
    return arr;
  }, [data, sortKey, daysSince]);

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        className="px-2 py-1 rounded bg-purple-700/80 hover:bg-purple-600 text-white text-xs"
      >
        See all
      </button>

      {open && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div className="absolute inset-0 bg-black/60" onClick={() => setOpen(false)}></div>
          <div className="relative bg-gray-900 border border-gray-700 rounded-lg w-[90vw] max-w-4xl max-h-[85vh] overflow-hidden">
            <div className="flex items-center justify-between p-4 border-b border-gray-800">
              <h4 className="text-lg font-semibold text-white">All continuations ({data.length})</h4>
              <div className="flex items-center gap-2">
                <label className="text-xs text-gray-400">Sort:</label>
                <select
                  value={sortKey}
                  onChange={(e) => setSortKey(e.target.value as any)}
                  className="bg-gray-800 text-gray-200 text-xs rounded px-2 py-1 border border-gray-700"
                >
                  <option value="most_behind">Most behind</option>
                  <option value="most_recent">Most recent</option>
                  <option value="longest_pause">Longest pause</option>
                </select>
                <button onClick={() => setOpen(false)} className="ml-2 px-2 py-1 text-xs rounded bg-gray-800 hover:bg-gray-700 text-gray-200">Close</button>
              </div>
            </div>
            <div className="p-4 overflow-auto">
              <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4">
                {sorted.map((item, idx) => (
                  <div key={`all-${item.trakt_id || idx}`} className="bg-gray-800/60 border border-gray-700 rounded-lg p-3">
                    {item.poster_path ? (
                      <img src={`https://image.tmdb.org/t/p/w185${item.poster_path}`} alt={item.title} className="w-full h-44 object-cover rounded mb-2" />
                    ) : (
                      <div className="w-full h-44 mb-2 rounded bg-gradient-to-br from-gray-700 to-gray-800 flex items-center justify-center text-gray-400 text-xs">No poster</div>
                    )}
                    <p className="text-sm font-medium text-white truncate" title={item.title}>{item.title}</p>
                    <p className="text-xs text-gray-400 mt-0.5">{nextEpisodeLabel(item)}</p>
                    <div className="flex items-center justify-between mt-1 text-xs">
                      <span className="text-purple-300">{typeof item.episodes_behind === 'number' ? `${item.episodes_behind} behind` : ''}</span>
                      <span className="text-gray-400">{(() => {
                        const d = daysSince(item.last_watched_at);
                        return d === null ? '' : d === 0 ? 'today' : `${d}d ago`;
                      })()}</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
