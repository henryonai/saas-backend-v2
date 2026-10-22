import { PackageIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { PageHeader } from '@/shared/components/page-header';
import { ErrorState, LoadingRows } from '@/shared/components/query-state';
import { Button } from '@/shared/components/ui/button';
import { Card } from '@/shared/components/ui/card';
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from '@/shared/components/ui/empty';
import { Spinner } from '@/shared/components/ui/spinner';
import { useOrderList } from './hooks';
import { NewOrderDialog } from './components/new-order-dialog';
import { OrdersTable } from './components/orders-table';

export function OrdersPage() {
  const { t } = useTranslation();
  const orders = useOrderList();
  const items = orders.data?.pages.flatMap((page) => page.items) ?? [];

  return (
    <>
      <PageHeader title={t('orders.title')} description={t('orders.description')} actions={<NewOrderDialog />} />
      {orders.isPending ? (
        <LoadingRows rows={5} />
      ) : orders.isError ? (
        <ErrorState error={orders.error} onRetry={() => orders.refetch()} />
      ) : items.length === 0 ? (
        <Empty className="border border-dashed">
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <PackageIcon />
            </EmptyMedia>
            <EmptyTitle>{t('orders.empty.title')}</EmptyTitle>
            <EmptyDescription>{t('orders.empty.description')}</EmptyDescription>
          </EmptyHeader>
          <EmptyContent>
            <NewOrderDialog />
          </EmptyContent>
        </Empty>
      ) : (
        <div className="space-y-4">
          <Card className="py-0">
            <OrdersTable orders={items} />
          </Card>
          {orders.hasNextPage && (
            <div className="flex justify-center">
              <Button variant="outline" onClick={() => orders.fetchNextPage()} disabled={orders.isFetchingNextPage}>
                {orders.isFetchingNextPage && <Spinner />}
                {t('common.loadMore')}
              </Button>
            </div>
          )}
        </div>
      )}
    </>
  );
}
