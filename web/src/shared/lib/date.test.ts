import { afterEach, describe, expect, it } from 'vitest';
import {
  formatDateTime,
  formatInterval,
  formatRelative,
  formatTime,
  updateDateTimePreferences,
} from './index';

describe('date formatting', () => {
  const original = { ...updateDateTimePreferences({}) };

  afterEach(() => {
    updateDateTimePreferences(original);
  });

  it('forces 24-hour clock in absolute time formats', () => {
    updateDateTimePreferences({
      locale: 'en-US',
      timeZone: 'UTC',
      use24Hour: true,
    });

    const formatted = formatTime('2026-03-15T13:05:09Z');
    expect(formatted).toContain('13:05');
    expect(formatted.toLowerCase()).not.toContain('am');
    expect(formatted.toLowerCase()).not.toContain('pm');
  });

  it('supports seconds preset in time formatting', () => {
    updateDateTimePreferences({
      locale: 'en-US',
      timeZone: 'UTC',
      use24Hour: true,
    });

    const formatted = formatTime('2026-03-15T13:05:09Z', {
      hour: '2-digit',
      minute: '2-digit',
      withSeconds: true,
    });
    expect(formatted).toContain('13:05:09');
  });

  it('formats date+time by locale while keeping 24-hour clock', () => {
    updateDateTimePreferences({
      locale: 'ru-RU',
      timeZone: 'UTC',
      use24Hour: true,
    });

    const formatted = formatDateTime('2026-03-15T13:05:09Z');
    expect(formatted).toContain('13:05');
    expect(formatted.toLowerCase()).not.toContain('am');
    expect(formatted.toLowerCase()).not.toContain('pm');
  });

  it('uses Intl relative formatting without hardcoded "just now"', () => {
    updateDateTimePreferences({
      locale: 'en-US',
      timeZone: 'UTC',
      use24Hour: true,
    });

    const now = new Date('2026-03-15T13:05:09Z');
    const formatted = formatRelative(new Date('2026-03-15T13:05:09Z'), now);
    expect(formatted).not.toBe('just now');
  });

  it('formats repeating interval into human-readable units', () => {
    expect(formatInterval(60)).toBe('Every 1 minute');
    expect(formatInterval(3_600)).toBe('Every 1 hour');
    expect(formatInterval(7_200)).toBe('Every 2 hours');
    expect(formatInterval(86_400)).toBe('Every 1 day');
  });
});
