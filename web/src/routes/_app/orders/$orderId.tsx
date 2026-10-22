import { createFileRoute } from '@tanstack/react-router';
import { OrderDetailPage } from '@/features/orders/order-detail-page';

export const Route = createFileRoute('/_app/orders/$orderId')({
  component: OrderRoute,
});

function OrderRoute() {
  const { orderId } = Route.useParams();
  return <OrderDetailPage key={orderId} orderId={orderId} />;
}
