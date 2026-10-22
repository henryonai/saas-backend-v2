import { createFileRoute } from '@tanstack/react-router';
import { WelcomePage } from '@/features/workspace/welcome-page';

export const Route = createFileRoute('/_onboarding/welcome')({
  component: WelcomePage,
});
