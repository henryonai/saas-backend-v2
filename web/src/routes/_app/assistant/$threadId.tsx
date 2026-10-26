import { createFileRoute } from '@tanstack/react-router';
import { AssistantPage } from '@/features/assistant/assistant-page';

export const Route = createFileRoute('/_app/assistant/$threadId')({
  component: ConversationRoute,
});

function ConversationRoute() {
  const { threadId } = Route.useParams();
  return <AssistantPage threadId={threadId} />;
}
