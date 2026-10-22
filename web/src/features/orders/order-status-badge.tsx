import { useTranslation } from 'react-i18next';
import type { OrderStatus } from '@/shared/api/types';
import { Badge } from '@/shared/components/ui/badge';
import { cn } from '@/shared/lib/utils';

const STYLES: Record<OrderStatus, string> = {
  pending: 'bg-amber-500/15 text-amber-700 dark:text-amber-300',
  paid: 'bg-sky-500/15 text-sky-700 dark:text-sky-300',
  shipped: 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300',
  cancelled: 'bg-muted text-muted-foreground',
};

export function OrderStatusBadge({ status }: { status: OrderStatus }) {
  const { t } = useTranslation();
  return (
    <Badge variant="secondary" className={cn('border-transparent', STYLES[status])}>
      {t(`orders.status.${status}`)}
    </Badge>
  );
}
