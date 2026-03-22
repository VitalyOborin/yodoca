import { reactive } from 'vue';

export interface DateTimePreferences {
  locale: string;
  timeZone: string;
  use24Hour: boolean;
}

const STORAGE_KEY = 'yodoca.datetime.preferences';

function detectLocale(): string {
  if (typeof navigator === 'undefined') return 'en-US';
  return navigator.language || 'en-US';
}

function detectTimeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
  } catch {
    return 'UTC';
  }
}

const defaults: DateTimePreferences = {
  locale: detectLocale(),
  timeZone: detectTimeZone(),
  use24Hour: true,
};

function loadSavedPreferences(): Partial<DateTimePreferences> {
  if (typeof localStorage === 'undefined') return {};
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as Partial<DateTimePreferences>;
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch {
    return {};
  }
}

export const dateTimePreferencesState = reactive<DateTimePreferences>({
  ...defaults,
  ...loadSavedPreferences(),
});

export function useDateTimePreferences(): DateTimePreferences {
  return dateTimePreferencesState;
}

export function updateDateTimePreferences(
  patch: Partial<DateTimePreferences>,
): DateTimePreferences {
  if (patch.locale) dateTimePreferencesState.locale = patch.locale;
  if (patch.timeZone) dateTimePreferencesState.timeZone = patch.timeZone;
  if (typeof patch.use24Hour === 'boolean') {
    dateTimePreferencesState.use24Hour = patch.use24Hour;
  }
  persistDateTimePreferences();
  return dateTimePreferencesState;
}

export function persistDateTimePreferences(): void {
  if (typeof localStorage === 'undefined') return;
  localStorage.setItem(STORAGE_KEY, JSON.stringify(dateTimePreferencesState));
}

export function getDateTimePreferences(): DateTimePreferences {
  return dateTimePreferencesState;
}
