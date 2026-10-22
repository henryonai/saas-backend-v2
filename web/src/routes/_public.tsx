import { createFileRoute } from '@tanstack/react-router';
import { PublicLayout } from '@/features/shell/public-layout';
import { requireSignedOut } from '@/shared/auth/guards';

export const Route = createFileRoute('/_public')({
  beforeLoad: () => requireSignedOut(),
  component: PublicLayout,
});
