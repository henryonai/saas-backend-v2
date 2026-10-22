import { Link } from '@tanstack/react-router';
import { SparklesIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Alert, AlertDescription, AlertTitle } from '@/shared/components/ui/alert';
import { Button } from '@/shared/components/ui/button';
import { useUser } from '@/shared/auth/session';

/** A 402 from the API: the plan doesn't allow this. Only the owner can upgrade. */
export function UpgradeNotice({ message }: { message: string }) {
  const { t } = useTranslation();
  const { role } = useUser();
  return (
    <Alert>
      <SparklesIcon />
      <AlertTitle>{t('upgrade.title')}</AlertTitle>
      <AlertDescription>
        <p>{message}</p>
        {role !== 'owner' && <p>{t('upgrade.askOwner')}</p>}
      </AlertDescription>
      {role === 'owner' && (
        <div className="col-start-2 mt-2">
          <Button asChild size="sm">
            <Link to="/billing">{t('upgrade.seePlans')}</Link>
          </Button>
        </div>
      )}
    </Alert>
  );
}
