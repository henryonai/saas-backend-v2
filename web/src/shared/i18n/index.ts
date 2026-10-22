import i18next from 'i18next';
import { initReactI18next } from 'react-i18next';
import en from './locales/en.json';
import vi from './locales/vi.json';

export type Language = 'en' | 'vi';
export const LANGUAGES: readonly Language[] = ['en', 'vi'];

// index.html reads the same key before the first paint: keep them in sync.
const STORAGE_KEY = 'saas.lang';

function initialLanguage(): Language {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === 'en' || stored === 'vi') return stored;
  } catch {
    // storage disabled
  }
  return navigator.language.toLowerCase().startsWith('vi') ? 'vi' : 'en';
}

// Initialized on import, so the first render already has its strings.
void i18next.use(initReactI18next).init({
  resources: { en: { translation: en }, vi: { translation: vi } },
  lng: initialLanguage(),
  fallbackLng: 'en',
  interpolation: { escapeValue: false }, // React escapes already
});

document.documentElement.lang = i18next.language;
i18next.on('languageChanged', (language) => {
  document.documentElement.lang = language;
});

export function currentLanguage(): Language {
  return i18next.language === 'vi' ? 'vi' : 'en';
}

export function setLanguage(language: Language): void {
  try {
    localStorage.setItem(STORAGE_KEY, language);
  } catch {
    // storage disabled: the choice lasts until the page reloads
  }
  void i18next.changeLanguage(language);
}

export { i18next as i18n };
