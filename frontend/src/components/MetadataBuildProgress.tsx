import React, { useEffect, useState, useRef } from 'react';
import { apiGet } from '../api/client';

interface BuildStatus {
  status: 'not_started' | 'running' | 'complete' | 'partial' | 'error';
  total: number;
  processed: number;
  progress_percent: number;
  started_at?: string;
  updated_at?: string;
  errors: number;
  error_message?: string;
}

interface MetadataBuildProgressProps {
  onComplete?: () => void;
}

export const MetadataBuildProgress: React.FC<MetadataBuildProgressProps> = ({ onComplete }) => {
  const [buildStatus, setBuildStatus] = useState<BuildStatus | null>(null);
  const [timeElapsed, setTimeElapsed] = useState<string>('0s');
  const [isSkipping, setIsSkipping] = useState<boolean>(false);
  const hasStartedBuild = useRef<boolean>(false);

  const handleSkip = async () => {
    if (!confirm('Skip metadata building? The app will continue to map IDs in the background.')) {
      return;
    }

    setIsSkipping(true);
    try {
      await fetch('/api/metadata/skip', { method: 'POST' });
      if (onComplete) {
        onComplete();
      }
    } catch (error) {
      console.error('Failed to skip metadata build:', error);
      setIsSkipping(false);
    }
  };

  const startBuildIfNeeded = async (status: BuildStatus) => {
    // If status is not_started or if we're showing the screen but nothing is running
    if ((status.status === 'not_started' || status.status === 'error') && !hasStartedBuild.current) {
      try {
        console.log('Starting metadata build task...');
        await fetch('/api/metadata/build/start', { 
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ user_id: 1, force: false })
        });
        hasStartedBuild.current = true;
      } catch (error) {
        console.error('Failed to start metadata build:', error);
      }
    }
  };

  useEffect(() => {
    let interval: number;
    
    const fetchStatus = async () => {
      try {
        const status = await apiGet('/metadata/build/status') as BuildStatus;
        setBuildStatus(status);
        
        // Auto-start build if needed
        await startBuildIfNeeded(status);
        
        // If complete or partial (finished but not all mapped), notify parent
        if ((status.status === 'complete' || status.status === 'partial') && onComplete) {
          setTimeout(() => onComplete(), 2000); // Small delay to show 100%
        }
      } catch (error) {
        console.error('Failed to fetch build status:', error);
      }
    };

    // Fetch immediately
    fetchStatus();
    
    // Poll every 2 seconds while building
    interval = setInterval(fetchStatus, 2000);
    
    return () => clearInterval(interval);
  }, [onComplete]);

  useEffect(() => {
    if (!buildStatus?.started_at || buildStatus.status !== 'running') return;
    
    const startTime = new Date(buildStatus.started_at).getTime();
    
    const updateElapsed = () => {
      const now = Date.now();
      const elapsed = Math.floor((now - startTime) / 1000);
      
      const minutes = Math.floor(elapsed / 60);
      const seconds = elapsed % 60;
      
      if (minutes > 0) {
        setTimeElapsed(`${minutes}m ${seconds}s`);
      } else {
        setTimeElapsed(`${seconds}s`);
      }
    };
    
    updateElapsed();
    const timer = setInterval(updateElapsed, 1000);
    
    return () => clearInterval(timer);
  }, [buildStatus?.started_at, buildStatus?.status]);

  if (!buildStatus) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-indigo-900 via-purple-900 to-pink-900 flex items-center justify-center">
        <div className="animate-spin rounded-full h-16 w-16 border-t-4 border-b-4 border-white"></div>
      </div>
    );
  }

  const progressPercent = buildStatus.progress_percent || 0;
  const isComplete = buildStatus.status === 'complete' || buildStatus.status === 'partial';
  const hasError = buildStatus.status === 'error';
  const isRunning = buildStatus.status === 'running';

  // Calculate estimated time remaining
  let estimatedRemaining = '';
  if (buildStatus.status === 'running' && buildStatus.processed > 0 && buildStatus.started_at) {
    const startTime = new Date(buildStatus.started_at).getTime();
    const elapsed = (Date.now() - startTime) / 1000; // seconds
    const rate = buildStatus.processed / elapsed; // items per second
    const remaining = buildStatus.total - buildStatus.processed;
    const secondsRemaining = Math.ceil(remaining / rate);
    
    const mins = Math.floor(secondsRemaining / 60);
    const secs = secondsRemaining % 60;
    
    if (mins > 0) {
      estimatedRemaining = `~${mins}m ${secs}s remaining`;
    } else if (secs > 0) {
      estimatedRemaining = `~${secs}s remaining`;
    }
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-indigo-900 via-purple-900 to-pink-900 flex items-center justify-center p-6">
      <div className="max-w-2xl w-full">
        {/* Main Card */}
        <div className="bg-white/10 backdrop-blur-lg rounded-3xl shadow-2xl p-8 md:p-12 border border-white/20">
          {/* Icon/Status Indicator */}
          <div className="flex justify-center mb-8">
            {isComplete ? (
              <div className="relative">
                <div className="w-24 h-24 bg-green-500 rounded-full flex items-center justify-center animate-bounce">
                  <svg className="w-12 h-12 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M5 13l4 4L19 7" />
                  </svg>
                </div>
                <div className="absolute inset-0 w-24 h-24 bg-green-400 rounded-full animate-ping opacity-20"></div>
              </div>
            ) : hasError ? (
              <div className="w-24 h-24 bg-red-500 rounded-full flex items-center justify-center">
                <svg className="w-12 h-12 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </div>
            ) : (
              <div className="relative">
                <div className="w-24 h-24 bg-indigo-500 rounded-full flex items-center justify-center">
                  <svg className="w-12 h-12 text-white animate-spin" fill="none" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                  </svg>
                </div>
                <div className="absolute inset-0 w-24 h-24 bg-indigo-400 rounded-full animate-pulse opacity-20"></div>
              </div>
            )}
          </div>

          {/* Title */}
          <h1 className="text-4xl md:text-5xl font-bold text-white text-center mb-4">
            {isComplete ? 'Build Complete!' : hasError ? 'Build Error' : buildStatus.status === 'not_started' ? 'Initializing...' : 'Building Metadata'}
          </h1>

          {/* Subtitle */}
          <p className="text-white/80 text-center text-lg mb-8">
            {isComplete 
              ? 'Your movie and TV show database is ready!'
              : hasError
              ? 'Something went wrong during the build process'
              : buildStatus.status === 'not_started'
              ? 'Starting metadata enrichment task...'
              : 'Enriching your movie and TV show catalog with Trakt IDs...'}
          </p>

          {/* Progress Bar */}
          {!hasError && (
            <div className="mb-8">
              <div className="flex justify-between text-white/90 text-sm mb-2">
                <span className="font-semibold">Progress</span>
                <span className="font-mono font-bold">{progressPercent.toFixed(1)}%</span>
              </div>
              
              <div className="relative h-4 bg-white/20 rounded-full overflow-hidden backdrop-blur-sm">
                <div 
                  className="absolute inset-y-0 left-0 bg-gradient-to-r from-indigo-500 via-purple-500 to-pink-500 rounded-full transition-all duration-500 ease-out"
                  style={{ width: `${progressPercent}%` }}
                >
                  <div className="absolute inset-0 bg-white/30 animate-pulse"></div>
                </div>
              </div>

              <div className="flex justify-between text-white/70 text-xs mt-2">
                <span>{buildStatus.processed.toLocaleString()} / {buildStatus.total.toLocaleString()} items</span>
                {estimatedRemaining && <span>{estimatedRemaining}</span>}
              </div>
            </div>
          )}

          {/* Stats Grid */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
            <div className="bg-white/5 rounded-xl p-4 backdrop-blur-sm border border-white/10">
              <div className="text-white/60 text-xs uppercase tracking-wide mb-1">Total</div>
              <div className="text-white text-2xl font-bold">{buildStatus.total.toLocaleString()}</div>
            </div>
            
            <div className="bg-white/5 rounded-xl p-4 backdrop-blur-sm border border-white/10">
              <div className="text-white/60 text-xs uppercase tracking-wide mb-1">Processed</div>
              <div className="text-green-400 text-2xl font-bold">{buildStatus.processed.toLocaleString()}</div>
            </div>
            
            <div className="bg-white/5 rounded-xl p-4 backdrop-blur-sm border border-white/10">
              <div className="text-white/60 text-xs uppercase tracking-wide mb-1">Remaining</div>
              <div className="text-blue-400 text-2xl font-bold">
                {(buildStatus.total - buildStatus.processed).toLocaleString()}
              </div>
            </div>
            
            <div className="bg-white/5 rounded-xl p-4 backdrop-blur-sm border border-white/10">
              <div className="text-white/60 text-xs uppercase tracking-wide mb-1">Errors</div>
              <div className={`text-2xl font-bold ${buildStatus.errors > 0 ? 'text-yellow-400' : 'text-white'}`}>
                {buildStatus.errors.toLocaleString()}
              </div>
            </div>
          </div>

          {/* Time Elapsed */}
          {buildStatus.status === 'running' && (
            <div className="text-center text-white/60 text-sm">
              <div className="inline-flex items-center gap-2 bg-white/5 px-4 py-2 rounded-full backdrop-blur-sm">
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
                <span>Time elapsed: {timeElapsed}</span>
              </div>
            </div>
          )}

          {/* Error Message */}
          {hasError && buildStatus.error_message && (
            <div className="mt-6 bg-red-500/20 border border-red-500/50 rounded-xl p-4">
              <p className="text-red-200 text-sm font-mono">{buildStatus.error_message}</p>
            </div>
          )}

          {/* Completion Message */}
          {isComplete && (
            <div className="mt-6 text-center">
              <div className="inline-flex items-center gap-2 text-green-400 bg-green-500/20 px-6 py-3 rounded-full backdrop-blur-sm border border-green-500/30">
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
                <span className="font-semibold">Redirecting to dashboard...</span>
              </div>
            </div>
          )}

          {/* Skip Button with Enhanced Disclaimer */}
          {isRunning && !isSkipping && (
            <div className="mt-6 text-center">
              <button
                onClick={handleSkip}
                className="px-6 py-3 bg-white/10 hover:bg-white/20 text-white rounded-xl border border-white/30 transition-all duration-200 hover:scale-105 font-semibold"
              >
                Skip and Continue to App
              </button>
              <div className="mt-4 bg-yellow-500/10 border border-yellow-500/30 rounded-xl p-4 max-w-md mx-auto">
                <p className="text-yellow-200 text-sm font-medium mb-2">⚠️ Note: App may run slower initially</p>
                <p className="text-white/60 text-xs">
                  Skipping will allow you to use the app now, but recommendations and syncing may be slower until all items are enriched in the background. This process can take 30-60 minutes depending on your database size.
                </p>
              </div>
            </div>
          )}
        </div>

        {/* Info Footer with Performance Note */}
        <div className="mt-6 text-center text-white/50 text-sm space-y-2">
          <p>This process enriches your catalog with Trakt IDs for accurate watch tracking and faster syncing.</p>
          <p>Recommended: Let it complete for optimal performance. You can safely close this window - the build will continue in the background.</p>
        </div>
      </div>
    </div>
  );
};

export default MetadataBuildProgress;
