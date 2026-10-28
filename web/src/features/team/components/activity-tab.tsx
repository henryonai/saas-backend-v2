import { useInfiniteQuery } from '@tanstack/react-query';
import { HistoryIcon } from 'lucide-react';
import type { TFunction } from 'i18next';
import { useTranslation } from 'react-i18next';
import { api } from '@/shared/api/client';
import { qk } from '@/shared/api/keys';
import type { AuditEntry, AuditPage } from '@/shared/api/types';
import { ErrorState, LoadingRows } from '@/shared/components/query-state';
import { Button } from '@/shared/components/ui/button';
import { Card } from '@/shared/components/ui/card';
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from '@/shared/components/ui/empty';
import { Spinner } from '@/shared/components/ui/spinner';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/shared/components/ui/table';
import { formatDateTime } from '@/shared/lib/format';
import { getPlan } from '@/shared/lib/plans';

/** Owners and admins: who did what in the workspace, newest first (GET /audit). */
export function ActivityTab() {
  const { t } = useTranslation();
  const audit = useInfiniteQuery({
    queryKey: qk.audit(),
    queryFn: ({ pageParam, signal }) =>
      api<AuditPage>(`/audit${pageParam ? `?cursor=${encodeURIComponent(pageParam)}` : ''}`, { signal }),
    initialPageParam: '',
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });

  if (audit.isPending) return <LoadingRows rows={3} />;
  if (audit.isError) return <ErrorState error={audit.error} onRetry={() => audit.refetch()} />;
  const entries = audit.data.pages.flatMap((page) => page.items);
  if (entries.length === 0) {
    return (
      <Empty className="border border-dashed">
        <EmptyHeader>
          <EmptyMedia variant="icon">
            <HistoryIcon />
          </EmptyMedia>
          <EmptyTitle>{t('team.activity.emptyTitle')}</EmptyTitle>
          <EmptyDescription>{t('team.activity.emptyDescription')}</EmptyDescription>
        </EmptyHeader>
      </Empty>
    );
  }

  return (
    <div className="space-y-4">
      <Card className="py-0">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t('team.activity.when')}</TableHead>
              <TableHead>{t('team.activity.who')}</TableHead>
              <TableHead>{t('team.activity.what')}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {entries.map((entry) => (
              <TableRow key={entry.event_id}>
                <TableCell className="whitespace-nowrap text-muted-foreground">{formatDateTime(entry.occurred_at)}</TableCell>
                <TableCell>{entry.actor_email || (entry.actor_id ? entry.actor_id : t('team.activity.system'))}</TableCell>
                <TableCell>{describe(entry, t)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Card>
      {audit.hasNextPage && (
        <div className="flex justify-center">
          <Button variant="outline" onClick={() => audit.fetchNextPage()} disabled={audit.isFetchingNextPage}>
            {audit.isFetchingNextPage && <Spinner />}
            {t('common.loadMore')}
          </Button>
        </div>
      )}
    </div>
  );
}

/** "Changed bob@example.com's role to admin": one sentence per event, from its details. */
function describe(entry: AuditEntry, t: TFunction): string {
  const d = entry.details as Record<string, unknown>;
  // A type the app doesn't know yet (added to the backend first) shows its name.
  const key = `team.activity.events.${entry.type}` as 'team.activity.events.TenantCreated';
  const role = typeof d.role === 'string' ? t(`roles.${d.role}` as 'roles.member') : undefined;
  const plan = typeof d.plan === 'string' ? getPlan(d.plan).name : undefined;
  return String(t(key, { ...d, role, plan, defaultValue: entry.type }));
}
