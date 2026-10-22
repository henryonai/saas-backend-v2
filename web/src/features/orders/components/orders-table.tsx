import { useNavigate } from '@tanstack/react-router';
import { useTranslation } from 'react-i18next';
import type { Order } from '@/shared/api/types';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/shared/components/ui/table';
import { formatDateTime, formatMoney } from '@/shared/lib/format';
import { OrderStatusBadge } from './order-status-badge';

export function OrdersTable({ orders, compact = false }: { orders: Order[]; compact?: boolean }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>{t('orders.columns.item')}</TableHead>
          {!compact && <TableHead className="text-right">{t('orders.columns.quantity')}</TableHead>}
          {!compact && <TableHead className="text-right">{t('orders.columns.unitPrice')}</TableHead>}
          <TableHead className="text-right">{t('orders.columns.total')}</TableHead>
          <TableHead>{t('orders.columns.status')}</TableHead>
          <TableHead className="hidden md:table-cell">{t('orders.columns.created')}</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {orders.map((order) => (
          <TableRow
            key={order.order_id}
            className="cursor-pointer"
            tabIndex={0}
            onClick={() => navigate({ to: '/orders/$orderId', params: { orderId: order.order_id } })}
            onKeyDown={(event) => {
              if (event.key === 'Enter') void navigate({ to: '/orders/$orderId', params: { orderId: order.order_id } });
            }}
          >
            <TableCell className="max-w-64 truncate font-medium">{order.item}</TableCell>
            {!compact && <TableCell className="text-right tabular-nums">{order.quantity}</TableCell>}
            {!compact && <TableCell className="text-right tabular-nums">{formatMoney(order.price_cents)}</TableCell>}
            <TableCell className="text-right tabular-nums">{formatMoney(order.price_cents * order.quantity)}</TableCell>
            <TableCell>
              <OrderStatusBadge status={order.status} />
            </TableCell>
            <TableCell className="hidden text-muted-foreground md:table-cell">{formatDateTime(order.created_at)}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
