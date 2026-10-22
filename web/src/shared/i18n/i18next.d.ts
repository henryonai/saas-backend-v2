import 'i18next';
import type en from './locales/en.json';

// Translation keys are checked at compile time against the English file.
declare module 'i18next' {
  interface CustomTypeOptions {
    resources: { translation: typeof en };
  }
}
