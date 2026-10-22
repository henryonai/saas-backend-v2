import catalog from '../api/generated/plans.json';

/** The plan catalog, generated from layers/shared/layer/shared/plans.py by `pnpm gen:api`. */
export interface Plan {
  id: string;
  name: string;
  price_usd_month: number;
  /** null: unlimited */
  max_users: number | null;
  max_orders: number | null;
}

export const PLANS: Plan[] = Object.entries(catalog.plans as Record<string, Omit<Plan, 'id'>>)
  .map(([id, plan]) => ({ id, ...plan }))
  .sort((a, b) => a.price_usd_month - b.price_usd_month);

/** Like the backend's get_plan(): unknown names fall back to the default plan. */
export function getPlan(id: string | null | undefined): Plan {
  return PLANS.find((plan) => plan.id === id) ?? PLANS.find((plan) => plan.id === catalog.default)!;
}
