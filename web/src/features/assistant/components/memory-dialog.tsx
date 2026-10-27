import { BrainIcon, PencilIcon, Trash2Icon } from 'lucide-react';
import { type FormEvent, useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { AssistantMemory } from '@/shared/api/types';
import { ConfirmDialog } from '@/shared/components/confirm-dialog';
import { ErrorState } from '@/shared/components/query-state';
import { Button } from '@/shared/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from '@/shared/components/ui/dialog';
import { Input } from '@/shared/components/ui/input';
import { Skeleton } from '@/shared/components/ui/skeleton';
import { Spinner } from '@/shared/components/ui/spinner';
import { Switch } from '@/shared/components/ui/switch';
import { toastError } from '@/shared/lib/errors';
import { formatNumber } from '@/shared/lib/format';
import { useMemories, useMemoryChanges } from '../hooks';

/** The server's limit on a memory (MemoryIn in modules/assistant/src/assistant/schemas.py). */
const MEMORY_CHARS = 500;

/**
 * What the assistant remembers about the user across conversations, as in
 * ChatGPT's settings: memory on or off, each memory to edit or delete, one
 * to add, and Forget everything. Read again each time it opens.
 */
export function MemoryButton() {
  const { t } = useTranslation();
  return (
    <Dialog>
      <DialogTrigger asChild>
        <Button variant="outline">
          <BrainIcon />
          {t('assistant.memory.title')}
        </Button>
      </DialogTrigger>
      <DialogContent className="max-h-[85svh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{t('assistant.memory.title')}</DialogTitle>
          <DialogDescription>{t('assistant.memory.description')}</DialogDescription>
        </DialogHeader>
        <Memories />
      </DialogContent>
    </Dialog>
  );
}

function Memories() {
  const { t } = useTranslation();
  const memories = useMemories();
  const changes = useMemoryChanges();
  const switchId = useId();

  if (memories.isPending) {
    return (
      <div className="space-y-2" aria-busy="true">
        <Skeleton className="h-6 w-2/3" />
        <Skeleton className="h-9 w-full" />
        <Skeleton className="h-9 w-full" />
      </div>
    );
  }
  if (memories.isError) return <ErrorState error={memories.error} onRetry={() => void memories.refetch()} />;
  const { enabled, items, limit } = memories.data;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-4 rounded-lg border p-3">
        <label htmlFor={switchId} className="space-y-0.5 text-sm">
          <span className="block font-medium">{t('assistant.memory.enabled')}</span>
          <span className="block text-muted-foreground">{enabled ? t('assistant.memory.on') : t('assistant.memory.off')}</span>
        </label>
        <Switch
          id={switchId}
          checked={enabled}
          disabled={changes.setEnabled.isPending}
          onCheckedChange={(checked) => changes.setEnabled.mutate(checked, { onError: (error) => toastError(error, t) })}
        />
      </div>
      <AddMemory full={items.length >= limit} />
      {items.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t('assistant.memory.empty')}</p>
      ) : (
        <ul className="divide-y rounded-lg border" aria-label={t('assistant.memory.listLabel')}>
          {items.map((memory) => (
            <MemoryItem key={memory.memory_id} memory={memory} />
          ))}
        </ul>
      )}
      <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
        <span>{t('assistant.memory.count', { count: items.length, limit: formatNumber(limit) })}</span>
        {items.length > 0 && (
          <ConfirmDialog
            trigger={
              <Button variant="ghost" size="sm" className="text-destructive">
                {t('assistant.memory.forgetAll')}
              </Button>
            }
            title={t('assistant.memory.forgetAllTitle')}
            description={t('assistant.memory.forgetAllDescription')}
            confirmLabel={t('assistant.memory.forgetAll')}
            destructive
            onConfirm={() =>
              changes.removeAll.mutateAsync().catch((error: unknown) => {
                toastError(error, t);
                throw error;
              })
            }
          />
        )}
      </div>
    </div>
  );
}

function AddMemory({ full }: { full: boolean }) {
  const { t } = useTranslation();
  const { add } = useMemoryChanges();
  const [text, setText] = useState('');

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!text.trim()) return;
    add.mutate(text, { onSuccess: () => setText(''), onError: (error) => toastError(error, t) });
  }

  return (
    <form onSubmit={submit} className="flex gap-2">
      <Input
        value={text}
        onChange={(event) => setText(event.target.value)}
        maxLength={MEMORY_CHARS}
        disabled={full}
        placeholder={full ? t('assistant.memory.full') : t('assistant.memory.addPlaceholder')}
        aria-label={t('assistant.memory.addLabel')}
      />
      <Button type="submit" variant="outline" disabled={full || !text.trim() || add.isPending}>
        {add.isPending && <Spinner />}
        {t('assistant.memory.add')}
      </Button>
    </form>
  );
}

function MemoryItem({ memory }: { memory: AssistantMemory }) {
  const { t } = useTranslation();
  const { edit, remove } = useMemoryChanges();
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(memory.text);

  function save(event: FormEvent) {
    event.preventDefault();
    if (!text.trim()) return;
    edit.mutate({ memoryId: memory.memory_id, text }, { onSuccess: () => setEditing(false), onError: (error) => toastError(error, t) });
  }

  if (editing) {
    return (
      <li className="p-2">
        <form onSubmit={save} className="flex gap-2">
          <Input value={text} onChange={(event) => setText(event.target.value)} maxLength={MEMORY_CHARS} autoFocus aria-label={t('assistant.memory.editLabel')} />
          <Button type="button" variant="ghost" onClick={() => setEditing(false)} disabled={edit.isPending}>
            {t('common.cancel')}
          </Button>
          <Button type="submit" disabled={!text.trim() || edit.isPending}>
            {edit.isPending && <Spinner />}
            {t('common.save')}
          </Button>
        </form>
      </li>
    );
  }
  return (
    <li className="flex items-start gap-2 py-2 pr-1 pl-3 text-sm">
      <span className="min-w-0 flex-1 pt-1 break-words">{memory.text}</span>
      <Button
        variant="ghost"
        size="icon-sm"
        onClick={() => {
          setText(memory.text);
          setEditing(true);
        }}
        aria-label={t('assistant.memory.edit', { text: memory.text })}
        title={t('assistant.memory.editLabel')}
      >
        <PencilIcon />
      </Button>
      <Button
        variant="ghost"
        size="icon-sm"
        disabled={remove.isPending}
        onClick={() => remove.mutate(memory.memory_id, { onError: (error) => toastError(error, t) })}
        aria-label={t('assistant.memory.delete', { text: memory.text })}
        title={t('assistant.memory.deleteLabel')}
      >
        <Trash2Icon />
      </Button>
    </li>
  );
}
