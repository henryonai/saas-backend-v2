import type { Notification } from '@/shared/api/types';
import { formatRelative } from '@/shared/lib/format';
import { cn } from '@/shared/lib/utils';

export function NotificationItem({ notification, onOpen, compact = false }: {
  notification: Notification;
  onOpen: (notification: Notification) => void;
  compact?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={() => onOpen(notification)}
      className={cn(
        'flex w-full gap-3 rounded-lg text-left transition-colors hover:bg-muted focus-visible:bg-muted focus-visible:outline-none',
        compact ? 'p-2' : 'p-3',
      )}
    >
      <span
        aria-hidden
        className={cn('mt-1.5 size-2 shrink-0 rounded-full', notification.read ? 'bg-transparent' : 'bg-primary')}
      />
      <span className="min-w-0 flex-1 space-y-0.5">
        <span className={cn('block text-sm', notification.read ? 'text-muted-foreground' : 'font-medium')}>{notification.title}</span>
        <span className={cn('block text-sm text-muted-foreground', compact && 'line-clamp-2')}>{notification.body}</span>
        <span className="block text-xs text-muted-foreground">{formatRelative(notification.created_at)}</span>
      </span>
    </button>
  );
}
