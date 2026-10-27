import { PlusIcon } from 'lucide-react';
import { type FormEvent, useId, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { Alert, AlertDescription } from '@/shared/components/ui/alert';
import { Button } from '@/shared/components/ui/button';
import { Field, FieldError, FieldLabel } from '@/shared/components/ui/field';
import { Input } from '@/shared/components/ui/input';
import { Spinner } from '@/shared/components/ui/spinner';
import { errorMessage } from '@/shared/lib/errors';
import { useCreateTenant, useGoToWorkspace } from '../hooks';

/** Creates a workspace owned by the user, then opens it. */
export function CreateWorkspaceForm({ onCreated }: { onCreated?: () => void }) {
  const { t } = useTranslation();
  const id = useId();
  const create = useCreateTenant();
  const goTo = useGoToWorkspace();
  const [name, setName] = useState('');
  const [nameError, setNameError] = useState<string | null>(null);
  const [apiError, setApiError] = useState<string | null>(null);
  // Creating, then opening it: one submission until both are done, so a second click can't create another.
  const [submitting, setSubmitting] = useState(false);
  // One Idempotency-Key per workspace: submitting the same name again (after a
  // timeout, or when opening it failed) sends the same key, so it isn't created twice.
  const attempt = useRef<{ name: string; key: string } | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (submitting) return;
    const trimmed = name.trim();
    if (!trimmed) {
      setNameError(t('welcome.create.nameRequired'));
      return;
    }
    setApiError(null);
    setSubmitting(true);
    try {
      if (attempt.current?.name !== trimmed) attempt.current = { name: trimmed, key: crypto.randomUUID() };
      const tenant = await create.mutateAsync({ body: { name: trimmed }, idempotencyKey: attempt.current.key });
      attempt.current = null;   // created: another workspace, even of the same name, is a new one
      toast.success(t('welcome.create.created', { workspace: trimmed }));
      onCreated?.();
      if (!(await goTo(tenant.tenant_id))) toast.error(t('workspaces.openFailed'));
    } catch (caught) {
      setApiError(errorMessage(caught, t));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={submit} noValidate className="space-y-4">
      {apiError && (
        <Alert variant="destructive">
          <AlertDescription>{apiError}</AlertDescription>
        </Alert>
      )}
      <Field data-invalid={!!nameError}>
        <FieldLabel htmlFor={`${id}-name`}>{t('welcome.create.name')}</FieldLabel>
        <Input
          id={`${id}-name`}
          maxLength={100}
          placeholder={t('welcome.create.namePlaceholder')}
          value={name}
          aria-invalid={!!nameError}
          onChange={(event) => {
            setName(event.target.value);
            setNameError(null);
          }}
        />
        {nameError && <FieldError>{nameError}</FieldError>}
      </Field>
      <div className="flex justify-end">
        <Button type="submit" disabled={submitting}>
          {submitting ? <Spinner /> : <PlusIcon />}
          {t('welcome.create.submit')}
        </Button>
      </div>
    </form>
  );
}
