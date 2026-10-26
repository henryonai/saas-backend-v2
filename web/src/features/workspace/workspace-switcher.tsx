import { CheckIcon, ChevronsUpDownIcon, MailIcon, PlusIcon } from 'lucide-react';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useUser } from '@/shared/auth/session';
import { BrandMark } from '@/shared/components/brand';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/shared/components/ui/dialog';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/shared/components/ui/dropdown-menu';
import { SidebarMenu, SidebarMenuButton, SidebarMenuItem } from '@/shared/components/ui/sidebar';
import { Skeleton } from '@/shared/components/ui/skeleton';
import { getPlan } from '@/shared/lib/plans';
import { CreateWorkspaceForm } from './components/create-workspace-form';
import { InvitationList } from './components/invitation-list';
import { useGoToWorkspace, useReceivedInvitations, useTenant, useWorkspaces } from './hooks';

/**
 * The sidebar's header: the workspace this tab works in, and the user's other
 * workspaces to switch to. Switching changes this tab only. Also creates a
 * workspace, or joins one the user was invited to, without leaving any.
 */
export function WorkspaceSwitcher() {
  const { t } = useTranslation();
  const { tenantId, plan } = useUser();
  const tenant = useTenant();
  const workspaces = useWorkspaces();
  const invitations = useReceivedInvitations();
  const goTo = useGoToWorkspace();
  const [dialog, setDialog] = useState<'create' | 'join' | null>(null);
  const pending = invitations.data?.length ?? 0;

  return (
    <SidebarMenu>
      <SidebarMenuItem>
        {/* not modal: a dialog opened from an item would otherwise leave the page unclickable */}
        <DropdownMenu modal={false}>
          <DropdownMenuTrigger asChild>
            <SidebarMenuButton size="lg" className="data-[state=open]:bg-sidebar-accent" aria-label={t('workspaces.switch')}>
              <BrandMark />
              <span className="grid flex-1 text-left text-sm leading-tight">
                <span className="truncate font-semibold">{tenant.data?.name ?? <Skeleton className="h-4 w-24" />}</span>
                <span className="truncate text-xs text-muted-foreground">{t('nav.plan', { plan: getPlan(plan).name })}</span>
              </span>
              <ChevronsUpDownIcon className="ml-auto size-4" />
            </SidebarMenuButton>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" className="w-(--radix-dropdown-menu-trigger-width) min-w-56">
            <DropdownMenuLabel className="text-xs text-muted-foreground">{t('workspaces.title')}</DropdownMenuLabel>
            {workspaces.isPending ? (
              <DropdownMenuItem disabled>
                <Skeleton className="h-4 w-32" />
              </DropdownMenuItem>
            ) : (
              (workspaces.data ?? []).map((workspace) => (
                <DropdownMenuItem
                  key={workspace.tenant_id}
                  onSelect={() => {
                    if (workspace.tenant_id !== tenantId) void goTo(workspace.tenant_id);
                  }}
                >
                  <span className="truncate">{workspace.name}</span>
                  <span className="ml-auto text-xs text-muted-foreground">{t(`roles.${workspace.role}`)}</span>
                  <CheckIcon className={workspace.tenant_id === tenantId ? '' : 'invisible'} aria-hidden />
                </DropdownMenuItem>
              ))
            )}
            <DropdownMenuSeparator />
            <DropdownMenuItem onSelect={() => setDialog('create')}>
              <PlusIcon />
              {t('workspaces.create')}
            </DropdownMenuItem>
            {pending > 0 && (
              <DropdownMenuItem onSelect={() => setDialog('join')}>
                <MailIcon />
                {t('workspaces.invitations', { count: pending })}
              </DropdownMenuItem>
            )}
          </DropdownMenuContent>
        </DropdownMenu>

        <Dialog open={dialog === 'create'} onOpenChange={(open) => !open && setDialog(null)}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>{t('welcome.create.title')}</DialogTitle>
              <DialogDescription>{t('workspaces.createDescription')}</DialogDescription>
            </DialogHeader>
            <CreateWorkspaceForm onCreated={() => setDialog(null)} />
          </DialogContent>
        </Dialog>
        <Dialog open={dialog === 'join'} onOpenChange={(open) => !open && setDialog(null)}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>{t('welcome.invitations.title')}</DialogTitle>
              <DialogDescription>{t('workspaces.invitationsDescription')}</DialogDescription>
            </DialogHeader>
            <InvitationList onJoined={() => setDialog(null)} />
          </DialogContent>
        </Dialog>
      </SidebarMenuItem>
    </SidebarMenu>
  );
}
