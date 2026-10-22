import { Link, type ErrorComponentProps } from '@tanstack/react-router';
import { useTranslation } from 'react-i18next';
import { ErrorState } from '@/shared/components/query-state';
import { Button } from '@/shared/components/ui/button';
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyTitle } from '@/shared/components/ui/empty';

export function RouteError({ error, reset }: ErrorComponentProps) {
  return (
    <div className="p-6">
      <ErrorState error={error} onRetry={reset} />
    </div>
  );
}

export function RouteNotFound() {
  const { t } = useTranslation();
  return (
    <Empty className="min-h-[60svh]">
      <EmptyHeader>
        <EmptyTitle>{t('errors.notFoundTitle')}</EmptyTitle>
        <EmptyDescription>{t('errors.notFound')}</EmptyDescription>
      </EmptyHeader>
      <EmptyContent>
        <Button asChild variant="outline">
          <Link to="/">{t('common.home')}</Link>
        </Button>
      </EmptyContent>
    </Empty>
  );
}
