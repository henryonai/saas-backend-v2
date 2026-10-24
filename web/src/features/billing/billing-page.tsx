import { useQueryClient } from '@tanstack/react-query';
import { CheckIcon, CircleAlertIcon, ClockIcon, InfoIcon } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { qk } from '@/shared/api/keys';
import type { BillingSummary, PaidPlan } from '@/shared/api/types';
import { refreshSession, useUser } from '@/shared/auth/session';
import { PageHeader } from '@/shared/components/page-header';
import { ErrorState, LoadingRows } from '@/shared/components/query-state';
import { Alert, AlertDescription, AlertTitle } from '@/shared/components/ui/alert';
import { Badge } from '@/shared/components/ui/badge';
import { Button } from '@/shared/components/ui/button';
import { Card, CardAction, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/shared/components/ui/card';
import { Spinner } from '@/shared/components/ui/spinner';
import { toastError } from '@/shared/lib/errors';
import { formatNumber } from '@/shared/lib/format';
import { getPlan, type Plan, PLANS } from '@/shared/lib/plans';
import { cn } from '@/shared/lib/utils';
import { waitForWorkspacePlan } from './api';
import { useBilling, useCheckout, usePortal } from './hooks';
import { clearPendingCheckout, type PendingCheckout, readPendingCheckout, savePendingCheckout } from './pending-checkout';

const POLL_MS = 3000;
const POLL_FOR_MS = 2 * 60 * 1000;

const STATUS_STYLES: Record<string, string> = {
  active: 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300',
  past_due: 'bg-amber-500/15 text-amber-700 dark:text-amber-300',
  cancelled: 'bg-muted text-muted-foreground',
};

export function BillingPage() {
  const { t } = useTranslation();
  const { tenantId, role } = useUser();
  const queryClient = useQueryClient();
  const [pending, setPending] = useState<PendingCheckout | null>(() => readPendingCheckout(tenantId));
  const [pollUntil, setPollUntil] = useState(() => Date.now() + POLL_FOR_MS);
  const [timedOut, setTimedOut] = useState(false);

  // The webhook, not the browser, changes the plan: ask until /billing shows it.
  const isConfirmed = (data: BillingSummary | undefined) => !!pending && data?.plan === pending.plan && data.status === 'active';
  const billing = useBilling((query) => (!pending || timedOut || isConfirmed(query.state.data) ? false : POLL_MS));
  const confirmed = isConfirmed(billing.data);
  const waiting = !!pending && !confirmed;
  const polling = waiting && !timedOut;

  // Stop asking after a while; "Check again" starts over.
  useEffect(() => {
    if (!polling) return;
    const timer = setTimeout(() => setTimedOut(true), Math.max(0, pollUntil - Date.now()));
    return () => clearTimeout(timer);
  }, [polling, pollUntil]);

  // The plan changed. New tokens carry it to every module (orders reads the plan from the
  // token), once the accounts module's copy has it too: the token is made from that copy.
  const announced = useRef(false);
  useEffect(() => {
    if (!confirmed || !pending || announced.current) return;
    announced.current = true;
    clearPendingCheckout();
    toast.success(t('billing.upgraded', { plan: billing.data?.plan_name }));
    void waitForWorkspacePlan(pending.plan)
      .catch(() => false) // refresh anyway; the next automatic refresh catches up
      .then(() => refreshSession())
      .then(() => queryClient.invalidateQueries({ queryKey: qk.tenant() }));
  }, [confirmed, pending, billing.data, queryClient, t]);

  function checkAgain() {
    setPollUntil(Date.now() + POLL_FOR_MS);
    setTimedOut(false);
    void billing.refetch();
  }

  function stopWaiting() {
    clearPendingCheckout();
    setPending(null);
  }

  return (
    <>
      <PageHeader title={t('billing.title')} description={t('billing.description')} />
      <div className="space-y-6">
        {waiting && (
          <Alert>
            {polling ? <Spinner /> : <ClockIcon />}
            <AlertTitle>{t(polling ? 'billing.waiting.title' : 'billing.waiting.stillTitle')}</AlertTitle>
            <AlertDescription>{t('billing.waiting.description', { plan: getPlan(pending.plan).name })}</AlertDescription>
            <div className="col-start-2 mt-2 flex gap-2">
              {!polling && (
                <Button size="sm" variant="outline" onClick={checkAgain}>
                  {t('billing.waiting.checkAgain')}
                </Button>
              )}
              <Button size="sm" variant="ghost" onClick={stopWaiting}>
                {t('billing.waiting.dismiss')}
              </Button>
            </div>
          </Alert>
        )}
        {billing.isPending ? (
          <LoadingRows rows={3} />
        ) : billing.isError ? (
          <ErrorState error={billing.error} onRetry={() => billing.refetch()} />
        ) : (
          <>
            <StatusNotice billing={billing.data} />
            <CurrentPlan billing={billing.data} canManage={role === 'owner' && billing.data.manageable} />
            {role !== 'owner' && (
              <Alert>
                <InfoIcon />
                <AlertDescription>{t('billing.ownerOnly')}</AlertDescription>
              </Alert>
            )}
            <div className="grid gap-4 md:grid-cols-3">
              {PLANS.map((plan) => (
                <PlanCard
                  key={plan.id}
                  plan={plan}
                  current={billing.data}
                  canUpgrade={role === 'owner'}
                  onStarted={(chosen) => tenantId && savePendingCheckout(tenantId, chosen)}
                />
              ))}
            </div>
          </>
        )}
      </div>
    </>
  );
}

function StatusNotice({ billing }: { billing: BillingSummary }) {
  const { t } = useTranslation();
  if (billing.status === 'past_due') {
    return (
      <Alert variant="destructive">
        <CircleAlertIcon />
        <AlertTitle>{t('billing.pastDue.title')}</AlertTitle>
        <AlertDescription>{t('billing.pastDue.description')}</AlertDescription>
      </Alert>
    );
  }
  if (billing.status === 'cancelled') {
    return (
      <Alert>
        <InfoIcon />
        <AlertDescription>{t('billing.cancelled')}</AlertDescription>
      </Alert>
    );
  }
  return null;
}

function CurrentPlan({ billing, canManage }: { billing: BillingSummary; canManage: boolean }) {
  const { t } = useTranslation();
  const status = billing.status in STATUS_STYLES ? billing.status : 'active';
  return (
    <Card>
      <CardHeader>
        <CardDescription>{t('billing.currentPlan')}</CardDescription>
        <CardTitle className="flex items-center gap-3 text-2xl">
          {billing.plan_name}
          <Badge variant="secondary" className={cn('border-transparent', STATUS_STYLES[status])}>
            {t(`billing.status.${status as 'active' | 'past_due' | 'cancelled'}`)}
          </Badge>
        </CardTitle>
        {canManage && (
          <CardAction>
            <ManageBilling />
          </CardAction>
        )}
      </CardHeader>
      <CardContent className="space-y-1">
        <p className="text-sm text-muted-foreground">
          {billing.price_usd_month === 0 ? t('billing.free') : t('billing.price', { price: billing.price_usd_month })}
        </p>
        {canManage && <p className="text-sm text-muted-foreground">{t('billing.manageHint')}</p>}
      </CardContent>
    </Card>
  );
}

function ManageBilling() {
  const { t } = useTranslation();
  const portal = usePortal();

  async function open() {
    try {
      const { portal_url } = await portal.mutateAsync();
      window.location.assign(portal_url);
    } catch (error) {
      toastError(error, t);
    }
  }

  return (
    <Button variant="outline" onClick={open} disabled={portal.isPending}>
      {portal.isPending && <Spinner />}
      {t('billing.manage')}
    </Button>
  );
}

function PlanCard({ plan, current, canUpgrade, onStarted }: {
  plan: Plan;
  current: BillingSummary;
  canUpgrade: boolean;
  onStarted: (plan: string) => void;
}) {
  const { t } = useTranslation();
  const checkout = useCheckout();
  const isCurrent = plan.id === current.plan;
  const isUpgrade = plan.price_usd_month > current.price_usd_month;

  async function upgrade() {
    try {
      const { checkout_url } = await checkout.mutateAsync(plan.id as PaidPlan);
      onStarted(plan.id);
      window.location.assign(checkout_url);
    } catch (error) {
      toastError(error, t);
    }
  }

  const features = [
    plan.max_users === null ? t('billing.features.usersUnlimited') : t('billing.features.users', { value: formatNumber(plan.max_users) }),
    plan.max_orders === null ? t('billing.features.ordersUnlimited') : t('billing.features.orders', { value: formatNumber(plan.max_orders) }),
  ];

  return (
    <Card className={cn(isCurrent && 'ring-2 ring-primary')}>
      <CardHeader>
        <CardTitle>{plan.name}</CardTitle>
        <CardDescription>
          <span className="text-2xl font-semibold text-foreground">${plan.price_usd_month}</span> {t('billing.perMonth')}
        </CardDescription>
      </CardHeader>
      <CardContent className="flex-1">
        <ul className="space-y-2 text-sm">
          {features.map((feature) => (
            <li key={feature} className="flex items-start gap-2">
              <CheckIcon className="mt-0.5 size-4 shrink-0 text-primary" />
              {feature}
            </li>
          ))}
        </ul>
      </CardContent>
      <CardFooter>
        {isCurrent ? (
          <Button variant="outline" className="w-full" disabled>
            {t('billing.isCurrent')}
          </Button>
        ) : isUpgrade && canUpgrade ? (
          <Button className="w-full" onClick={upgrade} disabled={checkout.isPending}>
            {checkout.isPending && <Spinner />}
            {t('billing.upgradeTo', { plan: plan.name })}
          </Button>
        ) : null}
      </CardFooter>
    </Card>
  );
}
