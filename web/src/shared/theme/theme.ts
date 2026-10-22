import { useStore } from 'zustand';
import { createStore } from 'zustand/vanilla';

export type ThemeMode = 'light' | 'dark' | 'system';
export const THEME_MODES: readonly ThemeMode[] = ['light', 'dark', 'system'];

// index.html reads the same key before the first paint: keep them in sync.
const STORAGE_KEY = 'saas.theme';

function readStored(): ThemeMode {
  try {
    const value = localStorage.getItem(STORAGE_KEY);
    return value === 'light' || value === 'dark' ? value : 'system';
  } catch {
    return 'system';
  }
}

function systemQuery(): MediaQueryList | null {
  return typeof window.matchMedia === 'function' ? window.matchMedia('(prefers-color-scheme: dark)') : null;
}

function resolve(mode: ThemeMode): 'light' | 'dark' {
  if (mode !== 'system') return mode;
  return systemQuery()?.matches ? 'dark' : 'light';
}

function apply(resolved: 'light' | 'dark'): void {
  const root = document.documentElement;
  root.classList.toggle('dark', resolved === 'dark');
  root.style.colorScheme = resolved;
}

const initial = readStored();

export const themeStore = createStore<{ mode: ThemeMode; resolved: 'light' | 'dark' }>()(() => ({
  mode: initial,
  resolved: resolve(initial),
}));

export function setThemeMode(mode: ThemeMode): void {
  try {
    localStorage.setItem(STORAGE_KEY, mode);
  } catch {
    // storage disabled: the choice lasts until the page reloads
  }
  const resolved = resolve(mode);
  apply(resolved);
  themeStore.setState({ mode, resolved });
}

/** While the mode is "system", follow the operating system's setting as it changes. */
export function followSystemTheme(): () => void {
  const query = systemQuery();
  if (!query) return () => {};
  const onChange = () => {
    if (themeStore.getState().mode !== 'system') return;
    const resolved = resolve('system');
    apply(resolved);
    themeStore.setState({ resolved });
  };
  query.addEventListener('change', onChange);
  return () => query.removeEventListener('change', onChange);
}

export function useTheme() {
  return useStore(themeStore);
}
