import { QueryClient } from '@tanstack/react-query';
import { ApiError } from './client';

/** Worth another try: no answer (0), a busy item (503, "safe to retry"), throttling (429), a server fault. */
function isTransient(error: unknown): boolean {
  if (!(error instanceof ApiError)) return false;
  return error.status === 0 || error.status === 429 || error.status >= 500;
}

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: (failureCount, error) => isTransient(error) && failureCount < 2,
        staleTime: 30_000,
      },
      mutations: {
        // a 503 means the item was busy and nothing was saved
        retry: (failureCount, error) => error instanceof ApiError && error.status === 503 && failureCount < 2,
      },
    },
  });
}
