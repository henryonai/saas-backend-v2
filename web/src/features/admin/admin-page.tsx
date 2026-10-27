import { useInfiniteQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { MoreHorizontalIcon } from 'lucide-react';
import { type FormEvent, useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { api } from '@/shared/api/client';
import { qk } from '@/shared/api/keys';
import type { AdminLogPage, AdminWorkspace, AdminWorkspacePage } from '@/shared/api/types';
import { useUser } from '@/shared/auth/session';
import { PageHeader } from '@/shared/components/page-header';
import { ErrorState, LoadingRows } from '@/shared/components/query-state';
import { Badge } from '@/shared/components/ui/badge';
import { Button } from '@/shared/components/ui/button';
import { Card } from '@/shared/components/ui/card';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/shared/components/ui/dialog';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/shared/components/ui/dropdown-menu';
import { Field, FieldLabel } from '@/shared/components/ui/field';
import { Input } from '@/shared/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/shared/components/ui/select';
import { Spinner } from '@/shared/components/ui/spinner';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/shared/components/ui/table';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/shared/components/ui/tabs';
import { toastError } from '@/shared/lib/errors';
import { formatDate, formatDateTime, formatNumber } from '@/shared/lib/format';
import { getPlan, PLANS } from '@/shared/lib/plans';

type Action = { kind: 'plan' | 'suspend'; workspace: AdminWorkspace } | null;

/**
 * The back-office, for platform admins (the platform-admins group): every
 * workspace with its plan, status, members and orders, and what admins did.
 * Granting a plan and suspending go through billing and accounts, so the
 * list shows them a few seconds later.
 */
export function AdminPage() {
  const { t } = useTranslation();
  const { platformAdmin } = useUser();
  if (!platformAdmin) return <p className="text-sm text-muted-foreground">{t('admin.forbidden')}</p>;
  return (
    <>
      <PageHeader title={t('admin.title')} description={t('admin.description')} />
      <Tabs defaultValue="workspaces">
        <TabsList>
          <TabsTrigger value="workspaces">{t('admin.tabs.workspaces')}</TabsTrigger>
          <TabsTrigger value="log">{t('admin.tabs.log')}</TabsTrigger>
        </TabsList>
        <TabsContent value="workspaces" className="mt-4">
          <Workspaces />
        </TabsContent>
        <TabsContent value="log" className="mt-4">
          <Log />
        </TabsContent>
      </Tabs>
    </>
  );
}

function usePaged<T>(key: readonly unknown[], path: string) {
  return useInfiniteQuery({
    queryKey: key,
    queryFn: ({ pageParam, signal }) =>
      api<{ items: T[]; next_cursor: string | null }>(`${path}${pageParam ? `?cursor=${encodeURIComponent(pageParam)}` : ''}`, { signal }),
    initialPageParam: '',
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
}

function Workspaces() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const workspaces = usePaged<AdminWorkspacePage['items'][number]>(qk.adminWorkspaces(), '/admin/tenants');
  const [action, setAction] = useState<Action>(null);
  const unsuspend = useMutation({
    mutationFn: (tenantId: string) => api<void>(`/admin/tenants/${encodeURIComponent(tenantId)}/unsuspend`, { method: 'POST' }),
    onSuccess: () => {
      toast.success(t('admin.applied'));
      void queryClient.invalidateQueries({ queryKey: ['admin'] });
    },
    onError: (error) => toastError(error, t),
  });

  if (workspaces.isPending) return <LoadingRows rows={4} />;
  if (workspaces.isError) return <ErrorState error={workspaces.error} onRetry={() => workspaces.refetch()} />;
  const items = workspaces.data.pages.flatMap((page) => page.items);

  return (
    <div className="space-y-4">
      <Card className="py-0">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t('admin.columns.workspace')}</TableHead>
              <TableHead>{t('admin.columns.plan')}</TableHead>
              <TableHead>{t('admin.columns.status')}</TableHead>
              <TableHead className="text-right">{t('admin.columns.members')}</TableHead>
              <TableHead className="hidden text-right md:table-cell">{t('admin.columns.orders')}</TableHead>
              <TableHead className="hidden md:table-cell">{t('admin.columns.created')}</TableHead>
              <TableHead className="w-0">
                <span className="sr-only">{t('team.columns.actions')}</span>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {items.map((workspace) => (
              <TableRow key={workspace.tenant_id}>
                <TableCell className="font-medium">{workspace.name || workspace.tenant_id}</TableCell>
                <TableCell>
                  {getPlan(workspace.plan).name}
                  {workspace.comped && <span className="ml-2 text-xs text-muted-foreground">{t('admin.comped')}</span>}
                </TableCell>
                <TableCell>
                  <Badge variant={workspace.status === 'active' ? 'secondary' : 'outline'}>{t(`admin.status.${workspace.status}`)}</Badge>
                </TableCell>
                <TableCell className="text-right">{formatNumber(workspace.members)}</TableCell>
                <TableCell className="hidden text-right md:table-cell">{formatNumber(workspace.orders)}</TableCell>
                <TableCell className="hidden md:table-cell">{workspace.created_at ? formatDate(workspace.created_at) : '—'}</TableCell>
                <TableCell>
                  <DropdownMenu modal={false}>
                    <DropdownMenuTrigger asChild>
                      <Button variant="ghost" size="icon" aria-label={t('admin.actionsFor', { workspace: workspace.name })}>
                        <MoreHorizontalIcon />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end">
                      <DropdownMenuItem onSelect={() => setAction({ kind: 'plan', workspace })}>{t('admin.grant.open')}</DropdownMenuItem>
                      {workspace.status === 'suspended' ? (
                        <DropdownMenuItem onSelect={() => unsuspend.mutate(workspace.tenant_id)}>{t('admin.unsuspend')}</DropdownMenuItem>
                      ) : (
                        <DropdownMenuItem onSelect={() => setAction({ kind: 'suspend', workspace })}>{t('admin.suspend.open')}</DropdownMenuItem>
                      )}
                    </DropdownMenuContent>
                  </DropdownMenu>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Card>
      {workspaces.hasNextPage && (
        <div className="flex justify-center">
          <Button variant="outline" onClick={() => workspaces.fetchNextPage()} disabled={workspaces.isFetchingNextPage}>
            {workspaces.isFetchingNextPage && <Spinner />}
            {t('common.loadMore')}
          </Button>
        </div>
      )}
      <ActionDialog action={action} onClose={() => setAction(null)} />
    </div>
  );
}

/** Grant a plan (with a note), or suspend (with a reason): both land in the admin log. */
function ActionDialog({ action, onClose }: { action: Action; onClose: () => void }) {
  const { t } = useTranslation();
  const id = useId();
  const queryClient = useQueryClient();
  const [plan, setPlan] = useState('pro');
  const [text, setText] = useState('');
  const submit = useMutation({
    mutationFn: ({ tenantId, kind }: { tenantId: string; kind: 'plan' | 'suspend' }) =>
      kind === 'plan'
        ? api<void>(`/admin/tenants/${encodeURIComponent(tenantId)}/plan`, { method: 'POST', body: { plan, note: text } })
        : api<void>(`/admin/tenants/${encodeURIComponent(tenantId)}/suspend`, { method: 'POST', body: { reason: text } }),
    onSuccess: () => {
      toast.success(t('admin.applied'));
      void queryClient.invalidateQueries({ queryKey: ['admin'] });
      setText('');
      onClose();
    },
    onError: (error) => toastError(error, t),
  });
  if (!action) return null;
  const granting = action.kind === 'plan';

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (action) submit.mutate({ tenantId: action.workspace.tenant_id, kind: action.kind });
  }

  return (
    <Dialog open onOpenChange={(open) => !open && !submit.isPending && onClose()}>
      <DialogContent>
        <form onSubmit={onSubmit} className="space-y-4">
          <DialogHeader>
            <DialogTitle>{t(granting ? 'admin.grant.title' : 'admin.suspend.title', { workspace: action.workspace.name })}</DialogTitle>
            <DialogDescription>{t(granting ? 'admin.grant.description' : 'admin.suspend.description')}</DialogDescription>
          </DialogHeader>
          {granting && (
            <Field>
              <FieldLabel htmlFor={`${id}-plan`}>{t('admin.columns.plan')}</FieldLabel>
              <Select value={plan} onValueChange={setPlan}>
                <SelectTrigger id={`${id}-plan`}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {PLANS.map((p) => (
                    <SelectItem key={p.id} value={p.id}>
                      {p.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
          )}
          <Field>
            <FieldLabel htmlFor={`${id}-text`}>{t(granting ? 'admin.grant.note' : 'admin.suspend.reason')}</FieldLabel>
            <Input id={`${id}-text`} maxLength={500} value={text} onChange={(event) => setText(event.target.value)} />
          </Field>
          <DialogFooter>
            <Button type="submit" variant={granting ? 'default' : 'destructive'} disabled={submit.isPending || (!granting && !text.trim())}>
              {submit.isPending && <Spinner />}
              {t(granting ? 'admin.grant.submit' : 'admin.suspend.submit')}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function Log() {
  const { t } = useTranslation();
  const log = usePaged<AdminLogPage['items'][number]>(qk.adminLog(), '/admin/log');
  if (log.isPending) return <LoadingRows rows={3} />;
  if (log.isError) return <ErrorState error={log.error} onRetry={() => log.refetch()} />;
  const items = log.data.pages.flatMap((page) => page.items);
  if (!items.length) return <p className="text-sm text-muted-foreground">{t('admin.logEmpty')}</p>;
  return (
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
          {items.map((entry) => (
            <TableRow key={entry.action_id}>
              <TableCell className="whitespace-nowrap text-muted-foreground">{formatDateTime(entry.at)}</TableCell>
              <TableCell>{entry.admin_email}</TableCell>
              <TableCell>
                {t(`admin.actions.${entry.action}`, { tenant: entry.tenant_id, ...(entry.details as Record<string, string>) })}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Card>
  );
}
