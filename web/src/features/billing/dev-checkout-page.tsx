import { Link } from '@tanstack/react-router';
import { FlaskConicalIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { CopyButton } from '@/shared/components/copy-button';
import { Brand } from '@/shared/components/brand';
import { Button } from '@/shared/components/ui/button';
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/shared/components/ui/card';
import { getPlan } from '@/shared/lib/plans';
import { clearPendingCheckout } from './pending-checkout';

/**
 * Where DevPaymentProvider (modules/billing/src/billing/provider.py) sends the
 * owner instead of a real checkout page. Only a signed webhook changes the plan,
 * and the browser can't sign one: scripts/dev-pay.py does it, as a provider
 * would after a payment.
 */
export function DevCheckoutPage({ tenantId, plan }: { tenantId?: string; plan?: string }) {
  const { t } = useTranslation();
  const command = `uv run python scripts/dev-pay.py --config-env dev --tenant ${tenantId ?? '<tenant_id>'} --plan ${plan ?? '<plan>'}`;

  return (
    <div className="flex min-h-svh flex-col items-center justify-center gap-6 bg-muted/40 p-4">
      <Brand />
      <Card className="w-full max-w-xl">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <FlaskConicalIcon className="size-5" />
            {t('devCheckout.title')}
          </CardTitle>
          <CardDescription>{t('devCheckout.description', { plan: plan ? getPlan(plan).name : '?' })}</CardDescription>
        </CardHeader>
        <CardContent className="mt-4 space-y-3">
          <p className="text-sm">{t('devCheckout.instructions')}</p>
          <pre className="rounded-lg bg-muted p-3 font-mono text-xs leading-relaxed break-all whitespace-pre-wrap">{command}</pre>
          <CopyButton text={command} />
        </CardContent>
        <CardFooter className="mt-4 justify-end gap-2">
          <Button variant="ghost" asChild>
            <Link to="/billing" onClick={() => clearPendingCheckout()}>
              {t('devCheckout.cancel')}
            </Link>
          </Button>
          <Button asChild>
            <Link to="/billing">{t('devCheckout.done')}</Link>
          </Button>
        </CardFooter>
      </Card>
    </div>
  );
}
