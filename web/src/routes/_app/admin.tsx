import { createFileRoute } from '@tanstack/react-router';
import { AdminPage } from '@/features/admin/admin-page';

export const Route = createFileRoute('/_app/admin')({
  component: AdminPage,
});
