import React, { useState, useEffect } from "react";
import { api } from "../hooks/useApi";

interface FusionSettings {
  enabled: boolean;
  weights: Record<string, number>;
}

interface TraktCredentials {
  configured: boolean;
  has_client_id: boolean;
  has_client_secret: boolean;
}

interface TMDBKeyStatus {
  configured: boolean;
  key_preview?: string;
}

interface TimezoneGroup {
  group: string;
  zones: Array<{id: string; label: string}>;
}

interface TimezoneSettings {
  timezone: string;
  available_timezones: TimezoneGroup[];
}

export default function Settings() {
  const [tmdbKey, setTmdbKey] = useState("");
  const [traktCreds, setTraktCreds] = useState<TraktCredentials | null>(null);
  const [tmdbStatus, setTmdbStatus] = useState<TMDBKeyStatus | null>(null);
  const [fusionSettings, setFusionSettings] = useState<FusionSettings>({
    enabled: false,
    weights: {}
  });
  const [timezoneSettings, setTimezoneSettings] = useState<TimezoneSettings | null>(null);
  const [timezone, setTimezone] = useState("UTC");
  const [traktRedirectUri, setTraktRedirectUri] = useState("localhost");
  const [traktRedirectUriDisplay, setTraktRedirectUriDisplay] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState<string | null>(null);

  useEffect(() => {
    loadSettings();
  }, []);

  async function loadSettings() {
    try {
      setLoading(true);
      
      // Load all settings in parallel
      const [fusionRes, traktRes, tmdbRes, timezoneRes, redirectUriRes] = await Promise.all([
        api.get("/settings/fusion").catch(() => ({ data: { enabled: false, weights: {} } })),
        api.get("/settings/trakt-credentials").catch(() => ({ data: { configured: false } })),
        api.get("/settings/tmdb-key").catch(() => ({ data: { configured: false } })),
        api.get("/settings/timezone").catch(() => ({ data: { timezone: "UTC", available_timezones: [] } })),
        api.get("/trakt/redirect-uri").catch(() => ({ data: { redirect_base: "localhost", full_redirect_uri: "http://localhost:5173/auth/trakt/callback" } }))
      ]);
      
      setFusionSettings(fusionRes.data);
      setTraktCreds(traktRes.data);
      setTmdbStatus(tmdbRes.data);
      setTimezoneSettings(timezoneRes.data);
      setTimezone(timezoneRes.data.timezone || "UTC");
      setTraktRedirectUri(redirectUriRes.data.redirect_base || "localhost");
      setTraktRedirectUriDisplay(redirectUriRes.data.full_redirect_uri || "");
    } catch (e) {
      console.error("Failed to load settings:", e);
    } finally {
      setLoading(false);
    }
  }

  async function reauthorizeTrakt() {
    try {
      setSaving("trakt");
      await api.post("/settings/reauthorize-trakt");
      
      // Redirect to setup for re-authorization
      window.location.href = "/";
    } catch (e: any) {
      alert("Failed to clear authorization: " + (e?.response?.data?.detail || e.message));
    } finally {
      setSaving(null);
    }
  }

  async function saveTmdbKey() {
    if (!tmdbKey.trim()) {
      alert("Please enter a TMDB API key");
      return;
    }
    
    try {
      setSaving("tmdb");
      await api.post("/settings/tmdb-key", { api_key: tmdbKey });
      setTmdbKey("");
      await loadSettings(); // Refresh status
      alert("TMDB API key saved and validated successfully!");
    } catch (e: any) {
      alert("Failed to save TMDB key: " + (e?.response?.data?.detail || e.message));
    } finally {
      setSaving(null);
    }
  }

  async function deleteTmdbKey() {
    if (!confirm("Are you sure you want to delete your TMDB API key?")) return;
    
    try {
      setSaving("tmdb");
      await api.delete("/settings/tmdb-key");
      await loadSettings(); // Refresh status
      alert("TMDB API key deleted successfully");
    } catch (e: any) {
      alert("Failed to delete TMDB key: " + (e?.response?.data?.detail || e.message));
    } finally {
      setSaving(null);
    }
  }

  async function saveTimezone() {
    try {
      setSaving("timezone");
      await api.post("/settings/timezone", { timezone });
      if (timezoneSettings) {
        setTimezoneSettings({ ...timezoneSettings, timezone });
      }
    } catch (error: any) {
      alert("Failed to save timezone: " + (error?.response?.data?.detail || error.message));
    } finally {
      setSaving(null);
    }
  }

  async function saveTraktRedirectUri() {
    if (!traktRedirectUri.trim()) {
      alert("Please enter a redirect URI (or leave as 'localhost' for default)");
      return;
    }
    
    try {
      setSaving("trakt-redirect");
      const response = await api.post("/trakt/redirect-uri", { redirect_uri: traktRedirectUri });
      setTraktRedirectUriDisplay(response.data.full_redirect_uri);
      alert("Trakt redirect URI saved successfully!");
    } catch (error: any) {
      alert("Failed to save redirect URI: " + (error?.response?.data?.detail || error.message));
    } finally {
      setSaving(null);
    }
  }

  if (loading) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-fuchsia-200 via-indigo-100 to-blue-200 flex items-center justify-center">
        <div className="text-center">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-indigo-600 mx-auto mb-4"></div>
          <p className="text-indigo-700">Loading settings...</p>
        </div>
      </div>
    );
  }

    return (
      <div className="relative z-10 bg-white/90 backdrop-blur-xl rounded-3xl shadow-2xl border border-indigo-100 w-full max-w-xl mx-auto p-6 md:p-8 flex flex-col gap-4 transition-all duration-500">
      <div className="max-w-4xl mx-auto">
        {/* Header */}
        <div className="text-center mb-8">
          <h1 className="text-4xl font-extrabold text-indigo-900 mb-2 tracking-tight">
            Settings
          </h1>
          <p className="text-indigo-700 text-lg">Manage your WatchBuddy configuration</p>
        </div>

        <div className="space-y-8">
          {/* Trakt Integration Card */}
          <div className="bg-white/90 backdrop-blur-xl rounded-3xl shadow-2xl border border-indigo-100 p-8">
            <div className="flex items-center gap-3 mb-6">
              <div className="w-12 h-12 bg-gradient-to-tr from-red-400 via-fuchsia-400 to-indigo-400 rounded-full flex items-center justify-center">
                <svg className="w-6 h-6 text-white" fill="currentColor" viewBox="0 0 24 24">
                  <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z"/>
                </svg>
              </div>
              <div>
                <h2 className="text-2xl font-bold text-indigo-900">Trakt Integration</h2>
                <p className="text-indigo-600">Manage your Trakt authentication</p>
              </div>
            </div>

            <div className="bg-gradient-to-r from-blue-50 via-indigo-50 to-fuchsia-50 rounded-xl p-6 mb-6">
              <div className="flex items-center justify-between">
                <div>
                  <h3 className="font-semibold text-indigo-900 mb-2">Authorization Status</h3>
                  <div className="space-y-1">
                    <div className="flex items-center gap-2">
                      <div className={`w-3 h-3 rounded-full ${traktCreds?.configured ? 'bg-green-500' : 'bg-gray-300'}`}></div>
                      <span className="text-sm text-indigo-700">
                        Credentials: {traktCreds?.configured ? 'Configured' : 'Not Set'}
                      </span>
                    </div>
                  </div>
                </div>
                <button
                  onClick={reauthorizeTrakt}
                  disabled={saving === "trakt"}
                  className="bg-gradient-to-r from-red-500 via-fuchsia-500 to-indigo-500 text-white px-6 py-3 rounded-lg font-semibold hover:from-red-600 hover:to-indigo-600 transition-all duration-200 shadow-md hover:shadow-lg disabled:opacity-50"
                >
                  {saving === "trakt" ? "Clearing..." : "Re-authorize Trakt"}
                </button>
              </div>
            </div>

            <div className="text-sm text-gray-600">
              <p>Re-authorization will clear your current Trakt connection and redirect you to set up new API credentials.</p>
            </div>
          </div>

          {/* Trakt Redirect URI Card */}
          <div className="bg-white/90 backdrop-blur-xl rounded-3xl shadow-2xl border border-indigo-100 p-8">
            <div className="flex items-center gap-3 mb-6">
              <div className="w-12 h-12 bg-gradient-to-tr from-purple-400 via-fuchsia-400 to-pink-400 rounded-full flex items-center justify-center">
                <svg className="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1" />
                </svg>
              </div>
              <div>
                <h2 className="text-2xl font-bold text-indigo-900">Trakt Redirect URI</h2>
                <p className="text-indigo-600">Configure OAuth callback for Trakt authentication</p>
              </div>
            </div>

            <div className="bg-gradient-to-r from-purple-50 via-fuchsia-50 to-pink-50 rounded-xl p-6 mb-6">
              <div className="space-y-4">
                <div>
                  <label htmlFor="redirect-uri-input" className="block text-sm font-medium text-indigo-900 mb-2">
                    Base Domain/IP
                  </label>
                  <input
                    id="redirect-uri-input"
                    type="text"
                    value={traktRedirectUri}
                    onChange={(e) => setTraktRedirectUri(e.target.value)}
                    placeholder="localhost (default) or example.com or 192.168.1.100"
                    className="w-full px-4 py-3 border border-indigo-200 rounded-lg focus:ring-2 focus:ring-fuchsia-400 focus:border-fuchsia-400 transition-all duration-200 shadow-sm"
                  />
                  {traktRedirectUriDisplay && (
                    <p className="text-xs text-indigo-600 mt-2">
                      Full callback URL: <span className="font-mono bg-white px-2 py-1 rounded">{traktRedirectUriDisplay}</span>
                    </p>
                  )}
                </div>

                <div className="flex justify-between items-center">
                  <div className="text-sm text-indigo-700">
                    <p>Set the domain or IP for OAuth callbacks. Default is "localhost" for local development.</p>
                  </div>
                  <button
                    onClick={saveTraktRedirectUri}
                    disabled={saving === "trakt-redirect"}
                    className="bg-gradient-to-r from-purple-500 via-fuchsia-500 to-pink-500 text-white px-6 py-3 rounded-lg font-semibold hover:from-purple-600 hover:to-pink-600 transition-all duration-200 shadow-md hover:shadow-lg disabled:opacity-50"
                  >
                    {saving === "trakt-redirect" ? "Saving..." : "Save URI"}
                  </button>
                </div>
              </div>
            </div>

            <div className="text-sm text-gray-600 space-y-2">
              <p><strong>Important:</strong> Make sure this matches the redirect URI registered in your Trakt application settings.</p>
              <p className="text-xs">Examples: <code className="bg-gray-100 px-1 rounded">localhost</code>, <code className="bg-gray-100 px-1 rounded">example.com</code>, <code className="bg-gray-100 px-1 rounded">192.168.1.100</code></p>
            </div>
          </div>

          {/* TMDB API Card */}
          <div className="bg-white/90 backdrop-blur-xl rounded-3xl shadow-2xl border border-indigo-100 p-8">
            <div className="flex items-center gap-3 mb-6">
              <div className="w-12 h-12 bg-gradient-to-tr from-green-400 via-blue-400 to-indigo-400 rounded-full flex items-center justify-center">
                <svg className="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 4V2a1 1 0 011-1h3a1 1 0 011 1v2h4a1 1 0 011 1v3a1 1 0 01-1 1h-2v9a1 1 0 01-1 1H8a1 1 0 01-1-1V9H5a1 1 0 01-1-1V5a1 1 0 011-1h2z" />
                </svg>
              </div>
              <div>
                <h2 className="text-2xl font-bold text-indigo-900">TMDB API Key</h2>
                <p className="text-indigo-600">Enhanced movie & show metadata</p>
              </div>
            </div>

            {tmdbStatus?.configured ? (
              <div className="bg-gradient-to-r from-green-50 to-blue-50 rounded-xl p-6 mb-6">
                <div className="flex items-center justify-between">
                  <div>
                    <h3 className="font-semibold text-green-900 mb-1">API Key Configured</h3>
                    {tmdbStatus.key_preview && (
                      <p className="text-sm text-green-700">Key: {tmdbStatus.key_preview}</p>
                    )}
                  </div>
                  <button
                    onClick={deleteTmdbKey}
                    disabled={saving === "tmdb"}
                    className="bg-red-500 text-white px-4 py-2 rounded-lg font-medium hover:bg-red-600 transition-colors disabled:opacity-50"
                  >
                    {saving === "tmdb" ? "Deleting..." : "Delete Key"}
                  </button>
                </div>
              </div>
            ) : (
              <div className="space-y-4">
                <div className="bg-gradient-to-r from-yellow-50 to-orange-50 rounded-xl p-6">
                  <h3 className="font-semibold text-orange-900 mb-2">TMDB API Key Required</h3>
                  <p className="text-sm text-orange-700">
                    Get your free API key from{" "}
                    <a href="https://www.themoviedb.org/settings/api" target="_blank" rel="noopener noreferrer" className="underline">
                      TMDB Settings
                    </a>
                  </p>
                </div>
                
                <div className="flex gap-3">
                  <input
                    type="password"
                    value={tmdbKey}
                    onChange={(e) => setTmdbKey(e.target.value)}
                    placeholder="Enter your TMDB API key..."
                    className="flex-1 px-4 py-3 border border-indigo-200 rounded-lg focus:ring-2 focus:ring-fuchsia-400 focus:border-fuchsia-400 transition-all duration-200 shadow-sm"
                  />
                  <button
                    onClick={saveTmdbKey}
                    disabled={saving === "tmdb" || !tmdbKey.trim()}
                    className="bg-gradient-to-r from-green-500 via-blue-500 to-indigo-500 text-white px-6 py-3 rounded-lg font-semibold hover:from-green-600 hover:to-indigo-600 transition-all duration-200 shadow-md hover:shadow-lg disabled:opacity-50"
                  >
                    {saving === "tmdb" ? "Saving..." : "Save Key"}
                  </button>
                </div>
              </div>
            )}
          </div>

          {/* Timezone Settings Card */}
          <div className="bg-white/90 backdrop-blur-xl rounded-3xl shadow-2xl border border-indigo-100 p-8">
            <div className="flex items-center gap-3 mb-6">
              <div className="w-12 h-12 bg-gradient-to-tr from-orange-400 via-pink-400 to-indigo-400 rounded-full flex items-center justify-center">
                <svg className="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
              </div>
              <div>
                <h2 className="text-2xl font-bold text-indigo-900">Timezone Settings</h2>
                <p className="text-indigo-600">Configure your local timezone for better recommendations</p>
              </div>
            </div>

            <div className="bg-gradient-to-r from-orange-50 via-pink-50 to-indigo-50 rounded-xl p-6 mb-6">
              <div className="space-y-4">
                <div>
                  <label htmlFor="timezone-select" className="block text-sm font-medium text-indigo-900 mb-2">
                    Current Timezone
                  </label>
                  <select
                    id="timezone-select"
                    value={timezone}
                    onChange={(e) => setTimezone(e.target.value)}
                    className="w-full px-4 py-3 border border-indigo-200 rounded-lg focus:ring-2 focus:ring-pink-400 focus:border-pink-400 transition-all duration-200 shadow-sm bg-white"
                  >
                    <optgroup label="Americas">
                      <option value="America/New_York">Eastern Time (New York)</option>
                      <option value="America/Chicago">Central Time (Chicago)</option>
                      <option value="America/Denver">Mountain Time (Denver)</option>
                      <option value="America/Los_Angeles">Pacific Time (Los Angeles)</option>
                      <option value="America/Toronto">Eastern Time (Toronto)</option>
                      <option value="America/Vancouver">Pacific Time (Vancouver)</option>
                      <option value="America/Mexico_City">Central Time (Mexico City)</option>
                      <option value="America/Sao_Paulo">Brazil Time (São Paulo)</option>
                      <option value="America/Argentina/Buenos_Aires">Argentina Time (Buenos Aires)</option>
                      <option value="America/Lima">Peru Time (Lima)</option>
                    </optgroup>
                    <optgroup label="Europe">
                      <option value="Europe/London">Greenwich Mean Time (London)</option>
                      <option value="Europe/Paris">Central European Time (Paris)</option>
                      <option value="Europe/Berlin">Central European Time (Berlin)</option>
                      <option value="Europe/Rome">Central European Time (Rome)</option>
                      <option value="Europe/Madrid">Central European Time (Madrid)</option>
                      <option value="Europe/Copenhagen">Central European Time (Copenhagen)</option>
                      <option value="Europe/Stockholm">Central European Time (Stockholm)</option>
                      <option value="Europe/Oslo">Central European Time (Oslo)</option>
                      <option value="Europe/Helsinki">Eastern European Time (Helsinki)</option>
                      <option value="Europe/Moscow">Moscow Standard Time</option>
                      <option value="Europe/Zurich">Central European Time (Zurich)</option>
                      <option value="Europe/Amsterdam">Central European Time (Amsterdam)</option>
                    </optgroup>
                    <optgroup label="Asia & Pacific">
                      <option value="Asia/Tokyo">Japan Standard Time (Tokyo)</option>
                      <option value="Asia/Seoul">Korea Standard Time (Seoul)</option>
                      <option value="Asia/Shanghai">China Standard Time (Shanghai)</option>
                      <option value="Asia/Hong_Kong">Hong Kong Time</option>
                      <option value="Asia/Singapore">Singapore Standard Time</option>
                      <option value="Asia/Bangkok">Indochina Time (Bangkok)</option>
                      <option value="Asia/Mumbai">India Standard Time (Mumbai)</option>
                      <option value="Asia/Dubai">Gulf Standard Time (Dubai)</option>
                      <option value="Australia/Sydney">Australian Eastern Time (Sydney)</option>
                      <option value="Australia/Melbourne">Australian Eastern Time (Melbourne)</option>
                      <option value="Australia/Perth">Australian Western Time (Perth)</option>
                      <option value="Pacific/Auckland">New Zealand Standard Time (Auckland)</option>
                    </optgroup>
                  </select>
                </div>

                <div className="flex justify-between items-center">
                  <div className="text-sm text-indigo-700">
                    <p>This helps improve mood-based recommendations by considering your local time context.</p>
                  </div>
                  <button
                    onClick={saveTimezone}
                    disabled={saving === "timezone"}
                    className="bg-gradient-to-r from-orange-500 via-pink-500 to-indigo-500 text-white px-6 py-3 rounded-lg font-semibold hover:from-orange-600 hover:to-indigo-600 transition-all duration-200 shadow-md hover:shadow-lg disabled:opacity-50"
                  >
                    {saving === "timezone" ? "Saving..." : "Save Timezone"}
                  </button>
                </div>
              </div>
            </div>

            <div className="text-sm text-gray-600">
              <p>Your timezone setting helps WatchBuddy provide more relevant recommendations based on your local time of day and viewing patterns.</p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
