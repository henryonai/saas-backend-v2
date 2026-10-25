import { useQueryClient } from '@tanstack/react-query';
import { Link, useNavigate } from '@tanstack/react-router';
import { BotIcon, MessagesSquareIcon, SquarePenIcon } from 'lucide-react';
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ApiError } from '@/shared/api/client';
import { PageHeader } from '@/shared/components/page-header';
import { ErrorState } from '@/shared/components/query-state';
import { Button } from '@/shared/components/ui/button';
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from '@/shared/components/ui/empty';
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetTrigger } from '@/shared/components/ui/sheet';
import { Skeleton } from '@/shared/components/ui/skeleton';
import { config } from '@/shared/config';
import { Composer } from './components/composer';
import { ConversationList } from './components/conversation-list';
import { RunErrorNotice } from './components/run-error';
import { AssistantTurn, UserTurn } from './components/turn';
import {
  conversationStore,
  editMessage,
  newConversation,
  openConversation,
  rateAnswer,
  regenerate,
  retry,
  sendMessage,
  stopAnswer,
  useConversation,
} from './conversation';
import { refreshConversations, showNewConversation, useConversationHistory } from './hooks';
import { toTurns } from './transcript';

const SUGGESTIONS = ['assistant.suggestions.orders', 'assistant.suggestions.usage', 'assistant.suggestions.analyze'] as const;

/**
 * /assistant starts a new conversation; /assistant/<threadId> shows one,
 * reopened from its history unless it is the one streaming now. The list of
 * conversations sits beside it (in a side panel on a phone).
 */
export function AssistantPage({ threadId }: { threadId?: string }) {
  const { t } = useTranslation();
  if (!config.assistantUrl) {
    return (
      <>
        <PageHeader title={t('assistant.title')} description={t('assistant.description')} />
        <NotSetUp />
      </>
    );
  }
  return (
    <div className="grid gap-6 md:grid-cols-[15rem_minmax(0,1fr)]">
      <aside className="hidden md:block">
        <div className="sticky top-20 max-h-[calc(100svh-7rem)] overflow-y-auto">
          <ConversationList activeId={threadId} />
        </div>
      </aside>
      {/* At least the window's height (less the header and the page's padding), so the composer starts at the bottom. */}
      <div className="mx-auto flex min-h-[calc(100svh-5.5rem)] w-full max-w-3xl min-w-0 flex-col md:min-h-[calc(100svh-7.5rem)]">
        <PageHeader
          title={t('assistant.title')}
          description={t('assistant.description')}
          actions={
            <>
              <ConversationsSheet activeId={threadId} />
              {threadId && <NewConversationButton />}
            </>
          }
        />
        {threadId ? <OpenConversation key={threadId} threadId={threadId} /> : <StartConversation />}
      </div>
    </div>
  );
}

function NewConversationButton() {
  const { t } = useTranslation();
  return (
    <Button variant="outline" asChild>
      <Link to="/assistant">
        <SquarePenIcon />
        {t('assistant.newConversation')}
      </Link>
    </Button>
  );
}

function ConversationsSheet({ activeId }: { activeId?: string }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger asChild>
        <Button variant="outline" className="md:hidden">
          <MessagesSquareIcon />
          {t('assistant.conversations.title')}
        </Button>
      </SheetTrigger>
      <SheetContent side="left" className="w-72 overflow-y-auto p-4">
        <SheetHeader className="sr-only">
          <SheetTitle>{t('assistant.conversations.title')}</SheetTitle>
        </SheetHeader>
        <ConversationList activeId={activeId} onOpen={() => setOpen(false)} />
      </SheetContent>
    </Sheet>
  );
}

/** A new conversation. Its first message gives it a URL and a place in the list. */
function StartConversation() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  // Coming back here from a conversation (New conversation, the sidebar) starts
  // afresh, before the old one is painted.
  useLayoutEffect(() => {
    if (conversationStore.getState().messages.length > 0) newConversation();
  }, []);

  function onFirstMessage(threadId: string, text: string) {
    showNewConversation(queryClient, threadId, text);
    void navigate({ to: '/assistant/$threadId', params: { threadId }, replace: true });
  }

  return <Conversation onFirstMessage={onFirstMessage} />;
}

/**
 * A conversation by its URL: the one on screen already, or loaded from its
 * history, following its answer if one is still coming.
 */
function OpenConversation({ threadId }: { threadId: string }) {
  const { t } = useTranslation();
  const live = useConversation().threadId === threadId;
  const history = useConversationHistory(threadId, !live);

  useEffect(() => {
    if (history.data && conversationStore.getState().threadId !== threadId) openConversation(threadId, history.data);
  }, [history.data, threadId]);

  if (live) return <Conversation />;
  if (history.isError) {
    if (history.error instanceof ApiError && history.error.status === 404) {
      return (
        <Empty className="border border-dashed">
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <MessagesSquareIcon />
            </EmptyMedia>
            <EmptyTitle>{t('assistant.conversations.goneTitle')}</EmptyTitle>
            <EmptyDescription>{t('assistant.conversations.gone')}</EmptyDescription>
          </EmptyHeader>
          <EmptyContent>
            <NewConversationButton />
          </EmptyContent>
        </Empty>
      );
    }
    return <ErrorState error={history.error} onRetry={() => void history.refetch()} />;
  }
  return (
    <div className="space-y-6" aria-busy="true">
      <Skeleton className="ml-auto h-9 w-1/2 rounded-2xl" />
      <Skeleton className="h-24 w-full" />
      <Skeleton className="ml-auto h-9 w-1/3 rounded-2xl" />
    </div>
  );
}

