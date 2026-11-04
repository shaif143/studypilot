/**
 * The whole application, as one component.
 *
 * Both hosts render this. The website wraps it in a `BrowserRouter`, the macOS
 * app in a `HashRouter`; everything else — screens, state, data access, design
 * — is this single shared implementation, which is what keeps the two from
 * drifting apart.
 */
import type { Platform } from '@studypilot/shared';
import { Button, Spinner } from '@studypilot/ui';
import { QueryCache, QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Component, type ErrorInfo, type ReactNode, useState } from 'react';
import { Navigate, Route, Routes, useLocation } from 'react-router-dom';

import { AppShell } from './components/AppShell';
import { AppProvider } from './context';
import { useAuthStatus } from './hooks/queries';
import { AnalyticsScreen } from './screens/AnalyticsScreen';
import { AssessmentScreen } from './screens/AssessmentScreen';
import { CoursesScreen } from './screens/CoursesScreen';
import { DashboardScreen } from './screens/DashboardScreen';
import { FlashcardsScreen } from './screens/FlashcardsScreen';
import { LibraryScreen } from './screens/LibraryScreen';
import { NotesScreen } from './screens/NotesScreen';
import { ReaderScreen } from './screens/ReaderScreen';
import { SettingsScreen } from './screens/SettingsScreen';
import { SignInScreen } from './screens/SignInScreen';
import { TutorScreen } from './screens/TutorScreen';

function createQueryClient(): QueryClient {
  return new QueryClient({
    queryCache: new QueryCache(),
    defaultOptions: {
      queries: {
        // A local backend is fast; refetching on every window focus would make
        // the app feel busy for no benefit.
        refetchOnWindowFocus: false,
        staleTime: 10_000,
        retry: (failureCount, error) => {
          const status = (error as { status?: number })?.status;
          // Never retry a refusal or a missing row; it will not change.
          if (status && status >= 400 && status < 500) return false;
          return failureCount < 2;
        },
      },
      mutations: { retry: false },
    },
  });
}

export interface StudyPilotAppProps {
  platform: Platform;
}

export function StudyPilotApp({ platform }: StudyPilotAppProps) {
  const [queryClient] = useState(createQueryClient);

  return (
    <QueryClientProvider client={queryClient}>
      <AppProvider platform={platform}>
        <RouteErrorBoundary>
          <AuthGate>
          <AppShell>
            <Routes>
              <Route path="/" element={<DashboardScreen />} />
              <Route path="/courses" element={<CoursesScreen />} />
              <Route path="/library" element={<LibraryScreen />} />
              <Route path="/read/:documentId" element={<ReaderScreen />} />
              <Route path="/tutor" element={<TutorScreen />} />
              <Route path="/flashcards" element={<FlashcardsScreen />} />
              <Route path="/quizzes" element={<AssessmentScreen kind="quiz" />} />
              <Route path="/exams" element={<AssessmentScreen kind="mock_exam" />} />
              <Route path="/notes" element={<NotesScreen />} />
              <Route path="/analytics" element={<AnalyticsScreen />} />
              <Route path="/settings" element={<SettingsScreen />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </AppShell>
          </AuthGate>
        </RouteErrorBoundary>
      </AppProvider>
    </QueryClientProvider>
  );
}

/**
 * Decides whether this deployment needs a sign-in before the app is usable.
 *
 * The desktop backend does not mount the account routes, so the status query
 * reports that no authentication is required and this renders straight
 * through — the gate costs the desktop one cheap request and nothing else.
 */
function AuthGate({ children }: { children: ReactNode }) {
  const status = useAuthStatus();

  if (status.isPending) {
    return (
      <div className="flex h-full items-center justify-center gap-2 text-[var(--text-muted)]">
        <Spinner size={14} />
        <span className="text-sm">Connecting…</span>
      </div>
    );
  }

  // A failure here means the backend is unreachable, not that the user is
  // signed out. Render the app so its own error states can explain that.
  if (status.isError) return <>{children}</>;

  if (status.data?.requires_authentication && !status.data.authenticated) {
    return <SignInScreen registrationOpen={status.data.registration_open} />;
  }

  return <>{children}</>;
}

/**
 * Catches render errors so one broken screen does not blank the window.
 *
 * Resets when the route changes, so navigating away recovers without a reload.
 */
class ErrorBoundary extends Component<
  { children: ReactNode; resetKey: string },
  { error: Error | null }
> {
  override state: { error: Error | null } = { error: null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  override componentDidCatch(error: Error, info: ErrorInfo) {
    // Keep this in the console only: a render error can carry document text.
    console.error('StudyPilot render error', error, info.componentStack);
  }

  override componentDidUpdate(previous: { resetKey: string }) {
    if (previous.resetKey !== this.props.resetKey && this.state.error) {
      this.setState({ error: null });
    }
  }

  override render() {
    if (this.state.error) {
      return (
        <div className="p-8">
          <div className="mx-auto max-w-lg rounded-[var(--radius-card)] border border-[var(--signal-danger)]/30 bg-[var(--signal-danger-bg)] p-5">
            <h1 className="text-base font-semibold text-[var(--signal-danger)]">
              This screen could not be displayed
            </h1>
            <p className="mt-1.5 text-[0.8125rem] leading-relaxed text-[var(--text-primary)]">
              {this.state.error.message}
            </p>
            <p className="mt-2 text-[0.75rem] text-[var(--text-secondary)]">
              Your data is unaffected. Navigating to another screen will recover.
            </p>
            <Button className="mt-3" onClick={() => this.setState({ error: null })}>
              Try again
            </Button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

function RouteErrorBoundary({ children }: { children: ReactNode }) {
  const location = useLocation();
  return <ErrorBoundary resetKey={location.pathname}>{children}</ErrorBoundary>;
}
