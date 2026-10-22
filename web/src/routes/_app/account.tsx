import { createFileRoute } from '@tanstack/react-router';
import { AccountPage } from '@/features/account/account-page';

export const Route = createFileRoute('/_app/account')({
  component: AccountPage,
});
