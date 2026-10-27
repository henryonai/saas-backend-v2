import { ArrowUpIcon, PaperclipIcon, SquareIcon } from 'lucide-react';
import { type ClipboardEvent, type DragEvent, type FormEvent, type KeyboardEvent, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/shared/components/ui/button';
import { Textarea } from '@/shared/components/ui/textarea';
import { errorMessage } from '@/shared/lib/errors';
import { formatNumber } from '@/shared/lib/format';
import { toast } from 'sonner';
import { startUpload, uploadFile } from '../api';
import { type AttachmentView, kindOf } from '../transcript';
import { FileChip } from './files';

/** The server's limit on a message (MessageIn in modules/assistant/src/assistant/schemas.py). */
export const MAX_LENGTH = 4000;
/** Files on one message (service.MAX_ATTACHMENTS). */
export const MAX_ATTACHMENTS = 5;

/** A file attached in the composer: uploading to S3 as soon as it is picked, then ready to send. */
interface Picked {
  key: string;
  name: string;
  size: number;
  contentType: string;
  state: 'uploading' | 'ready' | 'failed';
  file?: AttachmentView;
}

/**
 * Where the user writes. Enter sends and Shift+Enter starts a new line, except
 * while an input method is composing (Vietnamese Telex, for one), whose Enter
 * picks a word. They can write while an answer streams; sending waits for it.
 * Files (the paperclip, dropped, or pasted) upload at once and go with the
 * message: up to MAX_ATTACHMENTS.
 */
export function Composer({ running, tokens, onSend, onStop }: {
  running: boolean;
  tokens: number | null;
  onSend: (text: string, attachments: AttachmentView[]) => void;
  onStop: () => void;
}) {
  const { t } = useTranslation();
  const [text, setText] = useState('');
  const [picked, setPicked] = useState<Picked[]>([]);
  const input = useRef<HTMLInputElement>(null);
  const ready = picked.flatMap((item) => (item.state === 'ready' && item.file ? [item.file] : []));
  const uploading = picked.some((item) => item.state === 'uploading');
  const canSend = !running && !uploading && (text.trim() !== '' || ready.length > 0);

  function submit(event?: FormEvent) {
    event?.preventDefault();
    if (!canSend) return;
    onSend(text, ready);
    setText('');
    setPicked([]);
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) submit(event);
  }

  function attach(files: FileList | File[]) {
    const room = MAX_ATTACHMENTS - picked.filter((item) => item.state !== 'failed').length;
    const chosen = [...files].slice(0, Math.max(room, 0));
    if (chosen.length < files.length) toast.error(t('assistant.files.tooMany', { count: MAX_ATTACHMENTS }));
    for (const file of chosen) {
      const key = crypto.randomUUID();
      setPicked((items) => [...items, { key, name: file.name, size: file.size, contentType: file.type, state: 'uploading' }]);
      void upload(key, file);
    }
  }

  async function upload(key: string, file: File) {
    const change = (changes: Partial<Picked>) => setPicked((items) => items.map((item) => (item.key === key ? { ...item, ...changes } : item)));
    try {
      const ticket = await startUpload(file);
      await uploadFile(ticket, file);
      const { file_id, name, size, content_type, kind } = ticket.file;
      change({ state: 'ready', file: { fileId: file_id, name, size, contentType: content_type, kind } });
    } catch (error) {
      change({ state: 'failed' });
      toast.error(t('assistant.files.notAttached', { name: file.name, reason: errorMessage(error, t) }));
    }
  }

  function onDrop(event: DragEvent<HTMLFormElement>) {
    if (event.dataTransfer.files.length === 0) return;
    event.preventDefault();
    attach(event.dataTransfer.files);
  }

  function onPaste(event: ClipboardEvent<HTMLTextAreaElement>) {
    if (event.clipboardData.files.length === 0) return; // text: pasted as usual
    event.preventDefault();
    attach(event.clipboardData.files);
  }

  return (
    <form
      onSubmit={submit}
      onDragOver={(event) => event.preventDefault()}
      onDrop={onDrop}
      className="rounded-2xl border bg-background shadow-sm focus-within:border-ring focus-within:ring-3 focus-within:ring-ring/50"
    >
      {picked.length > 0 && (
        <ul className="flex flex-wrap gap-2 px-3 pt-3" aria-label={t('assistant.files.attached')}>
          {picked.map((item) => (
            <li key={item.key}>
              <FileChip
                file={item.file ?? { name: item.name, size: item.size, kind: kindOf(item.contentType) }}
                state={item.state === 'ready' ? undefined : item.state}
                onRemove={() => setPicked((items) => items.filter((other) => other.key !== item.key))}
              />
            </li>
          ))}
        </ul>
      )}
      <Textarea
        value={text}
        onChange={(event) => setText(event.target.value)}
        onKeyDown={onKeyDown}
        onPaste={onPaste}
        maxLength={MAX_LENGTH}
        rows={1}
        placeholder={t('assistant.placeholder')}
        aria-label={t('assistant.placeholder')}
        className="max-h-48 min-h-12 resize-none border-0 bg-transparent px-4 pt-3 shadow-none focus-visible:ring-0 dark:bg-transparent"
      />
      <div className="flex items-center gap-2 px-3 pb-2">
        <input
          ref={input}
          type="file"
          multiple
          hidden
          aria-label={t('assistant.files.choose')}
          onChange={(event) => {
            if (event.target.files) attach(event.target.files);
            event.target.value = ''; // the same file again is a new pick
          }}
        />
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          className="-ml-1 text-muted-foreground"
          onClick={() => input.current?.click()}
          disabled={picked.length >= MAX_ATTACHMENTS}
          aria-label={t('assistant.files.attach')}
          title={t('assistant.files.attach')}
        >
          <PaperclipIcon />
        </Button>
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
