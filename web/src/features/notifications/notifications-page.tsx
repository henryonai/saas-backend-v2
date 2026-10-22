import { BellIcon, CheckCheckIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { Notification } from '@/shared/api/types';
import { PageHeader } from '@/shared/components/page-header';
import { ErrorState, LoadingRows } from '@/shared/components/query-state';
import { Button } from '@/shared/components/ui/button';
import { Card, CardContent } from '@/shared/components/ui/card';
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from '@/shared/components/ui/empty';
import { Spinner } from '@/shared/components/ui/spinner';
import { toastError } from '@/shared/lib/errors';
import { useMarkAllRead, useMarkRead, useNotifications } from './hooks';
import { NotificationItem } from './components/notification-item';

export function NotificationsPage() {
  const { t } = useTranslation();
  const notifications = useNotifications();
  const markRead = useMarkRead();
  const markAll = useMarkAllRead();
  const unreadIds = (notifications.data ?? []).filter((item) => !item.read).map((item) => item.notification_id);

  function onOpen(notification: Notification) {
    if (!notification.read) markRead.mutate(notification.notification_id);
  }

  return (
    <>
      <PageHeader
        title={t('notifications.title')}
        description={t('notifications.description')}
        actions={
          unreadIds.length > 0 && (
            <Button variant="outline" disabled={markAll.isPending} onClick={() => markAll.mutate(unreadIds, { onError: (error) => toastError(error, t) })}>
              {markAll.isPending ? <Spinner /> : <CheckCheckIcon />}
              {t('notifications.markAll')}
            </Button>
          )
        }
      />
      {notifications.isPending ? (
        <LoadingRows rows={4} />
      ) : notifications.isError ? (
        <ErrorState error={notifications.error} onRetry={() => notifications.refetch()} />
      ) : notifications.data.length === 0 ? (
        <Empty className="border border-dashed">
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <BellIcon />
            </EmptyMedia>
            <EmptyTitle>{t('notifications.emptyTitle')}</EmptyTitle>
            <EmptyDescription>{t('notifications.empty')}</EmptyDescription>
          </EmptyHeader>
        </Empty>
      ) : (
        <Card>
          <CardContent className="space-y-1 px-2">
            {notifications.data.map((item) => (
              <NotificationItem key={item.notification_id} notification={item} onOpen={onOpen} />
            ))}
          </CardContent>
        </Card>
      )}
    </>
  );
}
