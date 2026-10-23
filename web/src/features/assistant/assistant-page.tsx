import { BotIcon, SquarePenIcon } from 'lucide-react';
import { useEffect, useMemo, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { PageHeader } from '@/shared/components/page-header';
import { Button } from '@/shared/components/ui/button';
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from '@/shared/components/ui/empty';
import { config } from '@/shared/config';
import { Composer } from './components/composer';
import { RunErrorNotice } from './components/run-error';
import { AssistantTurn, UserTurn } from './components/turn';
import { newConversation, retry, sendMessage, stopAnswer, useConversation } from './conversation';
import { toTurns } from './transcript';

const SUGGESTIONS = ['assistant.suggestions.orders', 'assistant.suggestions.usage', 'assistant.suggestions.analyze'] as const;

export function AssistantPage() {
  const { t } = useTranslation();
  const header = <PageHeader title={t('assistant.title')} description={t('assistant.description')} actions={config.assistantUrl && <NewConversation />} />;
  if (!config.assistantUrl) {
    return (
      <>
        {header}
        <NotSetUp />
      </>
    );
  }
  return (
    // At least the window's height (less the header and the page's padding), so the composer starts at the bottom.
    <div className="mx-auto flex min-h-[calc(100svh-5.5rem)] w-full max-w-3xl flex-col md:min-h-[calc(100svh-7.5rem)]">
      {header}
      <Conversation />
    </div>
  );
}

function NewConversation() {
  const { t } = useTranslation();
  const { messages } = useConversation();
  return (
    <Button variant="outline" onClick={newConversation} disabled={messages.length === 0}>
      <SquarePenIcon />
      {t('assistant.newConversation')}
    </Button>
  );
}

function Conversation() {
  const { t } = useTranslation();
  const { messages, running, error, tokens } = useConversation();
  const turns = useMemo(() => toTurns(messages), [messages]);
  const last = turns.at(-1);
  const lastPart = last?.role === 'assistant' ? last.parts.at(-1) : undefined;
  // Nothing on screen moves: the model hasn't written yet, or a tool just finished.
  const thinking = running && (!lastPart || (lastPart.type === 'tool' && lastPart.call.result !== null));
  const follow = useFollowBottom(messages, error, running);

  function send(text: string) {
    follow();
    void sendMessage(text);
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
              <UserTurn key={turn.id} text={turn.text} />
            ) : (
              <AssistantTurn key={turn.id} turn={turn} active={running && index === turns.length - 1} thinking={thinking && index === turns.length - 1} />
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
