import { describe, expect, it } from 'vitest';
import { firstMessageTitle } from './hooks';

describe('firstMessageTitle', () => {
  it('is the message on one line, cut to 80 characters as the server cuts it', () => {
    expect(firstMessageTitle('  What did\n we   order? ')).toBe('What did we order?');
    const long = firstMessageTitle(`Please compare ${'our orders '.repeat(20)}`);
    expect(long).toHaveLength(80);
    expect(long.endsWith('…')).toBe(true);
  });
});
