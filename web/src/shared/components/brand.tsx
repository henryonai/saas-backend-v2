import { LayersIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { cn } from '@/shared/lib/utils';

export function BrandMark({ className }: { className?: string }) {
  return (
    <span
      className={cn('flex size-8 shrink-0 items-center justify-center rounded-lg bg-primary text-primary-foreground', className)}
    >
      <LayersIcon className="size-4" />
    </span>
  );
}

export function Brand() {
  const { t } = useTranslation();
  return (
    <span className="flex items-center gap-2 font-semibold">
      <BrandMark />
      {t('app.name')}
    </span>
  );
}
