import { PlusIcon } from 'lucide-react';
import { type FormEvent, useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
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
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) {
      setError(t('welcome.create.nameRequired'));
      return;
    }
    setError(null);
    try {
      const tenant = await create.mutateAsync({ name: trimmed });
      toast.success(t('welcome.create.created', { workspace: trimmed }));
      onCreated?.();
      await goTo(tenant.tenant_id);
    } catch (caught) {
      setError(errorMessage(caught, t));
    }
  }

  return (
    <form onSubmit={submit} noValidate className="space-y-4">
      <Field data-invalid={!!error}>
        <FieldLabel htmlFor={`${id}-name`}>{t('welcome.create.name')}</FieldLabel>
        <Input
          id={`${id}-name`}
          maxLength={100}
          placeholder={t('welcome.create.namePlaceholder')}
          value={name}
          aria-invalid={!!error}
          onChange={(event) => setName(event.target.value)}
        />
        {error && <FieldError>{error}</FieldError>}
      </Field>
      <div className="flex justify-end">
        <Button type="submit" disabled={create.isPending}>
          {create.isPending ? <Spinner /> : <PlusIcon />}
          {t('welcome.create.submit')}
        </Button>
      </div>
    </form>
  );
}
