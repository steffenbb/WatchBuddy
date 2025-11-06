import React, { useState, useEffect } from "react";
let useNavigate: any = undefined;
try {
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  useNavigate = require("react-router-dom").useNavigate;
} catch (e) {
  useNavigate = undefined;
}
import { useToast } from "./ToastProvider";

interface SetupScreenProps {
  onTraktConnect: () => void;
}

const SetupScreen: React.FC<SetupScreenProps> = ({ onTraktConnect }) => {
  const navigate = typeof useNavigate === 'function' ? useNavigate() : null;
  const [step, setStep] = useState<'credentials' | 'tmdb' | 'oauth'>('credentials');
  const [isConnecting, setIsConnecting] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [isValidating, setIsValidating] = useState(false);
  const [authStatus, setAuthStatus] = useState<{authenticated: boolean, user: any} | null>(null);
  const [credentials, setCredentials] = useState({
    clientId: '',
    clientSecret: '',
    tmdbApiKey: ''
  });
  const [traktRedirectUri, setTraktRedirectUri] = useState(() => {
    // Auto-detect current host for redirect URI (never use 'localhost' as it breaks mobile)
    if (typeof window !== 'undefined') {
      return window.location.hostname;
    }
    return 'localhost';
  });
  const [hasCredentials, setHasCredentials] = useState(false);
  const [hasTmdbKey, setHasTmdbKey] = useState(false);
  const { addToast } = useToast();

  const [backendDown, setBackendDown] = useState(false);
  useEffect(() => {
    checkSetupStatus();
  }, []);

  useEffect(() => {
    // Only redirect if BOTH services are configured and authenticated
    if (authStatus?.authenticated && hasTmdbKey) {
      validateAndRedirect();
    }
  }, [authStatus, hasTmdbKey, onTraktConnect, navigate]);

  const checkSetupStatus = async () => {
    try {
      // Check if Trakt credentials are configured
      const credsResponse = await fetch('/api/settings/trakt-credentials');
      if (credsResponse.ok) {
        const credsData = await credsResponse.json();
        if (credsData.configured) {
          setHasCredentials(true);
        }
      }

      // Check if TMDB key is configured
      const tmdbResponse = await fetch('/api/settings/tmdb-key');
      if (tmdbResponse.ok) {
        const tmdbData = await tmdbResponse.json();
        if (tmdbData.configured) {
          setHasTmdbKey(true);
        }
      }

      // Check auth status
      const authResponse = await fetch('/api/trakt/status');
      const authData = await authResponse.json();
      setAuthStatus(authData);

      // Determine which step to show
      if (!hasCredentials) {
        setStep('credentials');
      } else if (!hasTmdbKey) {
        setStep('tmdb');
      } else if (!authData.authenticated) {
        setStep('oauth');
      }
    } catch (error) {
      setBackendDown(true);
      console.error('Failed to check setup status:', error);
    }
  };

  const validateAndRedirect = async () => {
    setIsValidating(true);
    try {
      const response = await fetch('/api/settings/validate-setup');
      const data = await response.json();
      
      if (data.valid) {
        addToast({ message: 'Setup complete! Initializing metadata...', type: 'success' });
        
        // Trigger metadata build
        try {
          await fetch('/api/metadata/build/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ user_id: 1 })
          });
        } catch (error) {
          console.error('Failed to start metadata build:', error);
        }
        
        setTimeout(() => {
          if (onTraktConnect) onTraktConnect();
          if (navigate) navigate("/dashboard");
        }, 1200);
      } else {
        addToast({ 
          message: `Setup incomplete: ${data.errors.join(', ')}`, 
          type: 'error' 
        });
        // Go back to appropriate step
        if (!data.trakt_configured) {
          setStep('credentials');
        } else if (!data.tmdb_configured) {
          setStep('tmdb');
        } else if (!data.trakt_authenticated) {
          setStep('oauth');
        }
      }
    } catch (error) {
      addToast({ message: 'Failed to validate setup', type: 'error' });
    } finally {
      setIsValidating(false);
    }
  };

  const saveCredentials = async () => {
    if (!credentials.clientId.trim() || !credentials.clientSecret.trim()) {
      addToast({ message: 'Please provide both Client ID and Client Secret', type: 'error' });
      return;
    }

    setIsSaving(true);
    try {
      // Save redirect URI first
      if (traktRedirectUri.trim()) {
        await fetch('/api/trakt/redirect-uri', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            redirect_uri: traktRedirectUri.trim()
          })
        });
      }

      // Then save credentials
      const response = await fetch('/api/settings/trakt-credentials', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          client_id: credentials.clientId.trim(),
          client_secret: credentials.clientSecret.trim()
        })
      });

      if (response.ok) {
        addToast({ message: 'Trakt credentials saved!', type: 'success' });
        setHasCredentials(true);
        setStep('tmdb');
      } else {
        throw new Error('Failed to save credentials');
      }
    } catch (error) {
      addToast({ message: 'Failed to save credentials. Please try again.', type: 'error' });
    } finally {
      setIsSaving(false);
    }
  };

  const saveTmdbKey = async () => {
    if (!credentials.tmdbApiKey.trim()) {
      addToast({ message: 'Please provide TMDB API key', type: 'error' });
      return;
    }

    setIsSaving(true);
    try {
      const response = await fetch('/api/settings/tmdb-key', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          api_key: credentials.tmdbApiKey.trim()
        })
      });

      if (response.ok) {
        addToast({ message: 'TMDB API key validated and saved!', type: 'success' });
        setHasTmdbKey(true);
        setStep('oauth');
      } else {
        const errorData = await response.json();
        throw new Error(errorData.detail || 'Invalid TMDB API key');
      }
    } catch (error: any) {
      addToast({ message: error.message || 'Failed to save TMDB key', type: 'error' });
    } finally {
      setIsSaving(false);
    }
  };

  const handleTraktConnect = async () => {
    setIsConnecting(true);
    
    try {
      const response = await fetch('/api/trakt/oauth/url');
      const data = await response.json();
      
      if (data.auth_url) {
        window.location.href = data.auth_url;
      } else {
        throw new Error('Failed to get OAuth URL');
      }
    } catch (error) {
      console.error('Failed to initiate OAuth:', error);
      addToast({ message: 'Failed to connect to Trakt. Please try again.', type: 'error' });
      setIsConnecting(false);
    }
  };

  const renderProgressIndicator = () => {
    const steps = [
      { key: 'credentials', label: 'Trakt API', completed: hasCredentials },
      { key: 'tmdb', label: 'TMDB API', completed: hasTmdbKey },
      { key: 'oauth', label: 'Connect', completed: authStatus?.authenticated }
    ];

    return (
      <div className="mb-6">
        <div className="flex items-center justify-between">
          {steps.map((s, idx) => (
            <React.Fragment key={s.key}>
              <div className="flex flex-col items-center">
                <div className={`w-10 h-10 rounded-full flex items-center justify-center font-bold text-sm transition-all duration-300 ${
                  s.completed ? 'bg-green-500 text-white' :
                  step === s.key ? 'bg-fuchsia-500 text-white' :
                  'bg-gray-200 text-gray-500'
                }`}>
                  {s.completed ? '✓' : idx + 1}
                </div>
                <span className="text-xs mt-1 text-gray-600">{s.label}</span>
              </div>
              {idx < steps.length - 1 && (
                <div className={`flex-1 h-1 mx-2 rounded transition-all duration-300 ${
                  steps[idx + 1].completed ? 'bg-green-500' : 'bg-gray-200'
                }`} />
              )}
            </React.Fragment>
          ))}
        </div>
      </div>
    );
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-fuchsia-200 via-indigo-100 to-blue-200 flex items-center justify-center p-2 md:p-8">
      <div className="relative w-full max-w-2xl">
        {backendDown && (
          <div className="absolute top-4 left-1/2 -translate-x-1/2 z-50 w-full max-w-lg">
            <div className="bg-red-100 border border-red-300 text-red-800 rounded-lg px-4 py-3 shadow text-center">
              <div className="font-bold mb-1">Backend Unavailable</div>
              <div className="mb-2">The WatchBuddy backend API is currently unreachable. You can still access the dashboard, but some features may not work until the backend is restored.</div>
              <button
                className="mt-2 px-4 py-2 bg-fuchsia-500 text-white rounded hover:bg-fuchsia-600 font-semibold"
                onClick={() => {
                  if (onTraktConnect) onTraktConnect();
                  if (navigate) navigate("/dashboard");
                }}
              >Continue to Dashboard Anyway</button>
            </div>
          </div>
        )}
        {/* Animated background blobs */}
        <div className="absolute -top-16 -left-16 w-72 h-72 bg-gradient-to-tr from-fuchsia-400 via-indigo-300 to-blue-400 opacity-30 rounded-full blur-3xl animate-pulse" />
        <div className="absolute -bottom-16 -right-16 w-72 h-72 bg-gradient-to-br from-blue-400 via-indigo-200 to-fuchsia-300 opacity-20 rounded-full blur-3xl animate-pulse" />
        
        {/* Card */}
        <div className="relative z-10 bg-white/90 backdrop-blur-xl rounded-3xl shadow-2xl border border-indigo-100 overflow-hidden">
          {/* Header */}
          <div className="bg-gradient-to-br from-indigo-50 via-fuchsia-50 to-blue-50 p-8 text-center">
            <div className="w-20 h-20 bg-gradient-to-tr from-fuchsia-400 via-indigo-400 to-blue-400 rounded-full flex items-center justify-center mx-auto mb-4 shadow-lg">
              <svg className="w-10 h-10 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z" />
              </svg>
            </div>
            <h1 className="text-4xl font-extrabold text-indigo-900 mb-2">
              Welcome to <span className="text-fuchsia-600">WatchBuddy</span>
            </h1>
            <p className="text-indigo-700 text-lg">
              Let's get you set up in 3 quick steps
            </p>
          </div>

          {/* Content */}
          <div className="p-8">
            {renderProgressIndicator()}

            {/* Step: Trakt Credentials */}
            {step === 'credentials' && (
              <div className="space-y-4 animate-fade-in">
                <h2 className="text-2xl font-bold text-gray-900 mb-4">Step 1: Trakt API Setup</h2>
                <div className="bg-blue-50 border border-blue-200 rounded-lg p-4">
                  <h3 className="font-semibold text-blue-900 mb-2 flex items-center gap-2">
                    <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                    </svg>
                    Create Trakt API Application
                  </h3>
                  <ol className="space-y-2 text-sm text-blue-800">
                    <li>1. Visit <a href="https://trakt.tv/oauth/applications/new" target="_blank" rel="noopener noreferrer" className="underline">trakt.tv/oauth/applications/new</a></li>
                    <li>2. Set Redirect URI to: 
                      <div className="flex items-center gap-2 mt-1">
                        <code className="bg-blue-100 px-2 py-1 rounded text-xs">
                          {traktRedirectUri === 'localhost' ? 'http://localhost:5173' : `http://${traktRedirectUri}:5173`}/auth/trakt/callback
                        </code>
                        <button
                          onClick={() => {
                            const uri = traktRedirectUri === 'localhost' ? 'http://localhost:5173/auth/trakt/callback' : `http://${traktRedirectUri}:5173/auth/trakt/callback`;
                            navigator.clipboard.writeText(uri);
                            addToast({ message: 'Copied!', type: 'success' });
                          }}
                          className="px-2 py-1 text-xs bg-blue-200 hover:bg-blue-300 rounded"
                        >Copy</button>
                      </div>
                    </li>
                    <li>3. Copy your Client ID and Client Secret below</li>
                  </ol>
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-2">Client ID</label>
                  <input
                    type="text"
                    value={credentials.clientId}
                    onChange={(e) => setCredentials(prev => ({ ...prev, clientId: e.target.value }))}
                    placeholder="Your Trakt Client ID"
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-fuchsia-400"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-2">Client Secret</label>
                  <input
                    type="password"
                    value={credentials.clientSecret}
                    onChange={(e) => setCredentials(prev => ({ ...prev, clientSecret: e.target.value }))}
                    placeholder="Your Trakt Client Secret"
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-fuchsia-400"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-2">
                    Redirect URI Base (Optional)
                  </label>
                  <input
                    type="text"
                    value={traktRedirectUri}
                    onChange={(e) => setTraktRedirectUri(e.target.value)}
                    placeholder="localhost (default) or your domain/IP"
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-fuchsia-400"
                  />
                  <p className="text-xs text-gray-500 mt-1">
                    💡 Use "localhost" for local development, or enter your domain (e.g., "example.com") or IP (e.g., "192.168.1.100") for remote access
                  </p>
                </div>
                <button
                  onClick={saveCredentials}
                  disabled={isSaving || !credentials.clientId.trim() || !credentials.clientSecret.trim()}
                  className="w-full px-4 py-3 bg-gradient-to-r from-fuchsia-500 to-indigo-500 text-white rounded-lg font-semibold disabled:opacity-50 disabled:cursor-not-allowed hover:shadow-lg transition-all"
                >
                  {isSaving ? 'Saving...' : 'Continue to TMDB Setup'}
                </button>
              </div>
            )}

            {/* Step: TMDB API Key */}
            {step === 'tmdb' && (
              <div className="space-y-4 animate-fade-in">
                <h2 className="text-2xl font-bold text-gray-900 mb-4">Step 2: TMDB API Setup</h2>
                <div className="bg-amber-50 border border-amber-200 rounded-lg p-4">
                  <h3 className="font-semibold text-amber-900 mb-2 flex items-center gap-2">
                    <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                    </svg>
                    TMDB is Required
                  </h3>
                  <p className="text-sm text-amber-800 mb-2">
                    WatchBuddy uses TMDB to fetch movie and TV show metadata (posters, descriptions, ratings, etc.). This is essential for the app to work.
                  </p>
                  <ol className="space-y-2 text-sm text-amber-800">
                    <li>1. Visit <a href="https://www.themoviedb.org/settings/api" target="_blank" rel="noopener noreferrer" className="underline">themoviedb.org/settings/api</a></li>
                    <li>2. Request an API key (free for personal use)</li>
                    <li>3. Copy your API Key (v3 auth) below</li>
                  </ol>
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-2">TMDB API Key</label>
                  <input
                    type="text"
                    value={credentials.tmdbApiKey}
                    onChange={(e) => setCredentials(prev => ({ ...prev, tmdbApiKey: e.target.value }))}
                    placeholder="Your TMDB API Key"
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-amber-400"
                  />
                  <p className="text-xs text-gray-500 mt-1">
                    Your key will be validated before saving
                  </p>
                </div>
                <div className="flex gap-3">
                  <button
                    onClick={() => setStep('credentials')}
                    className="px-4 py-3 border border-gray-300 text-gray-700 rounded-lg font-semibold hover:bg-gray-50"
                  >
                    Back
                  </button>
                  <button
                    onClick={saveTmdbKey}
                    disabled={isSaving || !credentials.tmdbApiKey.trim()}
                    className="flex-1 px-4 py-3 bg-gradient-to-r from-amber-500 to-orange-500 text-white rounded-lg font-semibold disabled:opacity-50 disabled:cursor-not-allowed hover:shadow-lg transition-all"
                  >
                    {isSaving ? 'Validating...' : 'Validate & Continue'}
                  </button>
                </div>
              </div>
            )}

            {/* Step: OAuth Connection */}
            {step === 'oauth' && !authStatus?.authenticated && (
              <div className="space-y-4 animate-fade-in">
                <h2 className="text-2xl font-bold text-gray-900 mb-4">Step 3: Connect Your Account</h2>
                <div className="bg-green-50 border border-green-200 rounded-lg p-4">
                  <div className="flex items-start gap-3">
                    <div className="w-8 h-8 bg-green-100 rounded-full flex items-center justify-center flex-shrink-0">
                      <svg className="w-5 h-5 text-green-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                      </svg>
                    </div>
                    <div>
                      <p className="font-medium text-green-900">API Credentials Configured</p>
                      <p className="text-sm text-green-700">Now authorize WatchBuddy to access your Trakt account</p>
                    </div>
                  </div>
                </div>
                <div className="bg-gray-50 rounded-lg p-4">
                  <h3 className="font-semibold text-gray-900 mb-2">What happens next:</h3>
                  <ul className="space-y-2 text-sm text-gray-600">
                    <li className="flex items-start gap-2">
                      <span className="text-fuchsia-500">→</span>
                      You'll be redirected to Trakt.tv to authorize
                    </li>
                    <li className="flex items-start gap-2">
                      <span className="text-fuchsia-500">→</span>
                      After authorization, you'll return automatically
                    </li>
                    <li className="flex items-start gap-2">
                      <span className="text-fuchsia-500">→</span>
                      Setup complete! You'll be taken to your dashboard
                    </li>
                  </ul>
                </div>
                <div className="flex gap-3">
                  <button
                    onClick={() => setStep('tmdb')}
                    className="px-4 py-3 border border-gray-300 text-gray-700 rounded-lg font-semibold hover:bg-gray-50"
                  >
                    Back
                  </button>
                  <button
                    onClick={handleTraktConnect}
                    disabled={isConnecting}
                    className="flex-1 px-4 py-3 bg-gradient-to-r from-red-500 via-fuchsia-500 to-indigo-500 text-white rounded-lg font-semibold disabled:opacity-50 hover:shadow-lg transition-all"
                  >
                    {isConnecting ? 'Connecting...' : 'Connect to Trakt'}
                  </button>
                </div>
              </div>
            )}

            {/* Success State */}
            {authStatus?.authenticated && hasTmdbKey && (
              <div className="space-y-4 animate-fade-in">
                <div className="bg-gradient-to-r from-green-100 via-green-50 to-white border border-green-200 rounded-xl p-6">
                  <div className="flex items-center gap-4 mb-4">
                    <div className="w-12 h-12 bg-green-100 rounded-full flex items-center justify-center">
                      <svg className="w-6 h-6 text-green-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                      </svg>
                    </div>
                    <div>
                      <p className="text-xl font-bold text-green-900">Setup Complete!</p>
                      {authStatus.user?.username && (
                        <p className="text-sm text-green-700">Welcome, {authStatus.user.name || authStatus.user.username}!</p>
                      )}
                    </div>
                  </div>
                  <div className="bg-white rounded-lg p-4 space-y-2">
                    <div className="flex items-center gap-2 text-sm text-gray-700">
                      <svg className="w-4 h-4 text-green-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                      </svg>
                      Trakt API connected
                    </div>
                    <div className="flex items-center gap-2 text-sm text-gray-700">
                      <svg className="w-4 h-4 text-green-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                      </svg>
                      TMDB API configured
                    </div>
                    <div className="flex items-center gap-2 text-sm text-gray-700">
                      <svg className="w-4 h-4 text-green-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                      </svg>
                      Account authorized
                    </div>
                  </div>
                  {isValidating && (
                    <p className="text-sm text-green-700 mt-4 text-center">
                      Validating setup...
                    </p>
                  )}
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Footer */}
        <div className="text-center mt-6 text-xs text-gray-600">
          <p>Your credentials are stored locally and securely.</p>
          <p>Need help? Check the <a href="https://trakt.docs.apiary.io/" target="_blank" rel="noopener noreferrer" className="underline">Trakt</a> and <a href="https://developers.themoviedb.org/3" target="_blank" rel="noopener noreferrer" className="underline">TMDB</a> docs.</p>
        </div>
      </div>
    </div>
  );
};

export default SetupScreen;
