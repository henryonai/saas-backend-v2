import { Link, useNavigate, useRouter } from '@tanstack/react-router';
import { autoSignIn, confirmSignUp, resendSignUpCode } from 'aws-amplify/auth';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { Button } from '@/shared/components/ui/button';
import { Field, FieldLabel } from '@/shared/components/ui/field';
import { Input } from '@/shared/components/ui/input';
import { loadSession } from '@/shared/auth/session';
import { safeRedirect } from '@/shared/lib/safe-redirect';
import { AuthForm } from './auth-form';
import { authErrorMessage } from './auth-errors';
import { CodeInput } from './code-input';

export function ConfirmPage({ email: initialEmail, redirect }: { email?: string; redirect?: string }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const router = useRouter();
  const [email, setEmail] = useState(initialEmail ?? '');
  const [code, setCode] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function signInAfterConfirming(): Promise<boolean> {
    try {
      const { nextStep } = await autoSignIn();
      if (nextStep.signInStep !== 'DONE') return false;
      await loadSession();
      return true;
    } catch {
      return false; // e.g. the page was reloaded since sign-up: sign in by hand
    }
  }

  async function confirm(confirmationCode: string) {
    if (pending || confirmationCode.length !== 6) return;
    setError(null);
    setPending(true);
    try {
      const username = email.trim();
      const { nextStep } = await confirmSignUp({ username, confirmationCode });
      if (nextStep.signUpStep === 'COMPLETE_AUTO_SIGN_IN' && (await signInAfterConfirming())) {
        router.history.push(safeRedirect(redirect));
        return;
      }
      toast.success(t('auth.confirm.confirmed'));
      await navigate({ to: '/sign-in', search: { email: username, redirect } });
    } catch (caught) {
      setError(authErrorMessage(caught, t));
    } finally {
      setPending(false);
    }
  }

  async function resend() {
    setError(null);
    try {
      await resendSignUpCode({ username: email.trim() });
      toast.success(t('auth.confirm.resent'));
    } catch (caught) {
      setError(authErrorMessage(caught, t));
    }
  }

  return (
    <AuthForm
      title={t('auth.confirm.title')}
      description={initialEmail ? t('auth.confirm.description', { email: initialEmail }) : t('auth.confirm.descriptionNoEmail')}
      error={error}
      submitLabel={t('auth.confirm.submit')}
      pending={pending}
      onSubmit={() => confirm(code)}
      footer={
        <>
          <Button variant="link" size="sm" onClick={resend} disabled={!email.trim()}>
            {t('auth.confirm.resend')}
          </Button>
          <Link to="/sign-in" search={{ redirect }} className="underline-offset-4 hover:underline">
            {t('auth.backToSignIn')}
          </Link>
        </>
      }
    >
      {!initialEmail && (
        <Field>
          <FieldLabel htmlFor="email">{t('auth.email')}</FieldLabel>
          <Input id="email" type="email" autoComplete="email" value={email} onChange={(event) => setEmail(event.target.value)} />
        </Field>
      )}
      <Field className="items-center">
        <FieldLabel className="sr-only">{t('auth.code')}</FieldLabel>
        <CodeInput value={code} onChange={setCode} onComplete={confirm} />
      </Field>
    </AuthForm>
  );
}
