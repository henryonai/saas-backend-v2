import { useTranslation } from 'react-i18next';
import { canManageMembers } from '@/shared/auth/claims';
import { useUser } from '@/shared/auth/session';
import { PageHeader } from '@/shared/components/page-header';
import { Badge } from '@/shared/components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/shared/components/ui/tabs';
import { formatNumber } from '@/shared/lib/format';
import { getPlan } from '@/shared/lib/plans';
import { useTenant } from '../workspace/api';
import { useSentInvitations } from './api';
import { InvitationsTab } from './invitations-tab';
import { InviteDialog } from './invite-dialog';
import { MembersTab } from './members-tab';

export function TeamPage() {
  const { t } = useTranslation();
  const { role } = useUser();
  const tenant = useTenant();
  const invitations = useSentInvitations();
  const maxUsers = tenant.data ? getPlan(tenant.data.plan).max_users : undefined;

  return (
    <>
      <PageHeader
        title={t('team.title')}
        description={
          tenant.data
            ? maxUsers === null
              ? t('team.seatsUnlimited', { used: formatNumber(tenant.data.seats_used) })
              : t('team.seats', { used: formatNumber(tenant.data.seats_used), max: formatNumber(maxUsers ?? 0) })
            : t('team.description')
        }
        actions={canManageMembers(role) ? <InviteDialog /> : undefined}
      />
      <Tabs defaultValue="members">
        <TabsList>
          <TabsTrigger value="members">{t('team.tabs.members')}</TabsTrigger>
          <TabsTrigger value="invitations">
            {t('team.tabs.invitations')}
            {!!invitations.data?.length && <Badge variant="secondary">{invitations.data.length}</Badge>}
          </TabsTrigger>
        </TabsList>
        <TabsContent value="members" className="mt-4">
          <MembersTab />
        </TabsContent>
        <TabsContent value="invitations" className="mt-4">
          <InvitationsTab />
        </TabsContent>
      </Tabs>
    </>
  );
}
