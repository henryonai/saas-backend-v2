import { Outlet } from '@tanstack/react-router';
import { LogOutIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { endSession } from '@/shared/auth/session';
import { Brand } from '@/shared/components/brand';
import { LanguageSelect, ThemeSelect } from '@/shared/components/preferences';
import { Button } from '@/shared/components/ui/button';

/** Signed in, no workspace yet: create one or accept an invitation. */
export function OnboardingLayout() {
  const { t } = useTranslation();
  return (
    <div className="flex min-h-svh flex-col bg-muted/40">
      <header className="flex h-14 items-center gap-2 border-b bg-background px-4">
        <Brand />
        <div className="ml-auto flex items-center gap-2">
          <div className="hidden gap-2 sm:flex">
            <LanguageSelect />
            <ThemeSelect />
          </div>
          <Button variant="ghost" onClick={() => void endSession()}>
            <LogOutIcon />
            {t('common.signOut')}
          </Button>
        </div>
      </header>
      <main className="flex-1 px-4 py-10">
        <Outlet />
      </main>
    </div>
  );
}
