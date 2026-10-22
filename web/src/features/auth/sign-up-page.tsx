import { Link, useNavigate } from '@tanstack/react-router';
import { signUp } from 'aws-amplify/auth';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Field, FieldDescription, FieldLabel } from '@/shared/components/ui/field';
import { Input } from '@/shared/components/ui/input';
import { AuthForm } from './components/auth-form';
import { authErrorMessage } from './auth-errors';

/** The user pool's PasswordPolicy in template.yaml. */
export const MIN_PASSWORD_LENGTH = 12;

export function SignUpPage({ redirect }: { redirect?: string }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function submit() {
    if (password.length < MIN_PASSWORD_LENGTH) {
      setError(t('auth.errors.invalidPassword'));
      return;
    }
    setError(null);
    setPending(true);
    try {
      const username = email.trim();
      // autoSignIn: entering the emailed code then signs the user in, with no second password prompt
      await signUp({ username, password, options: { userAttributes: { email: username }, autoSignIn: true } });
      await navigate({ to: '/confirm', search: { email: username, redirect } });
    } catch (caught) {
      setError(authErrorMessage(caught, t));
    } finally {
      setPending(false);
    }
  }

  return (
    <AuthForm
      title={t('auth.signUp.title')}
      description={t('auth.signUp.description')}
      error={error}
      submitLabel={t('auth.signUp.submit')}
      pending={pending}
      onSubmit={submit}
      footer={
        <p>
          {t('auth.signUp.haveAccount')}{' '}
          <Link to="/sign-in" search={{ redirect }} className="font-medium text-foreground underline-offset-4 hover:underline">
            {t('auth.signUp.signInLink')}
          </Link>
        </p>
      }
    >
      <Field>
        <FieldLabel htmlFor="email">{t('auth.email')}</FieldLabel>
        <Input id="email" type="email" autoComplete="email" required value={email} onChange={(event) => setEmail(event.target.value)} />
        <FieldDescription>{t('auth.signUp.emailHint')}</FieldDescription>
      </Field>
      <Field>
        <FieldLabel htmlFor="password">{t('auth.password')}</FieldLabel>
        <Input
          id="password"
          type="password"
          autoComplete="new-password"
          required
          minLength={MIN_PASSWORD_LENGTH}
          value={password}
          onChange={(event) => setPassword(event.target.value)}
        />
        <FieldDescription>{t('auth.passwordHint', { min: MIN_PASSWORD_LENGTH })}</FieldDescription>
      </Field>
    </AuthForm>
  );
}
