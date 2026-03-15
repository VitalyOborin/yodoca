import { getDateTimePreferences, type DateTimePreferences } from './dateTimePreferences';

type DateStyle = 'short' | 'medium' | 'long';
type TimeStyle = 'short' | 'medium';

export interface DateTimePreset {
  dateStyle?: DateStyle;
  timeStyle?: TimeStyle;
  withSeconds?: boolean;
  year?: 'numeric' | '2-digit';
  month?: '2-digit' | 'numeric' | 'short' | 'long';
  day?: '2-digit' | 'numeric';
  hour?: '2-digit' | 'numeric';
  minute?: '2-digit' | 'numeric';
  second?: '2-digit' | 'numeric';
}

const dateTimeFormatCache = new Map<string, Intl.DateTimeFormat>();
const relativeTimeFormatCache = new Map<string, Intl.RelativeTimeFormat>();

function toDate(value: Date | string | number): Date | null {
  const date = value instanceof Date ? value : new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

function hasTimeFields(options: Intl.DateTimeFormatOptions): boolean {
  return Boolean(
    options.timeStyle
    || options.hour
    || options.minute
    || options.second,
  );
}

function buildIntlOptions(
  preset: DateTimePreset,
  preferences: DateTimePreferences,
): Intl.DateTimeFormatOptions {
  const options: Intl.DateTimeFormatOptions = {
    timeZone: preferences.timeZone,
  };

  if (preset.dateStyle) options.dateStyle = preset.dateStyle;
  if (preset.timeStyle) options.timeStyle = preset.timeStyle;
  if (preset.year) options.year = preset.year;
  if (preset.month) options.month = preset.month;
  if (preset.day) options.day = preset.day;
  if (preset.hour) options.hour = preset.hour;
  if (preset.minute) options.minute = preset.minute;
  if (preset.second) options.second = preset.second;

  if (preset.withSeconds && !options.second && hasTimeFields(options)) {
    options.second = '2-digit';
  }

  if (hasTimeFields(options)) {
    options.hour12 = !preferences.use24Hour ? undefined : false;
    options.hourCycle = preferences.use24Hour ? 'h23' : undefined;
  }

  return options;
}

function formatterKey(
  locale: string,
  options: Intl.DateTimeFormatOptions,
): string {
  return JSON.stringify([locale, options]);
}

function getDateTimeFormatter(
  options: Intl.DateTimeFormatOptions,
  preferences: DateTimePreferences,
): Intl.DateTimeFormat {
  const key = formatterKey(preferences.locale, options);
  const cached = dateTimeFormatCache.get(key);
  if (cached) return cached;
  const formatter = new Intl.DateTimeFormat(preferences.locale, options);
  dateTimeFormatCache.set(key, formatter);
  return formatter;
}

function getRelativeTimeFormatter(locale: string): Intl.RelativeTimeFormat {
  const cached = relativeTimeFormatCache.get(locale);
  if (cached) return cached;
  const formatter = new Intl.RelativeTimeFormat(locale, { numeric: 'auto' });
  relativeTimeFormatCache.set(locale, formatter);
  return formatter;
}

export function formatDate(
  value: Date | string | number,
  preset: DateTimePreset = { month: 'short', day: 'numeric' },
): string {
  const date = toDate(value);
  if (!date) return '';
  const preferences = getDateTimePreferences();
  const options = buildIntlOptions(preset, preferences);
  return getDateTimeFormatter(options, preferences).format(date);
}

export function formatTime(
  value: Date | string | number,
  preset: DateTimePreset = { hour: '2-digit', minute: '2-digit' },
): string {
  return formatDate(value, preset);
}

export function formatDateTime(
  value: Date | string | number,
  preset: DateTimePreset = {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  },
): string {
  return formatDate(value, preset);
}

export function formatRelative(
  value: Date | string | number,
  now: Date = new Date(),
): string {
  const target = toDate(value);
  if (!target) return '';

  const diffMs = target.getTime() - now.getTime();
  const absMs = Math.abs(diffMs);
  const preferences = getDateTimePreferences();
  const rtf = getRelativeTimeFormatter(preferences.locale);

  if (absMs < 60_000) return rtf.format(Math.round(diffMs / 1000), 'second');
  if (absMs < 3_600_000) return rtf.format(Math.round(diffMs / 60_000), 'minute');
  if (absMs < 86_400_000) return rtf.format(Math.round(diffMs / 3_600_000), 'hour');
  return rtf.format(Math.round(diffMs / 86_400_000), 'day');
}

/** Format Unix timestamp (seconds) as relative time. */
export function formatRelativeTimeFromEpoch(ts: number): string {
  return formatRelative(new Date(ts * 1000));
}

export function formatRelativeTime(date: Date): string {
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const absMs = Math.abs(diffMs);

  if (absMs < 7 * 86_400_000) {
    return formatRelative(date, now);
  }
  return formatDate(date, { month: 'short', day: 'numeric' });
}

export function formatMessageTime(date: Date): string {
  return formatTime(date, { hour: '2-digit', minute: '2-digit' });
}

export function formatScheduleAbsolute(iso: string): string {
  return formatDateTime(iso, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export function formatScheduleRelative(iso: string): string {
  return formatRelative(iso);
}
