import axios from "axios";

// Use Vite's built-in env typing
const API_BASE = import.meta.env.VITE_API_URL || "/api";

// Create an axios instance that always includes user_id=1
export const api = axios.create({ baseURL: API_BASE, timeout: 10000 });

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


export async function getLists() {
  const r = await api.get("/lists/");
  return r.data;
}
