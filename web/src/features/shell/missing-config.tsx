import { useTranslation } from 'react-i18next';

/** Shown instead of the app until .env.local names the stack to talk to. */
export function MissingConfig({ names }: { names: string[] }) {
  const { t } = useTranslation();
  return (
    <div className="mx-auto max-w-xl space-y-4 p-8">
      <h1 className="text-xl font-semibold">{t('config.title')}</h1>
      <p className="text-sm text-muted-foreground">{t('config.description')}</p>
      <ul className="list-inside list-disc font-mono text-sm">
        {names.map((name) => (
          <li key={name}>{name}</li>
        ))}
      </ul>
      <pre className="rounded-lg bg-muted p-3 font-mono text-xs">cd web && ./scripts/env-from-stack.sh saas-dev</pre>
    </div>
  );
}
