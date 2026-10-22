import { afterEach, describe, expect, it } from 'vitest';
import { setLanguage } from '../i18n';
import { dollarsToCents, formatMoney, formatRelative } from './format';

afterEach(() => setLanguage('en'));

describe('dollarsToCents', () => {
  it.each([
    ['12', 1200],
    ['12.5', 1250],
    ['12.50', 1250],
    ['0.05', 5],
    ['0', 0],
    [' 7,25 ', 725],
  ])('%j -> %i', (text, cents) => {
    expect(dollarsToCents(text)).toBe(cents);
  });

  it.each(['', '-1', '1.234', 'abc', '1e3', '.5'])('refuses %j', (text) => {
    expect(dollarsToCents(text)).toBeNull();
  });
});

describe('formatMoney', () => {
  it('formats cents as US dollars in the current language', () => {
    expect(formatMoney(123456)).toBe('$1,234.56');
    setLanguage('vi');
    expect(formatMoney(123456)).toContain('1.234,56');
  });
});

describe('formatRelative', () => {
  it('says how long ago', () => {
    const now = Date.parse('2026-10-22T12:00:00Z');
    expect(formatRelative('2026-10-22T11:55:00Z', now)).toBe('5 minutes ago');
    expect(formatRelative('2026-10-20T12:00:00Z', now)).toBe('2 days ago');
  });
});
