import { createFileRoute } from '@tanstack/react-router';
import { ForgotPasswordPage } from '@/features/auth/forgot-password-page';
import { optionalString } from '@/shared/lib/search';

export const Route = createFileRoute('/_public/forgot-password')({
  validateSearch: (search: Record<string, unknown>): { email?: string } => ({
    email: optionalString(search, 'email'),
  }),
  component: ForgotPasswordRoute,
});

function ForgotPasswordRoute() {
  const { email } = Route.useSearch();
  return <ForgotPasswordPage email={email} />;
}
