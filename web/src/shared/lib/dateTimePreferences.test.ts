import { describe, expect, it } from 'vitest';
import {
  getDateTimePreferences,
  updateDateTimePreferences,
  useDateTimePreferences,
} from './index';

describe('dateTimePreferences', () => {
  it('uses 24-hour format by default', () => {
    const prefs = useDateTimePreferences();
    expect(prefs.use24Hour).toBe(true);
  });

  it('updates global preferences', () => {
    updateDateTimePreferences({
      locale: 'de-DE',
      timeZone: 'Europe/Berlin',
      use24Hour: true,
    });

    const prefs = getDateTimePreferences();
    expect(prefs.locale).toBe('de-DE');
    expect(prefs.timeZone).toBe('Europe/Berlin');
    expect(prefs.use24Hour).toBe(true);
  });
});