function Conversation({ onFirstMessage }: { onFirstMessage?: (threadId: string, text: string) => void }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { messages, running, error, tokens, ratings } = useConversation();
  const turns = useMemo(() => toTurns(messages), [messages]);
  const last = turns.at(-1);
  const lastPart = last?.role === 'assistant' ? last.parts.at(-1) : undefined;
  // Nothing on screen moves: the model hasn't written yet, or a tool just finished.
  const thinking = running && (!lastPart || (lastPart.type === 'tool' && lastPart.call.result !== null));
  const follow = useFollowBottom(messages, error, running);

  // After each answer: the list's order changed, and a new conversation has its model-written title.
  const wasRunning = useRef(running);
  useEffect(() => {
    if (wasRunning.current && !running) void refreshConversations(queryClient);
    wasRunning.current = running;
  }, [running, queryClient]);

  function send(text: string) {
    const { threadId, messages: before } = conversationStore.getState();
    if (!text.trim()) return;
    follow();
    void sendMessage(text);
    if (before.length === 0) onFirstMessage?.(threadId, text.trim());
  }

  function edit(messageId: string, text: string) {
    follow();
    void editMessage(messageId, text);
  }

  function answerAgain() {
    follow();
    void regenerate();
  }

  return (
    <>
      <div className="flex-1 space-y-6 pb-6">
        {turns.length === 0 && !running ? (
          <Empty className="border border-dashed">
            <EmptyHeader>
              <EmptyMedia variant="icon">
                <BotIcon />
              </EmptyMedia>
              <EmptyTitle>{t('assistant.emptyTitle')}</EmptyTitle>
              <EmptyDescription>{t('assistant.empty')}</EmptyDescription>
            </EmptyHeader>
            <EmptyContent className="flex flex-col gap-2">
              {SUGGESTIONS.map((key) => (
                <Button key={key} variant="outline" className="h-auto w-full justify-start py-2 text-left whitespace-normal" onClick={() => send(t(key))}>
                  {t(key)}
                </Button>
              ))}
            </EmptyContent>
          </Empty>
        ) : (
          turns.map((turn, index) =>
            turn.role === 'user' ? (
              <UserTurn key={turn.id} text={turn.text} onEdit={running ? undefined : (text) => edit(turn.id, text)} />
            ) : (
              <AssistantTurn
                key={turn.id}
                turn={turn}
                active={running && index === turns.length - 1}
                thinking={thinking && index === turns.length - 1}
                rating={ratings[turn.answerId]}
                onRate={(rating) => void rateAnswer(turn.answerId, rating)}
                // The last answer; after a failed run, the error's Try again does the same.
                onRegenerate={!running && !error && index === turns.length - 1 ? answerAgain : undefined}
              />
            ),
          )
        )}
        {thinking && last?.role !== 'assistant' && <AssistantTurn active thinking />}
        {error && <RunErrorNotice error={error} onRetry={() => void retry()} />}
      </div>
      {/* Stays at the bottom of the window as the conversation grows; covers the page's bottom padding. */}
      <div className="sticky bottom-0 -mb-4 bg-background pt-2 pb-4 md:-mb-8 md:pb-8">
        <Composer running={running} tokens={tokens} onSend={send} onStop={stopAnswer} />
      </div>
    </>
  );
}

/**
 * Keeps the newest words in view while they stream, unless the user scrolled
 * up to read. Returns a function that starts following again (on send).
 */
function useFollowBottom(...changes: unknown[]): () => void {
  const following = useRef(true);
  useEffect(() => {
    const onScroll = () => {
      following.current = window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 80;
    };
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => window.removeEventListener('scroll', onScroll);
  }, []);
  useEffect(() => {
    if (following.current) window.scrollTo({ top: document.documentElement.scrollHeight });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- the caller lists what moves the bottom
  }, changes);
  return () => {
    following.current = true;
  };
}

function NotSetUp() {
  const { t } = useTranslation();
  return (
    <Empty className="border border-dashed">
      <EmptyHeader>
        <EmptyMedia variant="icon">
          <BotIcon />
        </EmptyMedia>
        <EmptyTitle>{t('assistant.notSetUp.title')}</EmptyTitle>
        <EmptyDescription>{t('assistant.notSetUp.description')}</EmptyDescription>
      </EmptyHeader>
      <EmptyContent>
        <pre className="rounded-lg bg-muted p-3 font-mono text-xs">cd web && ./scripts/env-from-stack.sh saas-dev</pre>
      </EmptyContent>
    </Empty>
  );
}
