import type { FormEvent, ReactNode } from 'react';
import { Alert, AlertDescription } from '@/shared/components/ui/alert';
import { Button } from '@/shared/components/ui/button';
import { CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/shared/components/ui/card';
import { FieldGroup } from '@/shared/components/ui/field';
import { Spinner } from '@/shared/components/ui/spinner';

interface AuthFormProps {
  title: ReactNode;
  description?: ReactNode;
  notice?: ReactNode;
  error?: string | null;
  submitLabel: ReactNode;
  pending: boolean;
  onSubmit: () => Promise<void> | void;
  children: ReactNode;
  footer?: ReactNode;
}

/** The card body every sign-in and sign-up step shares (the card itself is in the _public layout). */
export function AuthForm({ title, description, notice, error, submitLabel, pending, onSubmit, children, footer }: AuthFormProps) {
  function submit(event: FormEvent) {
    event.preventDefault();
    if (!pending) void onSubmit();
  }

  return (
    <form onSubmit={submit} noValidate>
      <CardHeader>
        <CardTitle className="text-xl">{title}</CardTitle>
        {description && <CardDescription>{description}</CardDescription>}
      </CardHeader>
      <CardContent className="mt-6 space-y-4">
        {notice}
        {error && (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}
        <FieldGroup>{children}</FieldGroup>
        <Button type="submit" className="w-full" size="lg" disabled={pending}>
          {pending && <Spinner />}
          {submitLabel}
        </Button>
      </CardContent>
      {footer && <CardFooter className="mt-4 flex-col gap-2 text-sm text-muted-foreground">{footer}</CardFooter>}
    </form>
  );
}
