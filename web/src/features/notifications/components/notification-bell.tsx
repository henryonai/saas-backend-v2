import { Link } from '@tanstack/react-router';
import { BellIcon } from 'lucide-react';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { Notification } from '@/shared/api/types';
import { Button } from '@/shared/components/ui/button';
import { Popover, PopoverContent, PopoverTrigger } from '@/shared/components/ui/popover';
import { useMarkRead, useNotifications } from '../hooks';
import { NotificationItem } from './notification-item';

const SHOWN = 6;

export function NotificationBell() {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const notifications = useNotifications({ poll: true });
  const markRead = useMarkRead();
  const items = notifications.data ?? [];
  const unread = items.filter((item) => !item.read).length;

  function onOpen(notification: Notification) {
    if (!notification.read) markRead.mutate(notification.notification_id);
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button variant="ghost" size="icon" className="relative" aria-label={t('notifications.bell', { unread })}>
          <BellIcon />
          {unread > 0 && (
            <span className="absolute top-1 right-1 flex min-w-4 items-center justify-center rounded-full bg-primary px-1 text-[10px] leading-4 font-medium text-primary-foreground">
              {unread > 9 ? '9+' : unread}
            </span>
          )}
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-80 p-2">
        <p className="px-2 pt-1 pb-2 text-sm font-medium">{t('notifications.title')}</p>
        {items.length === 0 ? (
          <p className="px-2 pb-2 text-sm text-muted-foreground">{t('notifications.empty')}</p>
        ) : (
          <div className="space-y-1">
            {items.slice(0, SHOWN).map((item) => (
              <NotificationItem key={item.notification_id} notification={item} onOpen={onOpen} compact />
            ))}
          </div>
        )}
        <Button variant="ghost" size="sm" className="mt-1 w-full" asChild>
          <Link to="/notifications" onClick={() => setOpen(false)}>
            {t('notifications.viewAll')}
          </Link>
        </Button>
      </PopoverContent>
    </Popover>
  );
}
