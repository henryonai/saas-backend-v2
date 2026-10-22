import { createFileRoute } from '@tanstack/react-router';
import { SignUpPage } from '@/features/auth/sign-up-page';
import { optionalString } from '@/shared/lib/search';

export const Route = createFileRoute('/_public/sign-up')({
  validateSearch: (search: Record<string, unknown>): { redirect?: string } => ({
    redirect: optionalString(search, 'redirect'),
  }),
  component: SignUpRoute,
});

function SignUpRoute() {
  const { redirect } = Route.useSearch();
  return <SignUpPage redirect={redirect} />;
}
