import { createFileRoute } from '@tanstack/react-router';
import { TeamPage } from '@/features/team/team-page';

export const Route = createFileRoute('/_app/team')({
  component: TeamPage,
});
