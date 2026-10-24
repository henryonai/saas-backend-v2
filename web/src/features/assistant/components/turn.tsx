import { BotIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Spinner } from '@/shared/components/ui/spinner';
import type { Turn } from '../transcript';
import { Markdown } from './markdown';
import { ToolCall } from './tool-call';

export function UserTurn({ text }: { text: string }) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[85%] rounded-2xl bg-primary px-4 py-2 text-sm whitespace-pre-wrap break-words text-primary-foreground">
        {text}
      </div>
    </div>
  );
}

/**
 * One answer, as it streams: text and tool calls in order. `active`: its run
 * is still going; `thinking`: nothing is streaming right now (the model hasn't
 * started, or a tool just finished).
 */
export function AssistantTurn({ turn, active, thinking }: { turn?: Extract<Turn, { role: 'assistant' }>; active: boolean; thinking: boolean }) {
  const { t } = useTranslation();
  return (
    <div className="flex gap-3">
      <div className="flex size-7 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary" aria-label={t('assistant.title')}>
        <BotIcon className="size-4" />
      </div>
      <div className="min-w-0 flex-1 space-y-3 pt-0.5">
        {turn?.parts.map((part) =>
          part.type === 'text' ? <Markdown key={part.id} text={part.text} /> : <ToolCall key={part.call.id} call={part.call} active={active} />,
        )}
        {thinking && (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Spinner className="size-3.5" />
            {t('assistant.thinking')}
          </div>
        )}
      </div>
    </div>
  );
}
