import { createFileRoute } from '@tanstack/react-router';
import { ConfirmPage } from '@/features/auth/confirm-page';
import { optionalString } from '@/shared/lib/search';

export const Route = createFileRoute('/_public/confirm')({
  validateSearch: (search: Record<string, unknown>): { email?: string; redirect?: string } => ({
    email: optionalString(search, 'email'),
    redirect: optionalString(search, 'redirect'),
  }),
  component: ConfirmRoute,
});

function ConfirmRoute() {
  const { email, redirect } = Route.useSearch();
  return <ConfirmPage email={email} redirect={redirect} />;
}
