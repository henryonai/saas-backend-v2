import { RotateCcwIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { Button } from '@/shared/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/shared/components/ui/card';
import { Spinner } from '@/shared/components/ui/spinner';
import { toastError } from '@/shared/lib/errors';
import { formatDateTime } from '@/shared/lib/format';
import { useRestoreTenant, useWorkspaces } from '../hooks';

/**
 * Workspaces their owner deleted that aren't purged yet, and ones the product's
 * admins suspended. Their owner can restore a deleted one; for the others, it
 * says why they can't be opened. Nothing when there are none.
 */
export function DeletedWorkspaces() {
  const { t } = useTranslation();
  const workspaces = useWorkspaces();
  const restore = useRestoreTenant();
  const deleted = (workspaces.data ?? []).filter((workspace) => workspace.status && workspace.status !== 'active');
  if (!deleted.length) return null;

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t('workspaces.deleted.title')}</CardTitle>
        <CardDescription>{t('workspaces.deleted.description')}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {deleted.map((workspace) => (
          <div key={workspace.tenant_id} className="flex items-center gap-3">
            <div className="min-w-0 flex-1">
              <p className="truncate font-medium">{workspace.name}</p>
              <p className="text-xs text-muted-foreground">
                {workspace.status === 'suspended'
                  ? t('workspaces.deleted.suspended')
                  : workspace.status === 'deleting' && workspace.delete_after
                    ? t('workspaces.deleted.until', { date: formatDateTime(workspace.delete_after) })
                    : t('workspaces.deleted.purging')}
              </p>
            </div>
            {workspace.role === 'owner' && workspace.status === 'deleting' && (
              <Button
                variant="outline"
                size="sm"
                disabled={restore.isPending}
                onClick={() =>
                  restore.mutate(workspace.tenant_id, {
                    onSuccess: () => toast.success(t('workspaces.deleted.restored', { workspace: workspace.name })),
                    onError: (error) => toastError(error, t),
                  })
                }
              >
                {restore.isPending && restore.variables === workspace.tenant_id ? <Spinner /> : <RotateCcwIcon />}
                {t('workspaces.deleted.restore')}
              </Button>
            )}
          </div>
        ))}
      </CardContent>
    </Card>
  );
}
