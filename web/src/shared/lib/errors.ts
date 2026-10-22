import type { TFunction } from 'i18next';
import { toast } from 'sonner';
import { ApiError } from '../api/client';

/**
 * What to tell the user about a failed call. Business errors (400, 402, 403,
 * 404, 409, 429) carry the backend's own explanation, in English; the rest get
 * a translated one.
 */
export function errorMessage(error: unknown, t: TFunction): string {
  if (error instanceof ApiError) {
    if (error.status === 0) return t('errors.network');
    if (error.status === 401) return t('errors.signedOut');
    if (error.status === 503) return t('errors.busy');
    if (error.status >= 500) return t('errors.server');
    return error.message;
  }
  return t('errors.generic');
}

export function isPlanLimit(error: unknown): boolean {
  return error instanceof ApiError && error.status === 402;
}

export function toastError(error: unknown, t: TFunction): void {
  // a 401 already signed the user out, and the sign-in page says why
  if (error instanceof ApiError && error.status === 401) return;
  toast.error(errorMessage(error, t));
}
