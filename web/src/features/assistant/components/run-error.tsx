import type { TFunction } from 'i18next';
import { CircleAlertIcon, RotateCwIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Alert, AlertDescription, AlertTitle } from '@/shared/components/ui/alert';
import { Button } from '@/shared/components/ui/button';
import { UpgradeNotice } from '@/shared/components/upgrade-notice';
import type { RunError } from '../api';

/** Errors about the request itself (schemas.ERROR_CODES): asking again gets the same answer. */
const FINAL = new Set(['VALIDATION_ERROR', 'UNAUTHORIZED', 'ACCESS_DENIED', 'NOT_FOUND', 'CONFLICT']);

/**
 * Like errorMessage() for the HTTP API: business errors carry the backend's
 * own explanation, in English; network and server trouble gets a translated one.
 */
function messageOf(error: RunError, t: TFunction): string {
  switch (error.code) {
    case 'NETWORK_ERROR':
      return t('errors.network');
    case 'UNAUTHORIZED':
      return t('errors.signedOut');
    case 'INTERNAL_ERROR':
      return t('assistant.errors.internal');
    case 'SERVICE_BUSY':
    case 'RATE_LIMIT_EXCEEDED':
      return t('assistant.errors.busy');
    default:
      return error.message;
  }
}

/** A RUN_ERROR: the plan's limit offers an upgrade, the rest say what happened, with "Try again" when it may help. */
export function RunErrorNotice({ error, onRetry }: { error: RunError; onRetry: () => void }) {
  const { t } = useTranslation();
  if (error.code === 'PLAN_LIMIT_REACHED') return <UpgradeNotice message={error.message} />;
  return (
    <Alert variant="destructive">
      <CircleAlertIcon />
      <AlertTitle>{t('assistant.errorTitle')}</AlertTitle>
      <AlertDescription>{messageOf(error, t)}</AlertDescription>
      {!FINAL.has(error.code) && (
        <div className="col-start-2 mt-2">
          <Button size="sm" variant="outline" onClick={onRetry}>
            <RotateCwIcon />
            {t('common.retry')}
          </Button>
        </div>
      )}
    </Alert>
  );
}
