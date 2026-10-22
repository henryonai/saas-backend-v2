import type { OrderStatus } from '@/shared/api/types';

/** Mirrors ALLOWED_TRANSITIONS in modules/orders/src/orders/service.py (the backend decides). */
const NEXT: Record<OrderStatus, OrderStatus[]> = {
  pending: ['paid', 'cancelled'],
  paid: ['shipped', 'cancelled'],
  shipped: [],
  cancelled: [],
};

export function nextStatuses(status: OrderStatus): OrderStatus[] {
  return NEXT[status];
}

/** The quantity limits of OrderCreate in modules/orders/src/orders/schemas.py. */
export const MAX_QUANTITY = 100;
export const MAX_ITEM_LENGTH = 200;
