import { useTranslation } from 'react-i18next';
import { useUser } from '@/shared/auth/session';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/shared/components/ui/card';
import { DeleteAccount } from '../account/components/delete-account';
import { CreateWorkspaceForm } from './components/create-workspace-form';
import { DeletedWorkspaces } from './components/deleted-workspaces';
import { InvitationList } from './components/invitation-list';

/**
 * For a signed-in user without a workspace: join one they were invited to, or
 * create their own. Either way the token gets the new workspace, and the app
 * opens it. Users with workspaces add more from the workspace switcher.
 */
export function WelcomePage() {
  const { t } = useTranslation();
  const { email } = useUser();
  return (
    <div className="mx-auto w-full max-w-2xl space-y-6">
      <div className="space-y-1 text-center">
        <h1 className="text-2xl font-semibold tracking-tight">{t('welcome.title')}</h1>
        <p className="text-sm text-muted-foreground">{t('welcome.description', { email })}</p>
      </div>
      <DeletedWorkspaces />
      <Card>
        <CardHeader>
          <CardTitle>{t('welcome.invitations.title')}</CardTitle>
          <CardDescription>{t('welcome.invitations.description')}</CardDescription>
        </CardHeader>
        <CardContent>
          <InvitationList />
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>{t('welcome.create.title')}</CardTitle>
          <CardDescription>{t('welcome.create.description')}</CardDescription>
        </CardHeader>
        <CardContent className="mt-4">
          <CreateWorkspaceForm />
        </CardContent>
      </Card>
      <DeleteAccount />
    </div>
  );
}
