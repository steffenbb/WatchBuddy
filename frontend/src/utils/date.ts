// utils/date.ts

/**
 * Format a date string or Date object in the user's local time zone.
 * @param date Date string or Date object
 * @param options Intl.DateTimeFormatOptions (optional)
 * @returns string
 */
export function formatLocalDate(date: string | Date, options?: Intl.DateTimeFormatOptions): string {
  const d = typeof date === 'string' ? new Date(date) : date;
  if (isNaN(d.getTime())) {
    console.warn('Invalid date provided to formatLocalDate:', date);
    return '';
  }
  
  return d.toLocaleString(undefined, options);
}

/**
 * Format a date as a relative time (e.g. '2h ago', 'Just now').
 * @param date Date string or Date object
 * @returns string
 */
export function formatRelativeTime(date: string | Date): string {
  const d = typeof date === 'string' ? new Date(date) : date;
  if (isNaN(d.getTime())) return '';
  const now = new Date();
  const diffMs = now.getTime() - d.getTime();
  const mins = Math.floor(diffMs / 60000);
  const hrs = Math.floor(mins / 60);
  if (mins < 1) return 'Just now';
  if (mins < 60) return `${mins}m ago`;
  if (hrs < 24) return `${hrs}h ago`;
  return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
}

/**
 * Format elapsed time since a given start time (e.g. '2m', '1h 30m').
 * @param startTime Date string or Date object of when something started
 * @returns string
 */
export function formatElapsedTime(startTime: string | Date): string {
  const start = typeof startTime === 'string' ? new Date(startTime) : startTime;
  if (isNaN(start.getTime())) return '';
  
  const now = new Date();
  const diffMs = now.getTime() - start.getTime();
  const totalSeconds = Math.floor(diffMs / 1000);
  const totalMinutes = Math.floor(totalSeconds / 60);
  const totalHours = Math.floor(totalMinutes / 60);
  
  if (totalSeconds < 30) return 'just started';
  if (totalMinutes < 1) return '<1m';
  if (totalMinutes < 60) return `${totalMinutes}m`;
  if (totalHours < 24) {
    const remainingMinutes = totalMinutes % 60;
    if (remainingMinutes === 0) return `${totalHours}h`;
    return `${totalHours}h ${remainingMinutes}m`;
  }
  
  const totalDays = Math.floor(totalHours / 24);
  const remainingHours = totalHours % 24;
  if (remainingHours === 0) return `${totalDays}d`;
  return `${totalDays}d ${remainingHours}h`;
}
