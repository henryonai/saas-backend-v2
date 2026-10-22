import { createFileRoute } from '@tanstack/react-router';
import { AppShell } from '@/features/shell/app-shell';
import { requireWorkspace } from '@/shared/auth/guards';

export const Route = createFileRoute('/_app')({
  beforeLoad: ({ location }) => requireWorkspace(location),
  component: AppShell,
});
