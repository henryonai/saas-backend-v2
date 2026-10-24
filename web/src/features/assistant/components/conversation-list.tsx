import { Link, useNavigate } from '@tanstack/react-router';
import { EllipsisIcon, PencilIcon, Trash2Icon } from 'lucide-react';
import { type FormEvent, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { AssistantConversation } from '@/shared/api/types';
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/shared/components/ui/alert-dialog';
import { Button } from '@/shared/components/ui/button';
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/shared/components/ui/dialog';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/shared/components/ui/dropdown-menu';
import { Input } from '@/shared/components/ui/input';
import { Skeleton } from '@/shared/components/ui/skeleton';
import { Spinner } from '@/shared/components/ui/spinner';
import { toastError } from '@/shared/lib/errors';
import { formatRelative } from '@/shared/lib/format';
import { cn } from '@/shared/lib/utils';
import { TITLE_CHARS, useConversations, useDeleteConversation, useRenameConversation } from '../hooks';

/**
 * The user's conversations, most recently used first: open one, rename it,
 * delete it. `activeId`: the one on screen. `onOpen`: called when the user
 * picks one (the phone's side panel closes).
 */
export function ConversationList({ activeId, onOpen }: { activeId?: string; onOpen?: () => void }) {
  const { t } = useTranslation();
  const conversations = useConversations();
  const [renaming, setRenaming] = useState<AssistantConversation | null>(null);
  const [deleting, setDeleting] = useState<AssistantConversation | null>(null);
  const items = conversations.data?.pages.flatMap((page) => page.items) ?? [];

  return (
    <nav aria-label={t('assistant.conversations.title')} className="flex flex-col gap-1">
      <h2 className="px-2 pb-1 text-xs font-medium text-muted-foreground">{t('assistant.conversations.title')}</h2>
      {conversations.isPending ? (
        <div className="space-y-2 px-2" aria-busy="true">
          {Array.from({ length: 4 }, (_, index) => (
            <Skeleton key={index} className="h-9 w-full" />
          ))}
        </div>
      ) : conversations.isError ? (
        <div className="space-y-2 px-2 text-sm text-muted-foreground">
          <p>{t('assistant.conversations.error')}</p>
          <Button size="sm" variant="outline" onClick={() => void conversations.refetch()}>
            {t('common.retry')}
          </Button>
        </div>
      ) : items.length === 0 ? (
        <p className="px-2 text-sm text-muted-foreground">{t('assistant.conversations.empty')}</p>
      ) : (
        <ul className="space-y-0.5">
          {items.map((conversation) => (
            <ConversationItem
              key={conversation.thread_id}
              conversation={conversation}
              active={conversation.thread_id === activeId}
              onOpen={onOpen}
              onRename={() => setRenaming(conversation)}
              onDelete={() => setDeleting(conversation)}
            />
          ))}
        </ul>
      )}
      {conversations.hasNextPage && (
        <Button variant="ghost" size="sm" className="justify-start" disabled={conversations.isFetchingNextPage} onClick={() => void conversations.fetchNextPage()}>
          {conversations.isFetchingNextPage && <Spinner />}
          {t('common.loadMore')}
        </Button>
      )}
      {renaming && <RenameDialog key={renaming.thread_id} conversation={renaming} onClose={() => setRenaming(null)} />}
      {deleting && <DeleteDialog conversation={deleting} active={deleting.thread_id === activeId} onClose={() => setDeleting(null)} />}
    </nav>
  );
}

function ConversationItem({ conversation, active, onOpen, onRename, onDelete }: {
  conversation: AssistantConversation;
  active: boolean;
  onOpen?: () => void;
  onRename: () => void;
  onDelete: () => void;
}) {
  const { t } = useTranslation();
  return (
    <li className={cn('group flex items-center gap-1 rounded-md pr-1 hover:bg-accent', active && 'bg-accent')}>
      <Link
        to="/assistant/$threadId"
        params={{ threadId: conversation.thread_id }}
        onClick={onOpen}
        aria-current={active ? 'page' : undefined}
        className="min-w-0 flex-1 rounded-md px-2 py-1.5 outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <span className="block truncate text-sm">{conversation.title}</span>
        <span className="block text-xs text-muted-foreground">{formatRelative(conversation.updated_at)}</span>
      </Link>
      {/* Not modal: its items open a dialog, and two modal layers fight over focus and pointer events. */}
      <DropdownMenu modal={false}>
        <DropdownMenuTrigger asChild>
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label={t('assistant.conversations.actions', { title: conversation.title })}
            // Shown on hover on a desktop; always on touch screens, which have no hover.
            className={cn('shrink-0 data-[state=open]:opacity-100 md:opacity-0 md:group-hover:opacity-100 md:focus-visible:opacity-100', active && 'md:opacity-100')}
          >
            <EllipsisIcon />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuItem onSelect={onRename}>
            <PencilIcon />
            {t('assistant.conversations.rename')}
          </DropdownMenuItem>
          <DropdownMenuItem variant="destructive" onSelect={onDelete}>
            <Trash2Icon />
            {t('assistant.conversations.delete')}
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </li>
  );
}

function RenameDialog({ conversation, onClose }: { conversation: AssistantConversation; onClose: () => void }) {
  const { t } = useTranslation();
  const rename = useRenameConversation();
  const [title, setTitle] = useState(conversation.title);

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!title.trim()) return;
    rename.mutate({ threadId: conversation.thread_id, title }, { onSuccess: onClose, onError: (error) => toastError(error, t) });
  }

  return (
    <Dialog open onOpenChange={(open) => !open && !rename.isPending && onClose()}>
      <DialogContent>
        <form onSubmit={submit} className="space-y-4">
          <DialogHeader>
            <DialogTitle>{t('assistant.conversations.renameTitle')}</DialogTitle>
          </DialogHeader>
          <Input
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            maxLength={TITLE_CHARS}
            aria-label={t('assistant.conversations.titleLabel')}
            autoFocus
          />
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose} disabled={rename.isPending}>
              {t('common.cancel')}
            </Button>
            <Button type="submit" disabled={!title.trim() || rename.isPending}>
              {rename.isPending && <Spinner />}
              {t('common.save')}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/** Deleting the conversation on screen starts a new one. */
function DeleteDialog({ conversation, active, onClose }: { conversation: AssistantConversation; active: boolean; onClose: () => void }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const remove = useDeleteConversation();

  function confirm() {
    remove.mutate(conversation.thread_id, {
      onSuccess: () => {
        onClose();
        if (active) void navigate({ to: '/assistant' });
      },
      onError: (error) => toastError(error, t),
    });
  }

  return (
    <AlertDialog open onOpenChange={(open) => !open && !remove.isPending && onClose()}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{t('assistant.conversations.deleteTitle')}</AlertDialogTitle>
          <AlertDialogDescription>{t('assistant.conversations.deleteDescription', { title: conversation.title })}</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={remove.isPending}>{t('common.cancel')}</AlertDialogCancel>
          <Button variant="destructive" disabled={remove.isPending} onClick={confirm}>
            {remove.isPending && <Spinner />}
            {t('assistant.conversations.delete')}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
