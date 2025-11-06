/**
 * Simple toast notification system
 * Shows temporary messages at the bottom of the screen
 */

type ToastType = 'success' | 'error' | 'warning' | 'info';

interface ToastOptions {
  duration?: number;
  type?: ToastType;
  saveToBackend?: boolean; // Whether to persist notification to backend
}

const DEFAULT_DURATION = 4000;

/**
 * Save notification to backend for persistent storage
 */
async function saveNotification(message: string, type: ToastType) {
  try {
    await fetch('/api/notifications/send', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        user_id: 1,
        message,
        type,
        source: 'ui_toast'
      })
    });
  } catch (error) {
    // Silent fail - don't block UI toast from showing
    console.debug('Failed to save notification to backend:', error);
  }
}

export function showToast(message: string, options: ToastOptions = {}) {
  const { duration = DEFAULT_DURATION, type = 'info', saveToBackend = true } = options;

  // Save to backend for persistence (async, non-blocking)
  if (saveToBackend) {
    saveNotification(message, type);
  }

  // Create toast element
  const toast = document.createElement('div');
  toast.className = 'fixed bottom-20 left-1/2 transform -translate-x-1/2 z-50 px-6 py-3 rounded-xl backdrop-blur-lg border shadow-2xl animate-slide-up max-w-md text-center';
  
  // Style based on type
  const styles: Record<ToastType, string> = {
    success: 'bg-emerald-500/90 border-emerald-400/50 text-white',
    error: 'bg-red-500/90 border-red-400/50 text-white',
    warning: 'bg-yellow-500/90 border-yellow-400/50 text-black',
    info: 'bg-purple-500/90 border-purple-400/50 text-white',
  };
  
  toast.className += ' ' + styles[type];
  toast.textContent = message;
  
  // Add animation styles if not already present
  if (!document.getElementById('toast-styles')) {
    const style = document.createElement('style');
    style.id = 'toast-styles';
    style.textContent = `
      @keyframes slide-up {
        from {
          opacity: 0;
          transform: translate(-50%, 20px);
        }
        to {
          opacity: 1;
          transform: translate(-50%, 0);
        }
      }
      .animate-slide-up {
        animation: slide-up 0.3s ease-out;
      }
    `;
    document.head.appendChild(style);
  }
  
  document.body.appendChild(toast);
  
  // Auto-remove after duration
  setTimeout(() => {
    toast.style.transition = 'opacity 0.3s ease-out, transform 0.3s ease-out';
    toast.style.opacity = '0';
    toast.style.transform = 'translate(-50%, 20px)';
    setTimeout(() => toast.remove(), 300);
  }, duration);
}

// Convenience methods
export const toast = {
  success: (message: string, duration?: number) => showToast(message, { type: 'success', duration }),
  error: (message: string, duration?: number) => showToast(message, { type: 'error', duration }),
  warning: (message: string, duration?: number) => showToast(message, { type: 'warning', duration }),
  info: (message: string, duration?: number) => showToast(message, { type: 'info', duration }),
};
