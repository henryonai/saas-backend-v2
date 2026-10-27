import '@testing-library/jest-dom/vitest';
import { cleanup, configure } from '@testing-library/react';
import { afterEach } from 'vitest';

// A page's first test in a file loads its route's code, which, with every
// file running in parallel, can take longer than findBy's default second.
configure({ asyncUtilTimeout: 5000 });

// What jsdom lacks and the app uses: scrolling (the router restores it), media
// queries (the sidebar's mobile check, the theme) and ResizeObserver (Radix).
window.scrollTo = () => {};
window.matchMedia ??= (query: string) =>
  ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  }) as MediaQueryList;
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
};

afterEach(() => {
  cleanup();
  localStorage.clear();
  sessionStorage.clear();
});
