import { Outlet } from '@tanstack/react-router';
import { Brand } from '@/shared/components/brand';
import { LanguageSelect, ThemeSelect } from '@/shared/components/preferences';
import { Card } from '@/shared/components/ui/card';

/** Sign-in, sign-up and their steps: one card in the middle of the page. */
export function PublicLayout() {
  return (
    <div className="flex min-h-svh flex-col items-center justify-center gap-6 bg-muted/40 p-4">
      <Brand />
      <Card className="w-full max-w-sm">
        <Outlet />
      </Card>
      <div className="flex gap-2">
        <LanguageSelect />
        <ThemeSelect />
      </div>
    </div>
  );
}
