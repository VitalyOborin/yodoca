/** Format Unix timestamp (seconds) as relative time. */
export function formatRelativeTimeFromEpoch(ts: number): string {
  return formatRelativeTime(new Date(ts * 1000));
}

export function formatRelativeTime(date: Date): string {
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMinutes = Math.floor(diffMs / 60_000);
  const diffHours = Math.floor(diffMs / 3_600_000);
  const diffDays = Math.floor(diffMs / 86_400_000);

  if (diffMinutes < 1) return 'just now';
  if (diffMinutes < 60) return `${diffMinutes}m ago`;
  if (diffHours < 24) return `${diffHours}h ago`;
  if (diffDays < 7) return `${diffDays}d ago`;

  return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

export function formatMessageTime(date: Date): string {
  return date.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });
}

export function formatScheduleAbsolute(iso: string): string {
  const date = new Date(iso);
  return date.toLocaleString('en-US', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export function formatScheduleRelative(iso: string): string {
  const target = new Date(iso).getTime();
  const diffMs = target - Date.now();
  const absMs = Math.abs(diffMs);
  const rtf = new Intl.RelativeTimeFormat('en', { numeric: 'auto' });

  if (absMs < 60_000) return rtf.format(Math.round(diffMs / 1000), 'second');
  if (absMs < 3_600_000) return rtf.format(Math.round(diffMs / 60_000), 'minute');
  if (absMs < 86_400_000) return rtf.format(Math.round(diffMs / 3_600_000), 'hour');
  return rtf.format(Math.round(diffMs / 86_400_000), 'day');
}
