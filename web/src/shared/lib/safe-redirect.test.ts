import { describe, expect, it } from 'vitest';
import { safeRedirect } from './safe-redirect';

describe('safeRedirect', () => {
  it('keeps paths inside the app', () => {
    expect(safeRedirect('/orders/o-1?tab=x')).toBe('/orders/o-1?tab=x');
  });

  it.each([undefined, '', 'https://evil.example', '//evil.example', '/\\evil.example', 'orders'])('sends %j home', (target) => {
    expect(safeRedirect(target)).toBe('/');
  });
});
