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
      const [syncResponse, healthResponse, fusionResponse, fusionSettings] = await Promise.all([
        fetch('/api/status/sync'),
        fetch('/api/status/health'),
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
      <div className="space-y-4 bg-gradient-to-br from-fuchsia-100 via-indigo-50 to-blue-100 p-2 md:p-4 rounded-3xl shadow-2xl border border-indigo-100 w-full max-w-xl mx-auto transition-all duration-500">
        {[1, 2, 3].map(i => (
          <div key={i} className="relative z-10 bg-white/80 backdrop-blur-xl rounded-2xl shadow-xl border border-indigo-100 p-4 md:p-6 flex flex-col gap-2 animate-pulse transition-all duration-500">
            <div className="h-4 bg-gray-200 rounded w-1/2 mb-2"></div>
            <div className="h-6 bg-gray-200 rounded w-3/4"></div>
          </div>
        ))}
      </div>
    );
  }

  return (
    <div className="space-y-4 bg-gradient-to-br from-fuchsia-100 via-indigo-50 to-blue-100 p-2 md:p-4 rounded-3xl shadow-2xl border border-indigo-100 w-full max-w-xl mx-auto transition-all duration-500">
      {/* Active Syncs Widget */}
  <div className="relative z-10 bg-white/90 backdrop-blur-xl rounded-2xl shadow-xl border border-indigo-100 p-4 md:p-6 flex flex-col gap-2 transition-all duration-500">
        <div className="flex items-center justify-between mb-3">
          <h4 className="font-semibold text-gray-900 text-sm">Active Syncs</h4>
          <button
            onClick={fetchStatus}
            className="text-gray-400 hover:text-gray-600 text-sm p-1 rounded hover:bg-gray-100 transition-colors touch-manipulation flex items-center gap-1"
            title="Refresh"
            aria-busy={refreshing}
          >
            {refreshing ? (
              <span className="inline-block w-3 h-3 border-2 border-gray-500 border-t-transparent rounded-full animate-spin" />
            ) : '↻'}
          </button>
        </div>
        
        {syncStatus?.active_syncs.length ? (
          <div className="space-y-2">
            {syncStatus.active_syncs.map((sync) => (
              <div key={sync.list_id} className="border border-blue-200 bg-blue-50 p-3 rounded-lg">
                <div className="flex items-center justify-between">
                  <div className="min-w-0 flex-1">
                    <div className="font-medium text-sm text-gray-900 truncate">{sync.list_title}</div>
                    <div className="text-xs text-gray-600 mt-1">
                      Running for {formatDuration(sync.started_at)}
                    </div>
                  </div>
                  <div className="flex items-center ml-3">
                    <div className="animate-spin w-4 h-4 border-2 border-blue-600 border-t-transparent rounded-full"></div>
                  </div>
                </div>
                {sync.progress !== undefined && (
                  <div className="mt-3">
                    <div className="w-full bg-gray-200 rounded-full h-2">
                      <div 
                        className="bg-blue-600 h-2 rounded-full transition-all duration-300"
                        style={{ width: `${sync.progress}%` }}
                      ></div>
                    </div>
                    <div className="text-xs text-gray-600 mt-1 text-right">{sync.progress}%</div>
                  </div>
                )}
              </div>
            ))}
          </div>
        ) : (
          <div className="text-gray-500 text-sm py-2">No active syncs</div>
        )}
      </div>

      {/* Lists Overview Widget */}
  <div className="relative z-10 bg-white/90 backdrop-blur-xl rounded-2xl shadow-xl border border-indigo-100 p-4 md:p-6 flex flex-col gap-2 transition-all duration-500">
        <h4 className="font-semibold text-gray-900 mb-3 text-sm">Lists Overview</h4>
        <div className="grid grid-cols-2 gap-4">
          <div className="text-center">
            <div className="text-xl md:text-2xl font-bold text-indigo-600">
              {syncStatus?.total_lists || 0}
            </div>
            <div className="text-xs text-gray-600 mt-1">Total Lists</div>
          </div>
          <div className="text-center">
            <div className="text-xl md:text-2xl font-bold text-green-600">
              {syncStatus?.completed_today || 0}
            </div>
            <div className="text-xs text-gray-600 mt-1">Synced Today</div>
          </div>
        </div>
        
        <div className="mt-3 pt-3 border-t border-gray-200">
          <div className="text-sm text-gray-600 flex items-center gap-2">
            <span>Last sync:</span>
            <span className="font-medium">{formatLastSync(syncStatus?.last_sync || null)}</span>
            {refreshing && (
              <span className="inline-block w-3 h-3 border-2 border-gray-500 border-t-transparent rounded-full animate-spin" />
            )}
          </div>
        </div>
      </div>

      {/* Lists Quick Actions */}
  <div className="relative z-10 bg-white/90 backdrop-blur-xl rounded-2xl shadow-xl border border-indigo-100 p-4 md:p-6 flex flex-col gap-2 transition-all duration-500">
        <div className="flex items-center justify-between mb-3">
          <h4 className="font-semibold text-gray-900 text-sm">Lists Quick Actions</h4>
          <button
            onClick={fetchStatus}
            className="text-gray-400 hover:text-gray-600 text-sm p-1 rounded hover:bg-gray-100 transition-colors touch-manipulation flex items-center gap-1"
            title="Refresh"
            aria-busy={refreshing}
          >
            {refreshing ? (
              <span className="inline-block w-3 h-3 border-2 border-gray-500 border-t-transparent rounded-full animate-spin" />
            ) : '↻'}
          </button>
        </div>
  <QuickActions addToast={(msg, type)=>addToast({ message: msg, type: type || 'info' })} />
      </div>

      {/* System Health Widget */}
  <div className="relative z-10 bg-white/90 backdrop-blur-xl rounded-2xl shadow-xl border border-indigo-100 p-4 md:p-6 flex flex-col gap-2 transition-all duration-500">
        <h4 className="font-semibold text-gray-900 mb-3 text-sm">System Health</h4>
        
        {health ? (
          <div className="space-y-2">
            <div className="flex items-center justify-between text-sm py-1">
              <span>Redis</span>
              <span className={getHealthColor(health.redis)}>
                {getHealthIcon(health.redis)} {health.redis ? 'Online' : 'Offline'}
              </span>
            </div>
            <div className="flex items-center justify-between text-sm py-1">
              <span>Database</span>
              <span className={getHealthColor(health.database)}>
                {getHealthIcon(health.database)} {health.database ? 'Online' : 'Offline'}
              </span>
            </div>
            <div className="flex items-center justify-between text-sm py-1">
              <span>Celery</span>
              <span className={getHealthColor(health.celery)}>
                {getHealthIcon(health.celery)} {health.celery ? 'Online' : 'Offline'}
              </span>
            </div>
            <div className="flex items-center justify-between text-sm py-1">
              <span>Trakt API</span>
              <span className={getHealthColor(health.trakt_api)}>
                {getHealthIcon(health.trakt_api)} {health.trakt_api ? 'Online' : 'Offline'}
              </span>
            </div>
            <div className="flex items-center justify-between text-sm py-1">
              <span>TMDB API</span>
              <span className={getHealthColor(health.tmdb_api)}>
                {getHealthIcon(health.tmdb_api)} {health.tmdb_api ? 'Online' : 'Offline'}
              </span>
            </div>
          </div>
        ) : (
          <div className="text-gray-500 text-sm py-2">Unable to check health</div>
        )}
      </div>

      {/* Fusion Mode Widget */}
  <div className="relative z-10 bg-white/90 backdrop-blur-xl rounded-2xl shadow-xl border border-indigo-100 p-4 md:p-6 flex flex-col gap-2 transition-all duration-500">
        <h4 className="font-semibold text-gray-900 mb-3 text-sm">Fusion Mode</h4>
        {fusionStatus ? (
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-sm text-gray-600">Status</span>
              <span className={`text-sm font-medium ${fusionStatus.enabled ? 'text-green-600' : 'text-gray-500'}`}>
                {fusionStatus.enabled ? '● Enabled' : '○ Disabled'}
              </span>
            </div>
            {fusionStatus.enabled && (
              <>
                <div className="mt-3 pt-3 border-t border-gray-200">
                  <div className="text-xs text-gray-600 mb-2">Aggressiveness</div>
                  <div className="flex items-center gap-3">
                    <input
                      type="range"
                      min={0}
                      max={2}
                      step={0.01}
                      value={fusionAggressiveness}
                      onChange={e => handleAggressivenessChange(Number(e.target.value))}
                      disabled={fusionLoading}
                      className="w-full"
                    />
                    <span className="text-xs text-gray-700 w-12 text-right">
                      {fusionAggressiveness === 0 ? 'Low' : fusionAggressiveness === 2 ? 'High' : 'Med'}
                    </span>
                  </div>
                  <div className="text-xs text-gray-500 mt-1">Controls how strongly fusion mode influences recommendations.</div>
                </div>
                <div className="mt-3">
                  <div className="text-xs text-gray-600 mb-2">Active Components:</div>
                  <div className="grid grid-cols-2 gap-1 text-xs">
                    {Object.entries(liveWeights || fusionStatus.weights)
                      .filter(([_, weight]) => Number(weight) > 0.01)
                      .sort(([, a], [, b]) => Number(b) - Number(a))
                      .slice(0, 4)
                      .map(([component, weight]) => (
                        <div key={component} className="flex justify-between">
                          <span className="text-gray-600 truncate">
                            {component.replace('components.', '').replace(/^\w/, c => c.toUpperCase())}
                          </span>
                          <span className="text-gray-800 font-medium">
                            {(Number(weight) * 100).toFixed(0)}%
                          </span>
                        </div>
                      ))}
                  </div>
                </div>
              </>
            )}
          </div>
        ) : (
          <div className="text-gray-500 text-sm py-2">Unable to check fusion status</div>
        )}
      </div>
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

  if(loading) return <div className="text-gray-500 text-sm">Loading…</div>;
  if(!lists.length) return <div className="text-gray-500 text-sm">No lists yet</div>;

  return (
    <div className="space-y-3">
      <div className="text-xs text-gray-600 mb-2">
        {lists.length} list{lists.length !== 1 ? 's' : ''} total
      </div>
      <button
        onClick={syncAllWatchedOnly}
        disabled={syncing}
        className={`w-full px-3 py-2 rounded-lg text-sm font-medium transition-all ${
          syncing
            ? 'bg-gray-400 text-white cursor-not-allowed'
            : 'bg-blue-600 text-white hover:bg-blue-700 active:scale-95'
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