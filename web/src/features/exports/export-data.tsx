import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { DownloadIcon, FileDownIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { api } from '@/shared/api/client';
import { qk } from '@/shared/api/keys';
import type { DataExport } from '@/shared/api/types';
import { Button } from '@/shared/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/shared/components/ui/card';
import { Progress } from '@/shared/components/ui/progress';
import { Spinner } from '@/shared/components/ui/spinner';
import { toastError } from '@/shared/lib/errors';
import { formatDate } from '@/shared/lib/format';

type Kind = 'workspace' | 'me';

/**
 * Starts a copy of the workspace's data (its owner) or of the user's own, and
 * watches it: each module writes its part, and the links appear once all have.
 * The API keeps the files 7 days.
 */
export function ExportData({ kind }: { kind: Kind }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const latest = useQuery({
    queryKey: qk.export(kind),
    queryFn: ({ signal }) => api<DataExport | null>(`/exports/${kind}`, { signal }),
    refetchInterval: (query) => (query.state.data?.status === 'running' ? 3000 : false),
  });
  const start = useMutation({
    mutationFn: () => api<DataExport>(`/exports/${kind}`, { method: 'POST' }),
    onSuccess: (job) => queryClient.setQueryData(qk.export(kind), job),
    onError: (error) => toastError(error, t),
  });
  const job = latest.data;
  const running = job?.status === 'running';

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t(`exports.${kind}.title`)}</CardTitle>
        <CardDescription>{t(`exports.${kind}.description`)}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {running && (
          <div className="space-y-2" aria-live="polite">
            <Progress value={(job.done.length / job.parts.length) * 100} aria-label={t('exports.progress')} />
            <p className="text-xs text-muted-foreground">{t('exports.running', { done: job.done.length, total: job.parts.length })}</p>
          </div>
        )}
        {job?.status === 'done' && (
          <div className="space-y-2">
            <p className="text-sm text-muted-foreground">{t('exports.ready', { date: formatDate(job.expires_at) })}</p>
            <ul className="flex flex-wrap gap-2">
              {Object.entries(job.files).map(([part, url]) => (
                <li key={part}>
                  <Button asChild variant="outline" size="sm">
                    <a href={url} download>
                      <FileDownIcon />
                      {t(`exports.parts.${part}`, { defaultValue: part })}
                    </a>
                  </Button>
                </li>
              ))}
            </ul>
          </div>
        )}
        <Button onClick={() => start.mutate()} disabled={running || start.isPending}>
          {start.isPending ? <Spinner /> : <DownloadIcon />}
          {t('exports.start')}
        </Button>
      </CardContent>
    </Card>
  );
}
