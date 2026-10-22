import { createFileRoute } from '@tanstack/react-router';
import { SignInPage } from '@/features/auth/sign-in-page';
import { optionalString } from '@/shared/lib/search';

export const Route = createFileRoute('/_public/sign-in')({
  validateSearch: (search: Record<string, unknown>): { redirect?: string; email?: string } => ({
    redirect: optionalString(search, 'redirect'),
    email: optionalString(search, 'email'),
  }),
  component: SignInRoute,
});

function SignInRoute() {
  const { redirect, email } = Route.useSearch();
  return <SignInPage redirect={redirect} email={email} />;
}
