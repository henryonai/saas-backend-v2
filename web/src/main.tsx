import { QueryClientProvider } from '@tanstack/react-query';
import { RouterProvider } from '@tanstack/react-router';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import './index.css';
import './shared/i18n';
import { MissingConfig } from './features/shell/missing-config';
import { createAppRouter } from './router';
import { createQueryClient } from './shared/api/query-client';
import { configureAmplify } from './shared/auth/amplify';
import { listenToAuthEvents, loadSession, sessionStore } from './shared/auth/session';
import { WorkspaceScope } from './shared/auth/workspace-scope';
import { TooltipProvider } from './shared/components/ui/tooltip';
import { missingConfig } from './shared/config';
import { followSystemTheme } from './shared/theme/theme';

async function bootstrap(container: HTMLElement) {
  const root = createRoot(container);
  const missing = missingConfig();
  if (missing.length > 0) {
    root.render(<MissingConfig names={missing} />);
    return;
  }

  configureAmplify();
  listenToAuthEvents();
  followSystemTheme();
  try {
    // Amplify keeps the tokens in localStorage: a returning user is still signed in.
    await loadSession();
  } catch {
    // no usable session: start signed out
  }

  const queryClient = createQueryClient();
  const router = createAppRouter();

  sessionStore.subscribe((state, previous) => {
    const workspaceChanged = state.user?.tenantId !== previous.user?.tenantId;
    // Nothing from one workspace (or user) may show in the next (WorkspaceScope
    // then renders the page again, so it loads the new one's data).
    if (workspaceChanged || state.status === 'signed-out') queryClient.clear();
    // Signed out from anywhere (a revoked session, a 401), or moved to another
    // workspace: run the route guards again. Sign-in pages navigate on their own.
    if (previous.status === 'signed-in' && (state.status === 'signed-out' || workspaceChanged)) {
      void router.invalidate();
    }
  });

  root.render(
    <StrictMode>
      <TooltipProvider>
        <QueryClientProvider client={queryClient}>
          <WorkspaceScope>
            <RouterProvider router={router} />
          </WorkspaceScope>
        </QueryClientProvider>
      </TooltipProvider>
    </StrictMode>,
  );
}

const container = document.getElementById('root');
if (!container) throw new Error('#root not found');
void bootstrap(container);
