import { currentLanguage } from '../i18n';

function locale(): string {
  return currentLanguage() === 'vi' ? 'vi-VN' : 'en-US';
}

/** Money is stored as integer cents (never floats); every price in the app is in US dollars. */
export function formatMoney(cents: number): string {
  return new Intl.NumberFormat(locale(), { style: 'currency', currency: 'USD' }).format(cents / 100);
}

export function formatNumber(value: number): string {
  return new Intl.NumberFormat(locale()).format(value);
}

/** An ISO timestamp from the API, or epoch seconds (invitations' `expires_at`). */
function toDate(value: string | number): Date {
  return typeof value === 'number' ? new Date(value * 1000) : new Date(value);
}

export function formatDate(value: string | number): string {
  return new Intl.DateTimeFormat(locale(), { dateStyle: 'medium' }).format(toDate(value));
}

export function formatDateTime(value: string | number): string {
  return new Intl.DateTimeFormat(locale(), { dateStyle: 'medium', timeStyle: 'short' }).format(toDate(value));
}

const UNITS: [Intl.RelativeTimeFormatUnit, number][] = [
  ['year', 365 * 24 * 3600],
  ['month', 30 * 24 * 3600],
  ['week', 7 * 24 * 3600],
  ['day', 24 * 3600],
  ['hour', 3600],
  ['minute', 60],
];

/** "5 minutes ago", "in 3 days". */
export function formatRelative(value: string | number, now: number = Date.now()): string {
  const seconds = Math.round((toDate(value).getTime() - now) / 1000);
  const format = new Intl.RelativeTimeFormat(locale(), { numeric: 'auto' });
  for (const [unit, size] of UNITS) {
    if (Math.abs(seconds) >= size) return format.format(Math.trunc(seconds / size), unit);
  }
  return format.format(0, 'minute');
}

/** "12.5" → 1250. null for anything that isn't a non-negative amount with at most two decimals. */
export function dollarsToCents(text: string): number | null {
  const match = /^(\d+)(?:[.,](\d{1,2}))?$/.exec(text.trim());
  if (!match) return null;
  const cents = Number(match[1]) * 100 + Number((match[2] ?? '').padEnd(2, '0'));
  return Number.isSafeInteger(cents) ? cents : null;
}
