import React, { useState, useEffect } from 'react';
import { useToast } from './ToastProvider';
import { formatLocalDate, formatRelativeTime, formatElapsedTime } from '../utils/date';

interface SyncStatus {
  active_syncs: Array<{
    list_id: number;
    list_title: string;
    started_at: string;
    progress?: number;
  }>;
  last_sync: string | null;
  total_lists: number;
  completed_today: number;
}

interface SystemHealth {
  redis: boolean;
  database: boolean;
  celery: boolean;
  trakt_api: boolean;
  tmdb_api: boolean;
}

interface WorkerStatus {
  movie: {
    status: string;
    last_run: string | null;
    next_run: string | null;
    items_processed: number;
    error: string | null;
  };
  show: {
    status: string;
    last_run: string | null;
    next_run: string | null;
    items_processed: number;
    error: string | null;
  };
}

interface FusionStatus {
  enabled: boolean;
  weights: Record<string, number>;
  aggressiveness?: number;
}
// Helper to fetch and update fusion settings
async function getFusionSettings() {
  const res = await fetch('/api/settings/fusion');
  if (!res.ok) throw new Error('Failed to fetch fusion settings');
  return await res.json();
}
async function setFusionSettings(settings: { enabled?: boolean; weights?: Record<string, number>; aggressiveness?: number }) {
  await fetch('/api/settings/fusion', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(settings)
  });
}

