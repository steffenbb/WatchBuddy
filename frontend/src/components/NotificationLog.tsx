import React, { useState, useEffect } from 'react';
import { useToast } from './ToastProvider';
import { formatLocalDate } from '../utils/date';

interface Notification {
  id: string;
  message: string;
  created_at: string;
  read: boolean;
  type: string;
  link?: string;
}

interface NotificationLogProps {
  isOpen: boolean;
  onClose: () => void;
  userId: string;
}

export const NotificationLog: React.FC<NotificationLogProps> = ({ isOpen, onClose, userId }) => {
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [loading, setLoading] = useState(false);
  const [filter, setFilter] = useState<'all' | 'unread'>('all');
  const { addToast } = useToast();
  
  const fetchNotifications = async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({
        user_id: userId.toString(),
        ...(filter === 'unread' && { unread_only: 'true' })
      });
      
      const response = await fetch(`/api/notifications/?${params}`);
      if (response.ok) {
        const data = await response.json();
        setNotifications(data);
      } else {
        addToast({
          message: 'Failed to load notifications',
          type: 'error'
        });
      }
    } catch (error) {
      addToast({
        message: 'Error loading notifications',
        type: 'error'
      });
    } finally {
      setLoading(false);
    }
  };

  // Fetch notifications when component opens or filter changes
  useEffect(() => {
    if (isOpen) {
      fetchNotifications();
    }
  }, [isOpen, filter, userId]);

  const markAsRead = async (notificationId: string) => {
    try {
      const response = await fetch(`/api/notifications/${notificationId}/read`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: userId })
      });
      
      if (response.ok) {
        setNotifications(prev => 
          prev.map(n => 
            n.id === notificationId ? { ...n, read: true } : n
          )
        );
      }
    } catch (error) {
      addToast({
        message: 'Failed to mark notification as read',
        type: 'error'
      });
    }
  };

  const markAllAsRead = async () => {
    try {
      const response = await fetch('/api/notifications/mark-all-read', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: userId })
      });
      
      if (response.ok) {
        setNotifications(prev => prev.map(n => ({ ...n, read: true })));
        addToast({
          message: 'All notifications marked as read',
          type: 'success'
        });
      }
    } catch (error) {
      addToast({
        message: 'Failed to mark all as read',
        type: 'error'
      });
    }
  };

  const clearNotifications = async () => {
    try {
      const response = await fetch('/api/notifications/clear', {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: userId })
      });
      
      if (response.ok) {
        setNotifications([]);
        addToast({
          message: 'All notifications cleared',
          type: 'success'
        });
      }
    } catch (error) {
      addToast({
        message: 'Failed to clear notifications',
        type: 'error'
      });
    }
  };

  const getNotificationIcon = (type: string) => {
    switch (type) {
      case 'success': return '✓';
      case 'error': return '✕';
      case 'warning': return '⚠';
      default: return 'ℹ';
    }
  };

  const getNotificationColor = (type: string) => {
    switch (type) {
      case 'success': return 'text-green-300';
      case 'error': return 'text-red-300';
      case 'warning': return 'text-yellow-300';
      default: return 'text-blue-300';
    }
  };

  // Use formatLocalDate for all notification timestamps

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 bg-black/30 backdrop-blur-sm flex items-center justify-center z-50 p-4">
      <div className="relative z-10 bg-white/10 backdrop-blur-lg rounded-2xl shadow-lg border border-white/20 w-full max-w-2xl max-h-[90vh] md:max-h-[80vh] flex flex-col transition-all duration-300">
        {/* Header */}
        <div className="p-4 md:p-6 border-b border-white/10">
          <div className="flex items-center justify-between">
            <h2 className="text-lg md:text-xl font-semibold text-white">
              Notifications
            </h2>
            <button
              onClick={onClose}
              className="text-white/60 hover:text-white p-2 -m-2 touch-manipulation transition-colors"
              aria-label="Close"
            >
              ✕
            </button>
          </div>
          
          {/* Filters and Actions */}
          <div className="mt-4 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
            <div className="flex space-x-2">
              <button
                onClick={() => setFilter('all')}
                className={`px-3 py-2 rounded-lg text-sm font-medium transition-colors touch-manipulation ${
                  filter === 'all'
                    ? 'bg-white/20 text-white border border-white/30'
                    : 'text-white/70 hover:bg-white/10 hover:text-white border border-white/10'
                }`}
              >
                All
              </button>
              <button
                onClick={() => setFilter('unread')}
                className={`px-3 py-2 rounded-lg text-sm font-medium transition-colors touch-manipulation ${
                  filter === 'unread'
                    ? 'bg-white/20 text-white border border-white/30'
                    : 'text-white/70 hover:bg-white/10 hover:text-white border border-white/10'
                }`}
              >
                Unread
              </button>
            </div>
            
            <div className="flex flex-col sm:flex-row gap-2">
              <button
                onClick={markAllAsRead}
                className="px-3 py-2 bg-green-500/20 text-green-200 border border-green-400/30 rounded-lg text-sm font-medium hover:bg-green-500/30 transition-colors touch-manipulation"
              >
                Mark All Read
              </button>
              <button
                onClick={clearNotifications}
                className="px-3 py-2 bg-red-500/20 text-red-200 border border-red-400/30 rounded-lg text-sm font-medium hover:bg-red-500/30 transition-colors touch-manipulation"
              >
                Clear All
              </button>
            </div>
          </div>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-4 md:p-6">
          {loading ? (
            <div className="flex items-center justify-center py-8">
              <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-white"></div>
            </div>
          ) : notifications.length === 0 ? (
            <div className="text-center py-8">
              <p className="text-white/60">
                {filter === 'unread' ? 'No unread notifications' : 'No notifications'}
              </p>
            </div>
          ) : (
            <div className="space-y-3">
              {notifications.map((notification) => (
                <div
                  key={notification.id}
                  className={`p-3 md:p-4 rounded-lg border transition-all ${
                    notification.read
                      ? 'border-white/10 bg-white/5'
                      : 'border-white/30 bg-white/15'
                  }`}
                >
                  <div className="flex items-start">
                    <div className={`flex-shrink-0 mr-3 text-lg ${getNotificationColor(notification.type)}`}>
                      {getNotificationIcon(notification.type)}
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="text-sm text-white break-words">
                        {notification.message}
                      </p>
                      <div className="mt-2 flex flex-col sm:flex-row sm:items-center gap-2 sm:gap-3">
                        <span className="text-xs text-white/60">
                          {formatLocalDate(notification.created_at, { dateStyle: 'medium', timeStyle: 'short' })}
                        </span>
                        <div className="flex gap-3">
                          {notification.link && (
                            <button
                              onClick={() => window.location.href = notification.link!}
                              className="text-xs text-blue-300 hover:text-blue-200 hover:underline touch-manipulation"
                            >
                              View Details
                            </button>
                          )}
                          {!notification.read && (
                            <button
                              onClick={() => markAsRead(notification.id)}
                              className="text-xs text-white/60 hover:text-white touch-manipulation"
                            >
                              Mark as Read
                            </button>
                          )}
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};