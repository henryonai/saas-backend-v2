import { createFileRoute } from '@tanstack/react-router';
import { DevCheckoutPage } from '@/features/billing/dev-checkout-page';
import { optionalString } from '@/shared/lib/search';

// DevPaymentProvider's checkout URL: {AppUrl}/dev-checkout?tenant_id=...&plan=...
export const Route = createFileRoute('/dev-checkout')({
  validateSearch: (search: Record<string, unknown>): { tenant_id?: string; plan?: string } => ({
    tenant_id: optionalString(search, 'tenant_id'),
    plan: optionalString(search, 'plan'),
  }),
  component: DevCheckoutRoute,
});

function DevCheckoutRoute() {
  const { tenant_id, plan } = Route.useSearch();
  return <DevCheckoutPage tenantId={tenant_id} plan={plan} />;
}
