/**
 * Application context.
 *
 * Holds the single API client, the resolved platform adapter, and the theme.
 * This is the seam between the shared application and its two hosts: nothing
 * below here knows whether it is running in a browser tab or in a Tauri
 * webview.
 */
import { StudyPilotClient } from '@studypilot/api-client';
import type { Platform, Theme } from '@studypilot/shared';
import {
  type ReactNode,
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from 'react';

interface AppContextValue {
  client: StudyPilotClient;
  platform: Platform;
  theme: Theme;
  setTheme: (theme: Theme) => void;
  /** The theme actually in effect once `system` is resolved. */
  resolvedTheme: 'light' | 'dark';
}

const AppContext = createContext<AppContextValue | null>(null);

const THEME_STORAGE_KEY = 'studypilot.theme';

function readStoredTheme(): Theme {
  try {
    const stored = localStorage.getItem(THEME_STORAGE_KEY);
    if (stored === 'light' || stored === 'dark' || stored === 'system') return stored;
  } catch {
    // Private browsing or blocked storage: fall back to following the OS.
  }
  return 'system';
}

export function AppProvider({
  platform,
  children,
}: {
  platform: Platform;
  children: ReactNode;
}) {
  const client = useMemo(() => new StudyPilotClient(platform), [platform]);
  const [theme, setThemeState] = useState<Theme>(readStoredTheme);
  const [systemDark, setSystemDark] = useState(
    () => window.matchMedia?.('(prefers-color-scheme: dark)').matches ?? false,
  );

  useEffect(() => {
    const query = window.matchMedia?.('(prefers-color-scheme: dark)');
    if (!query) return;
    const listener = (event: MediaQueryListEvent) => setSystemDark(event.matches);
    query.addEventListener('change', listener);
    return () => query.removeEventListener('change', listener);
  }, []);

  const resolvedTheme: 'light' | 'dark' =
    theme === 'system' ? (systemDark ? 'dark' : 'light') : theme;

  useEffect(() => {
    document.documentElement.dataset.theme = resolvedTheme;
  }, [resolvedTheme]);

  const setTheme = useCallback((next: Theme) => {
    setThemeState(next);
    try {
      localStorage.setItem(THEME_STORAGE_KEY, next);
    } catch {
      // Not fatal: the theme simply will not persist for this viewer.
    }
  }, []);

  const value = useMemo<AppContextValue>(
    () => ({ client, platform, theme, setTheme, resolvedTheme }),
    [client, platform, theme, setTheme, resolvedTheme],
  );

  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
}

export function useApp(): AppContextValue {
  const value = useContext(AppContext);
  if (!value) throw new Error('useApp must be used inside <AppProvider>');
  return value;
}

export function useClient(): StudyPilotClient {
  return useApp().client;
}
