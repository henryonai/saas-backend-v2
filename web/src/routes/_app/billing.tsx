import { createFileRoute } from '@tanstack/react-router';
import { BillingPage } from '@/features/billing/billing-page';

export const Route = createFileRoute('/_app/billing')({
  component: BillingPage,
});
