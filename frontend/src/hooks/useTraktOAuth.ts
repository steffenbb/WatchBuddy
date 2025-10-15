import { useEffect } from "react";

interface UseTraktOAuthOptions {
  onAuth?: () => void;
}

export function useTraktOAuth(opts?: UseTraktOAuthOptions) {
  // This hook will handle redirecting to Trakt and processing the callback
  const startOAuth = () => {
    fetch("/api/trakt/oauth/url")
      .then(res => res.json())
      .then(data => {
        const url = data.auth_url || data.url;
        if (url) {
          window.location.href = url;
        }
      });
  };

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const code = params.get("code");
    const state = params.get("state");
    if (code && state) {
      // Call backend Trakt auth callback with code+state as query params
      fetch(`/api/trakt/oauth/callback?code=${encodeURIComponent(code)}&state=${encodeURIComponent(state)}`, {
        method: "POST"
      })
        .then(res => res.json())
        .then(() => {
          // Clean URL
          window.history.replaceState({}, document.title, "/");
          if (opts?.onAuth) opts.onAuth();
        });
    }
  }, []);

  return { startOAuth };
}
