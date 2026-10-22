import { Link } from '@tanstack/react-router';
import { ArrowLeftIcon } from 'lucide-react';
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { ApiError } from '@/shared/api/client';
import type { OrderStatus } from '@/shared/api/types';
import { useUser } from '@/shared/auth/session';
import { ConfirmDialog } from '@/shared/components/confirm-dialog';
import { PageHeader } from '@/shared/components/page-header';
import { ErrorState, LoadingRows } from '@/shared/components/query-state';
import { Button } from '@/shared/components/ui/button';
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from '@/shared/components/ui/card';
import { Spinner } from '@/shared/components/ui/spinner';
import { toastError } from '@/shared/lib/errors';
import { formatDateTime, formatMoney } from '@/shared/lib/format';
import { useChangeOrderStatus, useOrder } from './api';
import { OrderStatusBadge } from './order-status-badge';
import { nextStatuses } from './status';

export function OrderDetailPage({ orderId }: { orderId: string }) {
  const { t } = useTranslation();
  const { userId } = useUser();
  const order = useOrder(orderId);
  const change = useChangeOrderStatus(orderId);

  const back = (
    <Button variant="ghost" size="sm" asChild className="-ml-2 mb-2">
      <Link to="/orders">
        <ArrowLeftIcon />
        {t('orders.detail.back')}
      </Link>
    </Button>
  );

  if (order.isPending) return <>{back}<LoadingRows rows={4} /></>;
  if (order.isError) {
    const notFound = order.error instanceof ApiError && order.error.status === 404;
    return (
      <>
        {back}
        <ErrorState error={order.error} onRetry={notFound ? undefined : () => order.refetch()} />
      </>
    );
  }

  const { data } = order;

  async function moveTo(status: OrderStatus) {
    try {
      await change.mutateAsync(status);
      toast.success(t('orders.detail.changed', { status: t(`orders.status.${status}`) }));
    } catch (error) {
      // 400 (not allowed any more) or 409 (changed by someone else): the order is reloaded
      toastError(error, t);
      throw error;
    }
  }

  const actions = nextStatuses(data.status);

  return (
    <>
      {back}
      <PageHeader title={data.item} actions={<OrderStatusBadge status={data.status} />} />
      <Card>
        <CardHeader>
          <CardTitle>{t('orders.detail.title')}</CardTitle>
        </CardHeader>
        <CardContent>
          <dl className="grid gap-4 sm:grid-cols-2">
            <Detail label={t('orders.columns.quantity')}>{data.quantity}</Detail>
            <Detail label={t('orders.columns.unitPrice')}>{formatMoney(data.price_cents)}</Detail>
            <Detail label={t('orders.columns.total')}>{formatMoney(data.price_cents * data.quantity)}</Detail>
            <Detail label={t('orders.columns.created')}>{formatDateTime(data.created_at)}</Detail>
            <Detail label={t('orders.detail.createdBy')}>
              {data.created_by === userId ? t('common.you') : <span className="font-mono text-xs">{data.created_by}</span>}
            </Detail>
            <Detail label={t('orders.detail.id')}>
              <span className="font-mono text-xs">{data.order_id}</span>
            </Detail>
          </dl>
        </CardContent>
        <CardFooter className="mt-6 flex-wrap gap-2 border-t pt-6">
          {actions.length === 0 ? (
            <p className="text-sm text-muted-foreground">{t('orders.detail.final')}</p>
          ) : (
            actions.map((status) =>
              status === 'cancelled' ? (
                <ConfirmDialog
                  key={status}
                  trigger={
                    <Button variant="destructive" disabled={change.isPending}>
                      {t('orders.actions.cancelled')}
                    </Button>
                  }
                  title={t('orders.detail.cancelTitle')}
                  description={t('orders.detail.cancelDescription')}
                  confirmLabel={t('orders.actions.cancelled')}
                  destructive
                  onConfirm={() => moveTo(status)}
                />
              ) : (
                <Button key={status} disabled={change.isPending} onClick={() => moveTo(status).catch(() => {})}>
                  {change.isPending && change.variables === status && <Spinner />}
                  {t(`orders.actions.${status}`)}
                </Button>
              ),
            )
          )}
        </CardFooter>
      </Card>
    </>
  );
}

function Detail({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="space-y-1">
      <dt className="text-sm text-muted-foreground">{label}</dt>
      <dd className="font-medium">{children}</dd>
    </div>
  );
}
