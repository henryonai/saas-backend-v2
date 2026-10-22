import { Link } from '@tanstack/react-router';
import { ArrowRightIcon, CircleAlertIcon } from 'lucide-react';
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import type { Notification } from '@/shared/api/types';
import { PageHeader } from '@/shared/components/page-header';
import { ErrorState, LoadingRows } from '@/shared/components/query-state';
import { Alert, AlertDescription, AlertTitle } from '@/shared/components/ui/alert';
import { Button } from '@/shared/components/ui/button';
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from '@/shared/components/ui/card';
import { Progress } from '@/shared/components/ui/progress';
import { Skeleton } from '@/shared/components/ui/skeleton';
import { formatNumber } from '@/shared/lib/format';
import { getPlan } from '@/shared/lib/plans';
import { useMarkRead, useNotifications } from '../notifications/hooks';
import { NotificationItem } from '../notifications/components/notification-item';
import { useRecentOrders } from '../orders/hooks';
import { OrdersTable } from '../orders/components/orders-table';
import { useTenant } from '../workspace/hooks';

export function OverviewPage() {
  const { t } = useTranslation();
  const tenant = useTenant();

  if (tenant.isError) return <ErrorState error={tenant.error} onRetry={() => tenant.refetch()} />;
  const data = tenant.data;
  const plan = data ? getPlan(data.plan) : null;

  return (
    <>
      <PageHeader title={data ? data.name : <Skeleton className="h-8 w-48" />} description={t('overview.description')} />
      <div className="space-y-6">
        {data?.subscription_status === 'past_due' && (
          <Alert variant="destructive">
            <CircleAlertIcon />
            <AlertTitle>{t('billing.pastDue.title')}</AlertTitle>
            <AlertDescription>{t('billing.pastDue.description')}</AlertDescription>
          </Alert>
        )}
        <div className="grid gap-4 sm:grid-cols-3">
          <Stat title={t('overview.plan')} link={<Link to="/billing">{t('overview.managePlan')}</Link>}>
            {plan ? plan.name : <Skeleton className="h-7 w-20" />}
          </Stat>
          <Stat title={t('overview.members')} link={<Link to="/team">{t('overview.manageTeam')}</Link>}>
            {data && plan ? (
              <div className="space-y-2">
                <span>
                  {formatNumber(data.seats_used)}
                  <span className="text-base font-normal text-muted-foreground">
                    {' / '}
                    {plan.max_users === null ? t('common.unlimited') : formatNumber(plan.max_users)}
                  </span>
                </span>
                {plan.max_users !== null && <Progress value={Math.min(100, (data.seats_used / plan.max_users) * 100)} />}
              </div>
            ) : (
              <Skeleton className="h-7 w-20" />
            )}
          </Stat>
          <Stat title={t('overview.orderLimit')} link={<Link to="/orders">{t('overview.viewOrders')}</Link>}>
            {plan ? (plan.max_orders === null ? t('common.unlimited') : formatNumber(plan.max_orders)) : <Skeleton className="h-7 w-20" />}
          </Stat>
        </div>
        <div className="grid gap-4 lg:grid-cols-5">
          <RecentOrders />
          <RecentNotifications />
        </div>
      </div>
    </>
  );
}

function Stat({ title, link, children }: { title: string; link: ReactNode; children: ReactNode }) {
  return (
    <Card>
      <CardHeader>
        <CardDescription>{title}</CardDescription>
        <CardAction>
          <Button variant="link" size="sm" className="h-auto p-0" asChild>
            {link}
          </Button>
        </CardAction>
      </CardHeader>
      <CardContent className="text-2xl font-semibold tabular-nums">{children}</CardContent>
    </Card>
  );
}

function RecentOrders() {
  const { t } = useTranslation();
  const orders = useRecentOrders();
  return (
    <Card className="lg:col-span-3">
      <CardHeader>
        <CardTitle>{t('overview.recentOrders')}</CardTitle>
        <CardAction>
          <Button variant="ghost" size="sm" asChild>
            <Link to="/orders">
              {t('common.viewAll')}
              <ArrowRightIcon />
            </Link>
          </Button>
        </CardAction>
      </CardHeader>
      <CardContent>
        {orders.isPending ? (
          <LoadingRows rows={3} />
        ) : orders.isError ? (
          <ErrorState error={orders.error} onRetry={() => orders.refetch()} />
        ) : orders.data.items.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t('orders.empty.description')}</p>
        ) : (
          <OrdersTable orders={orders.data.items} compact />
        )}
      </CardContent>
    </Card>
  );
}

function RecentNotifications() {
  const { t } = useTranslation();
  const notifications = useNotifications();
  const markRead = useMarkRead();

  function onOpen(notification: Notification) {
    if (!notification.read) markRead.mutate(notification.notification_id);
  }

  return (
    <Card className="lg:col-span-2">
      <CardHeader>
        <CardTitle>{t('notifications.title')}</CardTitle>
        <CardAction>
          <Button variant="ghost" size="sm" asChild>
            <Link to="/notifications">
              {t('common.viewAll')}
              <ArrowRightIcon />
            </Link>
          </Button>
        </CardAction>
      </CardHeader>
      <CardContent className="px-2">
        {notifications.isPending ? (
          <LoadingRows rows={3} />
        ) : notifications.isError ? (
          <ErrorState error={notifications.error} onRetry={() => notifications.refetch()} />
        ) : notifications.data.length === 0 ? (
          <p className="px-2 text-sm text-muted-foreground">{t('notifications.empty')}</p>
        ) : (
          <div className="space-y-1">
            {notifications.data.slice(0, 5).map((item) => (
              <NotificationItem key={item.notification_id} notification={item} onOpen={onOpen} compact />
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
