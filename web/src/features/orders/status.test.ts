import { describe, expect, it } from 'vitest';
import { nextStatuses } from './status';

describe('nextStatuses', () => {
  it('follows the backend: pending -> paid -> shipped, cancel before shipping', () => {
    expect(nextStatuses('pending')).toEqual(['paid', 'cancelled']);
    expect(nextStatuses('paid')).toEqual(['shipped', 'cancelled']);
    expect(nextStatuses('shipped')).toEqual([]);
    expect(nextStatuses('cancelled')).toEqual([]);
  });
});
