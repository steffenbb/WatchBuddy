import axios from "axios";

// Use Vite's built-in env typing
const API_BASE = import.meta.env.VITE_API_URL || "/api";

// Create an axios instance with 30s timeout (matching fetch-based client)
export const api = axios.create({ 
  baseURL: API_BASE, 
  timeout: 30000  // 30 seconds to match fetch client
});

// Add a request interceptor to always include user_id=1
api.interceptors.request.use((config) => {
  // For GET requests, add user_id=1 to params
  if (config.method === 'get') {
    config.params = config.params || {};
    config.params.user_id = 1;
  } else if (config.data && typeof config.data === 'object') {
    // For POST/PUT/PATCH/DELETE, add user_id=1 to body if it's an object
    config.data = { ...config.data, user_id: 1 };
  }
  return config;
});

// Add response interceptor for better error handling
api.interceptors.response.use(
  response => response,
  error => {
    // Enhance error with user-friendly messages
    if (error.code === 'ECONNABORTED' && error.message.includes('timeout')) {
      error.isTimeout = true;
      error.message = 'Request timeout - the server took too long to respond';
    } else if (error.response?.status === 429 || error.message?.toLowerCase().includes('rate limit')) {
      error.isRateLimit = true;
      error.message = 'Trakt API rate limit exceeded. Please wait a few minutes and try again.';
    }
    return Promise.reject(error);
  }
);


export async function getLists() {
  console.log('[useApi] getLists called, API_BASE:', API_BASE);
  const r = await api.get("/lists/");
  console.log('[useApi] getLists response:', r.status, r.data?.length);
  return r.data;
}
