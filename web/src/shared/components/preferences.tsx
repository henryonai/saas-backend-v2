import { LanguagesIcon, MonitorIcon, MoonIcon, SunIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import {
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
} from '@/shared/components/ui/dropdown-menu';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/shared/components/ui/select';
import { currentLanguage, type Language, LANGUAGES, setLanguage } from '@/shared/i18n';
import { setThemeMode, THEME_MODES, type ThemeMode, useTheme } from '@/shared/theme/theme';

const THEME_ICONS = { light: SunIcon, dark: MoonIcon, system: MonitorIcon } as const;

export function LanguageSelect({ id }: { id?: string }) {
  const { t } = useTranslation();
  return (
    <Select value={currentLanguage()} onValueChange={(value) => setLanguage(value as Language)}>
      <SelectTrigger id={id} aria-label={t('prefs.language')} className="w-40">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {LANGUAGES.map((language) => (
          <SelectItem key={language} value={language}>
            {t(`prefs.languages.${language}`)}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

export function ThemeSelect({ id }: { id?: string }) {
  const { t } = useTranslation();
  const { mode } = useTheme();
  return (
    <Select value={mode} onValueChange={(value) => setThemeMode(value as ThemeMode)}>
      <SelectTrigger id={id} aria-label={t('prefs.theme')} className="w-40">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {THEME_MODES.map((theme) => (
          <SelectItem key={theme} value={theme}>
            {t(`prefs.themes.${theme}`)}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

/** Language and theme as submenus of a dropdown (the user menu). */
export function PreferencesMenuItems() {
  const { t } = useTranslation();
  const { mode } = useTheme();
  const ThemeIcon = THEME_ICONS[mode];
  return (
    <>
      <DropdownMenuSub>
        <DropdownMenuSubTrigger>
          <LanguagesIcon />
          {t('prefs.language')}
        </DropdownMenuSubTrigger>
        <DropdownMenuSubContent>
          <DropdownMenuRadioGroup value={currentLanguage()} onValueChange={(value) => setLanguage(value as Language)}>
            {LANGUAGES.map((language) => (
              <DropdownMenuRadioItem key={language} value={language}>
                {t(`prefs.languages.${language}`)}
              </DropdownMenuRadioItem>
            ))}
          </DropdownMenuRadioGroup>
        </DropdownMenuSubContent>
      </DropdownMenuSub>
      <DropdownMenuSub>
        <DropdownMenuSubTrigger>
          <ThemeIcon />
          {t('prefs.theme')}
        </DropdownMenuSubTrigger>
        <DropdownMenuSubContent>
          <DropdownMenuRadioGroup value={mode} onValueChange={(value) => setThemeMode(value as ThemeMode)}>
            {THEME_MODES.map((theme) => (
              <DropdownMenuRadioItem key={theme} value={theme}>
                {t(`prefs.themes.${theme}`)}
              </DropdownMenuRadioItem>
            ))}
          </DropdownMenuRadioGroup>
        </DropdownMenuSubContent>
      </DropdownMenuSub>
    </>
  );
}
