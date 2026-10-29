import { updatePassword } from 'aws-amplify/auth';
import { BadgeCheckIcon, LogOutIcon } from 'lucide-react';
import { type FormEvent, type ReactNode, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { endSession, useUser } from '@/shared/auth/session';
import { PageHeader } from '@/shared/components/page-header';
import { LanguageSelect, ThemeSelect } from '@/shared/components/preferences';
import { Alert, AlertDescription } from '@/shared/components/ui/alert';
import { Badge } from '@/shared/components/ui/badge';
import { Button } from '@/shared/components/ui/button';
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/shared/components/ui/card';
import { Field, FieldDescription, FieldGroup, FieldLabel } from '@/shared/components/ui/field';
import { Input } from '@/shared/components/ui/input';
import { Spinner } from '@/shared/components/ui/spinner';
import { authErrorMessage } from '../auth/auth-errors';
import { MIN_PASSWORD_LENGTH } from '../auth/sign-up-page';
import { useTenant } from '../workspace/hooks';
import { ExportData } from '../exports/export-data';
import { DeleteAccount } from './components/delete-account';

export function AccountPage() {
  const { t } = useTranslation();
  const user = useUser();
  const tenant = useTenant();

  return (
    <>
      <PageHeader title={t('account.title')} description={t('account.description')} />
      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>{t('account.profile')}</CardTitle>
          </CardHeader>
          <CardContent>
            <dl className="space-y-4">
              <Row label={t('auth.email')}>
                <span className="flex flex-wrap items-center gap-2">
                  {user.email}
                  {user.emailVerified ? (
                    <Badge variant="secondary">
                      <BadgeCheckIcon />
                      {t('account.verified')}
                    </Badge>
                  ) : (
                    <Badge variant="outline">{t('account.unverified')}</Badge>
                  )}
                </span>
              </Row>
              <Row label={t('account.workspace')}>{tenant.data?.name ?? '…'}</Row>
              <Row label={t('team.columns.role')}>{user.role ? t(`roles.${user.role}`) : '—'}</Row>
            </dl>
          </CardContent>
          <CardFooter className="mt-6 border-t pt-6">
            <Button variant="outline" onClick={() => endSession()}>
              <LogOutIcon />
              {t('common.signOut')}
            </Button>
          </CardFooter>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>{t('account.preferences')}</CardTitle>
          </CardHeader>
          <CardContent>
            <FieldGroup>
              <Field orientation="horizontal" className="justify-between">
                <FieldLabel htmlFor="language">{t('prefs.language')}</FieldLabel>
                <LanguageSelect id="language" />
              </Field>
              <Field orientation="horizontal" className="justify-between">
                <FieldLabel htmlFor="theme">{t('prefs.theme')}</FieldLabel>
                <ThemeSelect id="theme" />
              </Field>
            </FieldGroup>
          </CardContent>
        </Card>
        <ChangePassword />
        <ExportData kind="me" />
        <DeleteAccount />
      </div>
    </>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid gap-1 sm:grid-cols-3">
      <dt className="text-sm text-muted-foreground">{label}</dt>
      <dd className="text-sm font-medium sm:col-span-2">{children}</dd>
    </div>
  );
}

function ChangePassword() {
  const { t } = useTranslation();
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (next.length < MIN_PASSWORD_LENGTH) {
      setError(t('auth.errors.invalidPassword'));
      return;
    }
    setError(null);
    setPending(true);
    try {
      await updatePassword({ oldPassword: current, newPassword: next });
      toast.success(t('account.password.changed'));
      setCurrent('');
      setNext('');
    } catch (caught) {
      // Cognito answers NotAuthorizedException for a wrong current password
      setError(caught instanceof Error && caught.name === 'NotAuthorizedException' ? t('account.password.wrongCurrent') : authErrorMessage(caught, t));
    } finally {
      setPending(false);
    }
  }

  return (
    <Card>
      <form onSubmit={submit} noValidate>
        <CardHeader>
          <CardTitle>{t('account.password.title')}</CardTitle>
          <CardDescription>{t('account.password.description')}</CardDescription>
        </CardHeader>
        <CardContent className="mt-4 space-y-4">
          {error && (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
          <FieldGroup>
            <Field>
              <FieldLabel htmlFor="current-password">{t('account.password.current')}</FieldLabel>
              <Input id="current-password" type="password" autoComplete="current-password" value={current} onChange={(event) => setCurrent(event.target.value)} />
            </Field>
            <Field>
              <FieldLabel htmlFor="next-password">{t('auth.newPassword')}</FieldLabel>
              <Input id="next-password" type="password" autoComplete="new-password" value={next} onChange={(event) => setNext(event.target.value)} />
              <FieldDescription>{t('auth.passwordHint', { min: MIN_PASSWORD_LENGTH })}</FieldDescription>
            </Field>
          </FieldGroup>
        </CardContent>
        <CardFooter className="mt-6 justify-end">
          <Button type="submit" disabled={pending || !current || !next}>
            {pending && <Spinner />}
            {t('account.password.submit')}
          </Button>
        </CardFooter>
      </form>
    </Card>
  );
}
