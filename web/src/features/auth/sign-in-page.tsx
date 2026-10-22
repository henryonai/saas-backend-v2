import { Link, useNavigate, useRouter } from '@tanstack/react-router';
import { confirmSignIn, resendSignUpCode, signIn, type SignInOutput } from 'aws-amplify/auth';
import { InfoIcon } from 'lucide-react';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Alert, AlertDescription } from '@/shared/components/ui/alert';
import { Field, FieldLabel } from '@/shared/components/ui/field';
import { Input } from '@/shared/components/ui/input';
import { loadSession, useSession } from '@/shared/auth/session';
import { safeRedirect } from '@/shared/lib/safe-redirect';
import { AuthForm } from './components/auth-form';
import { authErrorMessage } from './auth-errors';
import { CodeInput } from './components/code-input';

export function SignInPage({ redirect, email: initialEmail }: { redirect?: string; email?: string }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const router = useRouter();
  const { reason } = useSession();
  const [email, setEmail] = useState(initialEmail ?? '');
  const [password, setPassword] = useState('');
  const [totp, setTotp] = useState<string | null>(null); // set while Cognito asks for an authenticator code
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function next(result: SignInOutput) {
    switch (result.nextStep.signInStep) {
      case 'DONE':
        await loadSession();
        router.history.push(safeRedirect(redirect));
        return;
      case 'CONFIRM_SIGN_UP':
        // signed up but never entered the code: send a fresh one
        await resendSignUpCode({ username: email.trim() });
        await navigate({ to: '/confirm', search: { email: email.trim(), redirect } });
        return;
      case 'RESET_PASSWORD':
        await navigate({ to: '/forgot-password', search: { email: email.trim() } });
        return;
      case 'CONFIRM_SIGN_IN_WITH_TOTP_CODE':
        setTotp('');
        return;
      default:
        setError(t('auth.errors.unsupportedStep'));
    }
  }

  async function run(action: () => Promise<SignInOutput>) {
    setError(null);
    setPending(true);
    try {
      await next(await action());
    } catch (caught) {
      setError(authErrorMessage(caught, t));
    } finally {
      setPending(false);
    }
  }

  if (totp !== null) {
    return (
      <AuthForm
        title={t('auth.signIn.totpTitle')}
        description={t('auth.signIn.totpDescription')}
        error={error}
        submitLabel={t('auth.signIn.submit')}
        pending={pending}
        onSubmit={() => run(() => confirmSignIn({ challengeResponse: totp }))}
      >
        <Field className="items-center">
          <CodeInput value={totp} onChange={setTotp} onComplete={(code) => run(() => confirmSignIn({ challengeResponse: code }))} />
        </Field>
      </AuthForm>
    );
  }

  return (
    <AuthForm
      title={t('auth.signIn.title')}
      description={t('auth.signIn.description')}
      notice={
        reason ? (
          <Alert>
            <InfoIcon />
            <AlertDescription>{t(`auth.signedOut.${reason}`)}</AlertDescription>
          </Alert>
        ) : null
      }
      error={error}
      submitLabel={t('auth.signIn.submit')}
      pending={pending}
      onSubmit={() => run(() => signIn({ username: email.trim(), password }))}
      footer={
        <>
          <p>
            {t('auth.signIn.noAccount')}{' '}
            <Link to="/sign-up" search={{ redirect }} className="font-medium text-foreground underline-offset-4 hover:underline">
              {t('auth.signIn.signUpLink')}
            </Link>
          </p>
          <p className="text-center text-xs">{t('auth.signIn.invitedHint')}</p>
        </>
      }
    >
      <Field>
        <FieldLabel htmlFor="email">{t('auth.email')}</FieldLabel>
        <Input id="email" type="email" autoComplete="email" required value={email} onChange={(event) => setEmail(event.target.value)} />
      </Field>
      <Field>
        <div className="flex items-center justify-between">
          <FieldLabel htmlFor="password">{t('auth.password')}</FieldLabel>
          <Link
            to="/forgot-password"
            search={{ email: email.trim() || undefined }}
            className="text-sm text-muted-foreground underline-offset-4 hover:underline"
          >
            {t('auth.signIn.forgot')}
          </Link>
        </div>
        <Input
          id="password"
          type="password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(event) => setPassword(event.target.value)}
        />
      </Field>
    </AuthForm>
  );
}
