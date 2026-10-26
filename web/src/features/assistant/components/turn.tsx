import { BotIcon, ChevronLeftIcon, ChevronRightIcon, PencilIcon, RotateCwIcon, ThumbsDownIcon, ThumbsUpIcon } from 'lucide-react';
import { type FormEvent, type KeyboardEvent, type ReactNode, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { AssistantRating, AssistantVersion } from '@/shared/api/types';
import { Button } from '@/shared/components/ui/button';
import { Spinner } from '@/shared/components/ui/spinner';
import { Textarea } from '@/shared/components/ui/textarea';
import { cn } from '@/shared/lib/utils';
import type { Turn } from '../transcript';
import { MAX_LENGTH } from './composer';
import { Markdown } from './markdown';
import { ToolCall } from './tool-call';

/** Shown on hover, and always where there is no hover (touch screens), or when focused. */
const ON_HOVER = 'opacity-0 group-hover:opacity-100 focus-visible:opacity-100 [@media(hover:none)]:opacity-100';

/**
 * A message of the user's. `onEdit` (none while an answer is coming): new
 * text, a new version of it. `version`: which of its versions it is, and
 * `onVersion` shows another.
 */
export function UserTurn({ text, onEdit, version, onVersion }: {
  text: string;
  onEdit?: (text: string) => void;
  version?: AssistantVersion;
  onVersion?: (version: number) => void;
}) {
  const { t } = useTranslation();
  const [editing, setEditing] = useState(false);
  if (editing && onEdit) {
    return (
      <EditMessage
        text={text}
        onCancel={() => setEditing(false)}
        onSend={(edited) => {
          setEditing(false);
          onEdit(edited);
        }}
      />
    );
  }
  return (
    <div className="group flex flex-col items-end gap-1">
      <div className="max-w-[85%] rounded-2xl bg-primary px-4 py-2 text-sm whitespace-pre-wrap break-words text-primary-foreground">
        {text}
      </div>
      <div className="flex items-center gap-0.5 text-muted-foreground">
        {onEdit && (
          <Button variant="ghost" size="icon-sm" className={ON_HOVER} onClick={() => setEditing(true)} aria-label={t('assistant.edit')} title={t('assistant.edit')}>
            <PencilIcon />
          </Button>
        )}
        {version && <VersionPicker version={version} onShow={onVersion} />}
      </div>
    </div>
  );
}

/** "‹ 2/3 ›": which version of a message this is, of how many; the arrows show the one before or after it. */
function VersionPicker({ version, onShow }: { version: AssistantVersion; onShow?: (version: number) => void }) {
  const { t } = useTranslation();
  return (
    <div className="flex items-center text-xs tabular-nums" role="group" aria-label={t('assistant.version', { index: version.index, count: version.count })}>
      <Button
        variant="ghost"
        size="icon-sm"
        disabled={!onShow || version.index <= 1}
        onClick={() => onShow?.(version.index - 1)}
        aria-label={t('assistant.previousVersion')}
        title={t('assistant.previousVersion')}
      >
        <ChevronLeftIcon />
      </Button>
      <span aria-hidden="true">
        {version.index}/{version.count}
      </span>
      <Button
        variant="ghost"
        size="icon-sm"
        disabled={!onShow || version.index >= version.count}
        onClick={() => onShow?.(version.index + 1)}
        aria-label={t('assistant.nextVersion')}
        title={t('assistant.nextVersion')}
      >
        <ChevronRightIcon />
      </Button>
    </div>
  );
}

/** Editing a message, in place: Enter sends, Shift+Enter starts a new line (as in the composer), Escape cancels. */
function EditMessage({ text, onCancel, onSend }: { text: string; onCancel: () => void; onSend: (text: string) => void }) {
  const { t } = useTranslation();
  const [value, setValue] = useState(text);
  const canSend = value.trim() !== '';

  function submit(event?: FormEvent) {
    event?.preventDefault();
    if (canSend) onSend(value);
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Escape') onCancel();
    else if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) submit(event);
  }

  return (
    <form onSubmit={submit} className="ml-auto w-full max-w-[85%] rounded-2xl border bg-background p-2 shadow-sm focus-within:border-ring">
      <Textarea
        autoFocus
        value={value}
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={onKeyDown}
        maxLength={MAX_LENGTH}
        rows={2}
        aria-label={t('assistant.editLabel')}
        className="max-h-48 resize-none border-0 bg-transparent shadow-none focus-visible:ring-0 dark:bg-transparent"
      />
      <div className="flex justify-end gap-2 pt-1">
        <Button type="button" variant="ghost" size="sm" onClick={onCancel}>
          {t('assistant.editCancel')}
        </Button>
        <Button type="submit" size="sm" disabled={!canSend}>
          {t('assistant.editSend')}
        </Button>
      </div>
    </form>
  );
}

/**
 * One answer, as it streams: text and tool calls in order. `active`: its run
 * is still going; `thinking`: nothing is streaming right now (the model hasn't
 * started, or a tool just finished). Once it's done: thumbs up or down
 * (`rating`, `onRate`), `onRegenerate`, and which of its versions it is
 * (`version`, `onVersion`). Its actions show on hover, except on the
 * `latest` answer, or one rated or with versions.
 */
export function AssistantTurn({ turn, active, thinking, latest = false, rating, onRate, onRegenerate, version, onVersion }: {
  turn?: Extract<Turn, { role: 'assistant' }>;
  active: boolean;
  thinking: boolean;
  latest?: boolean;
  rating?: AssistantRating;
  onRate?: (rating: AssistantRating) => void;
  onRegenerate?: () => void;
  version?: AssistantVersion;
  onVersion?: (version: number) => void;
}) {
  const { t } = useTranslation();
  const done = turn && turn.parts.length > 0 && !active;
  return (
    <div className="group flex gap-3">
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
        {done && onRate && (
          <div className={cn('-ml-2 flex items-center gap-0.5 text-muted-foreground', !rating && !latest && !version && ON_HOVER)}>
            {version && <VersionPicker version={version} onShow={onVersion} />}
            <RateButton label={t('assistant.goodAnswer')} pressed={rating === 'up'} onClick={() => onRate('up')}>
              <ThumbsUpIcon />
            </RateButton>
            <RateButton label={t('assistant.badAnswer')} pressed={rating === 'down'} onClick={() => onRate('down')}>
              <ThumbsDownIcon />
            </RateButton>
            {onRegenerate && (
              <Button variant="ghost" size="icon-sm" onClick={onRegenerate} aria-label={t('assistant.regenerate')} title={t('assistant.regenerate')}>
                <RotateCwIcon />
              </Button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function RateButton({ label, pressed, onClick, children }: { label: string; pressed: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <Button
      variant="ghost"
      size="icon-sm"
      onClick={onClick}
      aria-label={label}
      aria-pressed={pressed}
      title={label}
      className={cn(pressed && 'text-foreground [&_svg]:fill-current')}
    >
      {children}
    </Button>
  );
}
