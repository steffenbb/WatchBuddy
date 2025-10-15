import React, { createContext, useContext, useState, useCallback, useEffect } from 'react';

interface Toast {
  id: string;
  message: string;
  type: 'info' | 'success' | 'warning' | 'error';
  link?: string;
  duration?: number;
}

interface ToastContextType {
  toasts: Toast[];
  addToast: (toast: Omit<Toast, 'id'>) => void;
  removeToast: (id: string) => void;
  clearToasts: () => void;
}

const ToastContext = createContext<ToastContextType | undefined>(undefined);

export const useToast = () => {
  const context = useContext(ToastContext);
  if (!context) {
    throw new Error('useToast must be used within a ToastProvider');
  }
  return context;
};

export const ToastProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const addToast = useCallback((toast: Omit<Toast, 'id'>) => {
    const id = Math.random().toString(36).substr(2, 9);
    const newToast = { ...toast, id };
    
    setToasts(prev => [...prev, newToast]);
    
    // Auto-remove after duration (default 6 seconds)
    const duration = toast.duration || 6000;
    setTimeout(() => {
      removeToast(id);
    }, duration);
  }, []);

  const removeToast = useCallback((id: string) => {
    setToasts(prev => prev.filter(toast => toast.id !== id));
  }, []);

  const clearToasts = useCallback(() => {
    setToasts([]);
  }, []);

  // Connect to SSE for real-time notifications
  useEffect(() => {
    const userId = 1; // TODO: Get from auth context
    const eventSource = new EventSource(`/api/notifications/stream?user_id=${userId}`);
    
    eventSource.onmessage = (event) => {
      try {
        const notification = JSON.parse(event.data);
        
        if (notification.type === 'heartbeat' || notification.type === 'connected') {
          return; // Ignore heartbeat messages
        }
        
        // Add notification as toast
        addToast({
          message: notification.message,
          type: notification.type || 'info',
          link: notification.link,
          duration: 8000 // Longer duration for real-time notifications
        });
        
        // Mark as read if it has an ID
        if (notification.id) {
          fetch(`/api/notifications/${notification.id}/read`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ user_id: userId })
          });
        }
      } catch (error) {
        console.error('Failed to parse notification:', error);
      }
    };
    
    eventSource.onerror = (error) => {
      console.error('SSE connection error:', error);
    };
    

    return () => {
      eventSource.close();
    };
  }, [addToast]);

  return (
    <ToastContext.Provider value={{ toasts, addToast, removeToast, clearToasts }}>
      {children}
      <ToastContainer />
    </ToastContext.Provider>
  );
};

const ToastContainer: React.FC = () => {
  const { toasts, removeToast } = useToast();

  const getToastStyles = (type: Toast['type']) => {
    const baseStyles = "rounded-2xl shadow-2xl p-4 max-w-sm md:max-w-sm w-full pointer-events-auto border border-indigo-100 bg-white/90 backdrop-blur-xl transition-all duration-500";
    let accent = "";
    switch (type) {
      case 'success':
        accent = "border-green-300";
        break;
      case 'error':
        accent = "border-red-300";
        break;
      case 'warning':
        accent = "border-yellow-300";
        break;
      default:
        accent = "border-blue-300";
    }
    return `${baseStyles} ${accent}`;
  };

  const getIcon = (type: Toast['type']) => {
    switch (type) {
      case 'success':
        return '✓';
      case 'error':
        return '✕';
      case 'warning':
        return '⚠';
      default:
        return 'ℹ';
    }
  };

  return (
    <div className="fixed bottom-4 right-4 left-4 md:left-auto md:top-4 md:bottom-auto space-y-2 z-50 pointer-events-none">
      {toasts.map((toast) => (
        <div
          key={toast.id}
          className={`${getToastStyles(toast.type)} transform transition-all duration-300 ease-in-out pointer-events-auto`}
        >
          <div className="flex items-start">
            <div className="flex-shrink-0 mr-3 text-lg">
              {getIcon(toast.type)}
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium break-words">{toast.message}</p>
              {toast.link && (
                <button
                  onClick={() => {
                    window.location.href = toast.link!;
                    removeToast(toast.id);
                  }}
                  className="mt-2 text-xs underline hover:no-underline touch-manipulation"
                >
                  View Details
                </button>
              )}
            </div>
            <button
              onClick={() => removeToast(toast.id)}
              className="flex-shrink-0 ml-4 text-lg hover:opacity-70 p-1 touch-manipulation"
              aria-label="Close notification"
            >
              ×
            </button>
          </div>
        </div>
      ))}
    </div>
  );
};