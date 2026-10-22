import { Link, useNavigate } from '@tanstack/react-router';
import { confirmResetPassword, resetPassword } from 'aws-amplify/auth';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { Field, FieldDescription, FieldLabel } from '@/shared/components/ui/field';
import { Input } from '@/shared/components/ui/input';
import { AuthForm } from './components/auth-form';
import { authErrorMessage } from './auth-errors';
import { CodeInput } from './components/code-input';
import { MIN_PASSWORD_LENGTH } from './sign-up-page';

export function ForgotPasswordPage({ email: initialEmail }: { email?: string }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [step, setStep] = useState<'request' | 'reset'>('request');
  const [email, setEmail] = useState(initialEmail ?? '');
  const [code, setCode] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function run(action: () => Promise<void>) {
    setError(null);
    setPending(true);
    try {
      await action();
    } catch (caught) {
      setError(authErrorMessage(caught, t));
    } finally {
      setPending(false);
    }
  }

  const backToSignIn = (
    <Link to="/sign-in" search={{ email: email.trim() || undefined }} className="underline-offset-4 hover:underline">
      {t('auth.backToSignIn')}
    </Link>
  );

  if (step === 'request') {
    return (
      <AuthForm
        title={t('auth.forgot.title')}
        description={t('auth.forgot.description')}
        error={error}
        submitLabel={t('auth.forgot.sendCode')}
        pending={pending}
        onSubmit={() =>
          run(async () => {
            // The same answer whether or not the email has an account (PreventUserExistenceErrors).
            await resetPassword({ username: email.trim() });
            setStep('reset');
          })
        }
        footer={backToSignIn}
      >
        <Field>
          <FieldLabel htmlFor="email">{t('auth.email')}</FieldLabel>
          <Input id="email" type="email" autoComplete="email" required value={email} onChange={(event) => setEmail(event.target.value)} />
        </Field>
      </AuthForm>
    );
  }

  return (
    <AuthForm
      title={t('auth.forgot.resetTitle')}
      description={t('auth.forgot.resetDescription', { email: email.trim() })}
      error={error}
      submitLabel={t('auth.forgot.submit')}
      pending={pending}
      onSubmit={() =>
        run(async () => {
          if (password.length < MIN_PASSWORD_LENGTH) throw Object.assign(new Error(), { name: 'InvalidPasswordException' });
          await confirmResetPassword({ username: email.trim(), confirmationCode: code, newPassword: password });
          toast.success(t('auth.forgot.done'));
          await navigate({ to: '/sign-in', search: { email: email.trim() } });
        })
      }
      footer={backToSignIn}
    >
      <Field className="items-center">
        <FieldLabel className="sr-only">{t('auth.code')}</FieldLabel>
        <CodeInput value={code} onChange={setCode} />
      </Field>
      <Field>
        <FieldLabel htmlFor="new-password">{t('auth.newPassword')}</FieldLabel>
        <Input
          id="new-password"
          type="password"
          autoComplete="new-password"
          required
          value={password}
          onChange={(event) => setPassword(event.target.value)}
        />
        <FieldDescription>{t('auth.passwordHint', { min: MIN_PASSWORD_LENGTH })}</FieldDescription>
      </Field>
    </AuthForm>
  );
}
