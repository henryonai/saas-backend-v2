import { createFileRoute } from '@tanstack/react-router';
import { OnboardingLayout } from '@/features/shell/onboarding-layout';
import { requireNoWorkspace } from '@/shared/auth/guards';

export const Route = createFileRoute('/_onboarding')({
  beforeLoad: ({ location }) => requireNoWorkspace(location),
  component: OnboardingLayout,
});
