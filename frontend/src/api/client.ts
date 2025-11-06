const API_BASE = import.meta.env.VITE_API_URL || "/api";

// Default timeout for API calls (30 seconds)
const DEFAULT_TIMEOUT = 30000;

interface ApiError {
  message: string;
  status?: number;
  isRateLimit?: boolean;
  isTimeout?: boolean;
}

async function fetchWithTimeout(url: string, options: RequestInit = {}, timeout: number = DEFAULT_TIMEOUT): Promise<Response> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeout);

  try {
    const response = await fetch(url, {
      ...options,
      signal: controller.signal,
    });
    clearTimeout(timeoutId);
    return response;
  } catch (error: any) {
    clearTimeout(timeoutId);
    if (error.name === 'AbortError') {
      const timeoutError = new Error('Request timeout - the server took too long to respond') as any;
      timeoutError.isTimeout = true;
      throw timeoutError;
    }
    throw error;
  }
}

async function handleResponse(res: Response): Promise<any> {
  if (!res.ok) {
    let errorMessage = "API error";
    let errorData: any = {};
    
    try {
      errorData = await res.json();
      errorMessage = errorData.detail || errorData.message || errorMessage;
    } catch {
      // Response might not be JSON
      errorMessage = `API error: ${res.status} ${res.statusText}`;
    }

    const error = new Error(errorMessage) as any;
    error.status = res.status;
    
    // Detect rate limit errors
    if (res.status === 429 || errorMessage.toLowerCase().includes('rate limit')) {
      error.isRateLimit = true;
      error.message = errorMessage.includes('Trakt')
        ? 'Trakt API rate limit exceeded. Please wait a few minutes and try again.'
        : 'Too many requests. Please wait a moment and try again.';
    }
    
    throw error;
  }
  
  // Handle empty responses
  const contentType = res.headers.get("content-type");
  if (contentType && contentType.includes("application/json")) {
    return res.json();
  }
  
  return null;
}

export async function apiGet(path: string, timeout?: number) {
  const res = await fetchWithTimeout(`${API_BASE}${path}`, {}, timeout);
  return handleResponse(res);
}

export async function apiPost(path: string, body: any, timeout?: number) {
  const res = await fetchWithTimeout(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }, timeout);
  return handleResponse(res);
}

export async function apiPatch(path: string, body: any, timeout?: number) {
  const res = await fetchWithTimeout(`${API_BASE}${path}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }, timeout);
  return handleResponse(res);
}

export async function apiPut(path: string, body: any, timeout?: number) {
  const res = await fetchWithTimeout(`${API_BASE}${path}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }, timeout);
  return handleResponse(res);
}

export async function apiDelete(path: string, timeout?: number) {
  const res = await fetchWithTimeout(`${API_BASE}${path}`, {
    method: "DELETE",
  }, timeout);
  return handleResponse(res);
}

export type { ApiError };