export const StatusWidgets: React.FC = () => {
  const [syncStatus, setSyncStatus] = useState<SyncStatus | null>(null);
  const [health, setHealth] = useState<SystemHealth | null>(null);
  const [workerStatus, setWorkerStatus] = useState<WorkerStatus | null>(null);
  const [fusionStatus, setFusionStatus] = useState<FusionStatus | null>(null);
  const [fusionAggressiveness, setFusionAggressiveness] = useState<number>(1);
  const [fusionLoading, setFusionLoading] = useState(false);
  const [liveWeights, setLiveWeights] = useState<Record<string, number> | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const { addToast } = useToast();

  const fetchStatus = async () => {
    try {
      setRefreshing(true);
      const [syncResponse, healthResponse, workerResponse, fusionResponse, fusionSettings] = await Promise.all([
        fetch('/api/status/sync'),
        fetch('/api/status/health'),
        fetch('/api/status/workers'),
        fetch('/api/recommendations/fusion/status'),
        getFusionSettings().catch(()=>({ aggressiveness: 1 }))
      ]);

      if (syncResponse.ok) {
        const syncData = await syncResponse.json();
        setSyncStatus(syncData);
      }

      if (healthResponse.ok) {
        const healthData = await healthResponse.json();
        setHealth(healthData);
      }

      if (workerResponse.ok) {
        const workerData = await workerResponse.json();
        setWorkerStatus(workerData);
      }

      if (fusionResponse.ok) {
        const fusionData = await fusionResponse.json();
        setFusionStatus(fusionData);
        setLiveWeights(fusionData.weights);
      }
      if (fusionSettings && typeof fusionSettings.aggressiveness === 'number') {
        setFusionAggressiveness(fusionSettings.aggressiveness);
      }
    } catch (error) {
      console.error('Failed to fetch status:', error);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  // Handler for slider
  const handleAggressivenessChange = async (val: number) => {
    setFusionAggressiveness(val);
    setFusionLoading(true);
    // Optimistically update weights for UI
    if (fusionStatus && fusionStatus.enabled) {
      // Simulate weights change for demo: scale all weights by (0.5 + val/2)
      const newWeights = Object.fromEntries(
        Object.entries(fusionStatus.weights).map(([k, w]) => [k, Math.max(0, Math.min(1, Number(w) * (0.5 + val/2)))])
      );
      setLiveWeights(newWeights);
    }
    try {
      await setFusionSettings({ aggressiveness: val });
      addToast({ message: 'Fusion aggressiveness updated', type: 'success' });
      // Optionally, refetch status to get real weights
          const updated = await getFusionSettings();
          if (typeof updated.aggressiveness === 'number') setFusionAggressiveness(updated.aggressiveness);
          if (updated.weights) setLiveWeights(updated.weights);
    } catch {
      addToast({ message: 'Failed to update fusion aggressiveness', type: 'error' });
    } finally {
      setFusionLoading(false);
    }
  };

  useEffect(() => {
    fetchStatus();
    
    // Refresh every 10 seconds
    const interval = setInterval(fetchStatus, 10000);
    return () => clearInterval(interval);
  }, []);

  const formatDuration = (timestamp: string) => {
    // Show elapsed time since sync started (e.g. '2m', '1h 30m')
    return formatElapsedTime(timestamp);
  };

  const formatLastSync = (timestamp: string | null) => {
    if (!timestamp) return 'Never';
    return formatRelativeTime(timestamp);
  };

  const getHealthColor = (status: boolean) => {
    return status ? 'text-green-600' : 'text-red-600';
  };

  const getHealthIcon = (status: boolean) => {
    return status ? '●' : '●';
  };

  if (loading) {
    return (
      <div className="space-y-4">
        {[1, 2, 3].map(i => (
          <div key={i} className="bg-white/10 backdrop-blur-lg rounded-2xl border border-white/20 p-4 md:p-6 flex flex-col gap-2 animate-pulse">
            <div className="h-4 bg-white/20 rounded w-1/2 mb-2"></div>
            <div className="h-6 bg-white/20 rounded w-3/4"></div>
          </div>
        ))}
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Active Syncs Widget */}
      <div className="bg-white/10 backdrop-blur-lg border border-white/20 rounded-2xl shadow-lg p-4 md:p-6">
        <div className="flex items-center justify-between mb-3">
          <h4 className="font-semibold text-white text-base">⏱️ Active Syncs</h4>
          <button
            onClick={fetchStatus}
            className="text-white/60 hover:text-white text-sm p-2 rounded-xl hover:bg-white/10 transition-all min-h-[44px] min-w-[44px] flex items-center justify-center"
            title="Refresh"
            aria-busy={refreshing}
          >
            {refreshing ? (
              <span className="inline-block w-4 h-4 border-2 border-white/60 border-t-transparent rounded-full animate-spin" />
            ) : <span className="text-xl">↻</span>}
          </button>
        </div>
        
        {syncStatus?.active_syncs.length ? (
          <div className="space-y-3">
            {syncStatus.active_syncs.map((sync) => (
              <div key={sync.list_id} className="border border-purple-400/30 bg-purple-500/20 p-4 rounded-xl">
                <div className="flex items-center justify-between">
                  <div className="min-w-0 flex-1">
                    <div className="font-medium text-white truncate">{sync.list_title}</div>
                    <div className="text-sm text-white/60 mt-1">
                      Running for {formatDuration(sync.started_at)}
                    </div>
                  </div>
                  <div className="flex items-center ml-3">
                    <div className="animate-spin w-5 h-5 border-2 border-purple-300 border-t-transparent rounded-full"></div>
                  </div>
                </div>
                {sync.progress !== undefined && (
                  <div className="mt-3">
                    <div className="w-full bg-white/10 rounded-full h-2">
                      <div 
                        className="bg-gradient-to-r from-purple-400 to-pink-400 h-2 rounded-full transition-all duration-300"
                        style={{ width: `${sync.progress}%` }}
                      ></div>
                    </div>
                    <div className="text-xs text-white/70 mt-1 text-right">{sync.progress}%</div>
                  </div>
                )}
              </div>
            ))}
          </div>
        ) : (
          <div className="text-white/50 text-sm py-2">No active syncs</div>
        )}
      </div>

      {/* Lists Overview Widget */}
      <div className="bg-white/10 backdrop-blur-lg border border-white/20 rounded-2xl shadow-lg p-4 md:p-6">
        <h4 className="font-semibold text-white mb-4 text-base">📊 Lists Overview</h4>
        <div className="grid grid-cols-2 gap-4">
          <div className="text-center bg-indigo-500/20 border border-indigo-400/30 rounded-xl p-4">
            <div className="text-2xl md:text-3xl font-bold text-white">
              {syncStatus?.total_lists || 0}
            </div>
            <div className="text-sm text-white/70 mt-2">Total Lists</div>
          </div>
          <div className="text-center bg-emerald-500/20 border border-emerald-400/30 rounded-xl p-4">
            <div className="text-2xl md:text-3xl font-bold text-white">
              {syncStatus?.completed_today || 0}
            </div>
            <div className="text-sm text-white/70 mt-2">Synced Today</div>
          </div>
        </div>
        
        <div className="mt-4 pt-4 border-t border-white/20">
          <div className="text-sm text-white/80 flex items-center gap-2">
            <span>Last sync:</span>
            <span className="font-medium text-white">{formatLastSync(syncStatus?.last_sync || null)}</span>
            {refreshing && (
              <span className="inline-block w-3 h-3 border-2 border-white/60 border-t-transparent rounded-full animate-spin" />
            )}
          </div>
        </div>
      </div>

      {/* Lists Quick Actions */}
      <div className="bg-white/10 backdrop-blur-lg border border-white/20 rounded-2xl shadow-lg p-4 md:p-6">
        <div className="flex items-center justify-between mb-3">
          <h4 className="font-semibold text-white text-base">⚡ Quick Actions</h4>
          <button
            onClick={fetchStatus}
            className="text-white/60 hover:text-white text-sm p-2 rounded-xl hover:bg-white/10 transition-all min-h-[44px] min-w-[44px] flex items-center justify-center"
            title="Refresh"
            aria-busy={refreshing}
          >
            {refreshing ? (
              <span className="inline-block w-4 h-4 border-2 border-white/60 border-t-transparent rounded-full animate-spin" />
            ) : <span className="text-xl">↻</span>}
          </button>
        </div>
        <QuickActions addToast={(msg, type)=>addToast({ message: msg, type: type || 'info' })} />
      </div>

      {/* System Health Widget */}
      <div className="bg-white/10 backdrop-blur-lg border border-white/20 rounded-2xl shadow-lg p-4 md:p-6">
        <h4 className="font-semibold text-white mb-4 text-base">💚 System Health</h4>
        
        {health ? (
          <div className="space-y-2">
            <div className="flex items-center justify-between text-sm py-2 px-3 bg-white/5 rounded-lg">
              <span className="text-white/90">Redis</span>
              <span className={`font-medium ${health.redis ? 'text-emerald-300' : 'text-red-300'}`}>
                {health.redis ? '● Online' : '● Offline'}
              </span>
            </div>
            <div className="flex items-center justify-between text-sm py-2 px-3 bg-white/5 rounded-lg">
              <span className="text-white/90">Database</span>
              <span className={`font-medium ${health.database ? 'text-emerald-300' : 'text-red-300'}`}>
                {health.database ? '● Online' : '● Offline'}
              </span>
            </div>
            <div className="flex items-center justify-between text-sm py-2 px-3 bg-white/5 rounded-lg">
              <span className="text-white/90">Celery</span>
              <span className={`font-medium ${health.celery ? 'text-emerald-300' : 'text-red-300'}`}>
                {health.celery ? '● Online' : '● Offline'}
              </span>
            </div>
            <div className="flex items-center justify-between text-sm py-2 px-3 bg-white/5 rounded-lg">
              <span className="text-white/90">Trakt API</span>
              <span className={`font-medium ${health.trakt_api ? 'text-emerald-300' : 'text-red-300'}`}>
                {health.trakt_api ? '● Online' : '● Offline'}
              </span>
            </div>
            <div className="flex items-center justify-between text-sm py-2 px-3 bg-white/5 rounded-lg">
              <span className="text-white/90">TMDB API</span>
              <span className={`font-medium ${health.tmdb_api ? 'text-emerald-300' : 'text-red-300'}`}>
                {health.tmdb_api ? '● Online' : '● Offline'}
              </span>
            </div>
            
            {/* Worker Status Section */}
            {workerStatus && (
              <>
                <div className="border-t border-white/20 my-3"></div>
                <div className="text-sm font-semibold text-white/90 mb-2">Background Workers</div>
                
                {/* Movie Worker */}
                <div className="bg-white/5 rounded-xl p-3 space-y-2">
                  <div className="flex items-center justify-between text-sm">
                    <span className="font-medium text-white/90">🎬 Movie Ingestion</span>
                    <span className={`font-medium text-xs px-2 py-1 rounded-full ${
                      workerStatus.movie.status === 'running' ? 'bg-blue-500/20 text-blue-300' :
                      workerStatus.movie.status === 'completed' ? 'bg-emerald-500/20 text-emerald-300' :
                      workerStatus.movie.status === 'error' ? 'bg-red-500/20 text-red-300' :
                      'bg-white/10 text-white/50'
                    }`}>
                      {workerStatus.movie.status === 'running' && '⏳ Running'}
                      {workerStatus.movie.status === 'completed' && '✓ Completed'}
                      {workerStatus.movie.status === 'error' && '✕ Error'}
                      {workerStatus.movie.status === 'idle' && '○ Idle'}
                    </span>
                  </div>
                  {workerStatus.movie.last_run && (
                    <div className="text-xs text-white/60">
                      Last: {workerStatus.movie.last_run}
                    </div>
                  )}
                  {workerStatus.movie.next_run && (
                    <div className="text-xs text-white/60">
                      Next: {workerStatus.movie.next_run}
                    </div>
                  )}
                  {workerStatus.movie.items_processed > 0 && (
                    <div className="text-xs text-white/60">
                      Processed: {workerStatus.movie.items_processed} items
                    </div>
                  )}
                  {workerStatus.movie.error && (
                    <div className="text-xs text-red-300 truncate" title={workerStatus.movie.error}>
                      Error: {workerStatus.movie.error}
                    </div>
                  )}
                </div>
                
                {/* TV Show Worker */}
                <div className="bg-white/5 rounded-xl p-3 space-y-2">
                  <div className="flex items-center justify-between text-sm">
                    <span className="font-medium text-white/90">📺 TV Show Ingestion</span>
                    <span className={`font-medium text-xs px-2 py-1 rounded-full ${
                      workerStatus.show.status === 'running' ? 'bg-blue-500/20 text-blue-300' :
                      workerStatus.show.status === 'completed' ? 'bg-emerald-500/20 text-emerald-300' :
                      workerStatus.show.status === 'error' ? 'bg-red-500/20 text-red-300' :
                      'bg-white/10 text-white/50'
                    }`}>
                      {workerStatus.show.status === 'running' && '⏳ Running'}
                      {workerStatus.show.status === 'completed' && '✓ Completed'}
                      {workerStatus.show.status === 'error' && '✕ Error'}
                      {workerStatus.show.status === 'idle' && '○ Idle'}
                    </span>
                  </div>
                  {workerStatus.show.last_run && (
                    <div className="text-xs text-white/60">
                      Last: {workerStatus.show.last_run}
                    </div>
                  )}
                  {workerStatus.show.next_run && (
                    <div className="text-xs text-white/60">
                      Next: {workerStatus.show.next_run}
                    </div>
                  )}
                  {workerStatus.show.items_processed > 0 && (
                    <div className="text-xs text-white/60">
                      Processed: {workerStatus.show.items_processed} items
                    </div>
                  )}
                  {workerStatus.show.error && (
                    <div className="text-xs text-red-300 truncate" title={workerStatus.show.error}>
                      Error: {workerStatus.show.error}
                    </div>
                  )}
                </div>
              </>
            )}
          </div>
        ) : (
          <div className="text-white/50 text-sm py-2">Unable to check health</div>
        )}
      </div>

      {/* Fusion Mode Widget (removed per requirements) */}
      {false && (
        <div className="bg-white/10 backdrop-blur-lg border border-white/20 rounded-2xl shadow-lg p-4 md:p-6">
          <h4 className="font-semibold text-white mb-3 text-base">Fusion Mode</h4>
          {/* Content intentionally disabled */}
        </div>
      )}
    </div>
  );
};

const QuickActions: React.FC<{ addToast: (msg: string, type?: 'success'|'error'|'info') => void }>=({ addToast })=>{
  const [lists, setLists] = React.useState<Array<{id:number; title:string;}>>([]);
  const [loading, setLoading] = React.useState<boolean>(false);

  const load = async()=>{
    try{
      setLoading(true);
      // Always use user_id=1 for demo
      const res = await fetch('/api/lists/?user_id=1');
      if(res.ok){
        const data = await res.json();
        // Accept array or {lists:[]}
        if(Array.isArray(data)) setLists(data);
        else setLists(data.lists || []);
      }
    }catch{}
    finally{ setLoading(false); }
  };

  React.useEffect(()=>{ 
    load(); 
    
    // Listen for list updates from other components
    const handleListsUpdated = () => load();
    window.addEventListener('lists-updated', handleListsUpdated);
    
    return () => window.removeEventListener('lists-updated', handleListsUpdated);
  },[]);

  const [syncing, setSyncing] = React.useState<boolean>(false);

  const syncAllWatchedOnly = async()=>{
    if(syncing) return;
    setSyncing(true);
    try{
      const results = await Promise.allSettled(
        lists.map(async (list) => {
          const res = await fetch(`/api/lists/${list.id}/sync?user_id=1&watched_only=true`, {
            method: 'POST'
          });
          if (!res.ok) throw new Error(`Failed to sync ${list.title}`);
          return list.title;
        })
      );
      
      const successful = results.filter(r => r.status === 'fulfilled').length;
      const failed = results.filter(r => r.status === 'rejected').length;
      
      if (failed === 0) {
        addToast(`Successfully synced watched status for all ${successful} lists`, 'success');
      } else if (successful === 0) {
        addToast(`Failed to sync watched status for all lists`, 'error');
      } else {
        addToast(`Synced ${successful} lists successfully, ${failed} failed`, 'info');
      }
    }catch(e:any){
      addToast(e?.message || 'Failed to sync watched status', 'error');
    } finally {
      setSyncing(false);
    }
  };

  if(loading) return <div className="text-white/50 text-sm">Loading…</div>;
  if(!lists.length) return <div className="text-white/50 text-sm">No lists yet</div>;

  return (
    <div className="space-y-3">
      <div className="text-sm text-white/70">
        {lists.length} list{lists.length !== 1 ? 's' : ''} total
      </div>
      <button
        onClick={syncAllWatchedOnly}
        disabled={syncing}
        className={`w-full px-4 py-3 rounded-xl text-sm font-medium transition-all min-h-[44px] ${
          syncing
            ? 'bg-white/5 text-white/40 cursor-not-allowed'
            : 'bg-gradient-to-r from-indigo-500 to-purple-500 hover:from-indigo-600 hover:to-purple-600 text-white shadow-lg'
        }`}
      >
        {syncing ? (
          <div className="flex items-center justify-center gap-2">
            <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin"></div>
            Syncing Watched Status...
          </div>
        ) : (
          `Sync Watched Status (All ${lists.length} Lists)`
        )}
      </button>
    </div>
  );
}