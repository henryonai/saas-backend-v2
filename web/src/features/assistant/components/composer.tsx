import { ArrowUpIcon, SquareIcon } from 'lucide-react';
import { type FormEvent, type KeyboardEvent, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/shared/components/ui/button';
import { Textarea } from '@/shared/components/ui/textarea';
import { formatNumber } from '@/shared/lib/format';

/** The server's limit on a message (ChatRequest in modules/assistant/src/assistant/schemas.py). */
const MAX_LENGTH = 4000;

/**
 * Where the user writes. Enter sends and Shift+Enter starts a new line, except
 * while an input method is composing (Vietnamese Telex, for one), whose Enter
 * picks a word. They can write while an answer streams; sending waits for it.
 */
export function Composer({ running, tokens, onSend, onStop }: {
  running: boolean;
  tokens: number | null;
  onSend: (text: string) => void;
  onStop: () => void;
}) {
  const { t } = useTranslation();
  const [text, setText] = useState('');
  const canSend = !running && text.trim() !== '';

  function submit(event?: FormEvent) {
    event?.preventDefault();
    if (!canSend) return;
    onSend(text);
    setText('');
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) submit(event);
  }

  return (
    <form onSubmit={submit} className="rounded-2xl border bg-background shadow-sm focus-within:border-ring focus-within:ring-3 focus-within:ring-ring/50">
      <Textarea
        value={text}
        onChange={(event) => setText(event.target.value)}
        onKeyDown={onKeyDown}
        maxLength={MAX_LENGTH}
        rows={1}
        placeholder={t('assistant.placeholder')}
        aria-label={t('assistant.placeholder')}
        className="max-h-48 min-h-12 resize-none border-0 bg-transparent px-4 pt-3 shadow-none focus-visible:ring-0 dark:bg-transparent"
      />
      <div className="flex items-center gap-2 px-3 pb-2">
        <span className="min-w-0 truncate text-xs text-muted-foreground">
          {text.length > MAX_LENGTH - 500
            ? `${formatNumber(text.length)} / ${formatNumber(MAX_LENGTH)}`
            : tokens !== null
              ? t('assistant.tokens', { tokens: formatNumber(tokens) })
              : t('assistant.hint')}
        </span>
        {running ? (
          <Button type="button" size="icon" variant="outline" className="ml-auto rounded-full" onClick={onStop} aria-label={t('assistant.stop')}>
            <SquareIcon className="fill-current" />
          </Button>
        ) : (
          <Button type="submit" size="icon" className="ml-auto rounded-full" disabled={!canSend} aria-label={t('assistant.send')}>
            <ArrowUpIcon />
          </Button>
        )}
      </div>
    </form>
  );
}
