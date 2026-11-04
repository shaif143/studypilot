import '@studypilot/ui/theme.css';
import 'katex/dist/katex.min.css';

import { StudyPilotApp } from '@studypilot/app';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { HashRouter } from 'react-router-dom';

import { desktopPlatform, prepareDesktopPlatform } from './platform';

const container = document.getElementById('root');
if (!container) throw new Error('Missing #root element');

const root = createRoot(container);

const PANEL: React.CSSProperties = {
  display: 'flex',
  height: '100vh',
  alignItems: 'center',
  justifyContent: 'center',
  padding: '2rem',
  fontFamily: 'ui-sans-serif, -apple-system, system-ui, sans-serif',
  textAlign: 'center',
  color: 'var(--text-primary)',
  background: 'var(--surface-page)',
};

/**
 * Shown while the bundled engine starts.
 *
 * The backend is a self-extracting executable, so a cold launch takes a few
 * seconds. Saying so is better than an empty window.
 */
function Starting() {
  return (
    <div style={PANEL}>
      <div>
        <div
          style={{
            width: 20,
            height: 20,
            margin: '0 auto',
            borderRadius: '50%',
            border: '2px solid currentColor',
            borderTopColor: 'transparent',
            opacity: 0.5,
            animation: 'spin 0.8s linear infinite',
          }}
        />
        <p style={{ marginTop: '1rem', fontSize: '0.875rem', opacity: 0.75 }}>
          Starting the StudyPilot engine…
        </p>
        <p style={{ marginTop: '0.35rem', fontSize: '0.75rem', opacity: 0.5 }}>
          The first launch after an update takes a few seconds longer.
        </p>
        <style>{'@keyframes spin { to { transform: rotate(360deg) } }'}</style>
      </div>
    </div>
  );
}

function StartupFailure({ message }: { message: string }) {
  return (
    <div style={PANEL}>
      <div style={{ maxWidth: '28rem' }}>
        <h1 style={{ fontSize: '1rem', fontWeight: 600 }}>
          StudyPilot could not start its local engine
        </h1>
        <p style={{ marginTop: '0.5rem', fontSize: '0.875rem', lineHeight: 1.6, opacity: 0.8 }}>
          {message}
        </p>
        <p style={{ marginTop: '0.75rem', fontSize: '0.8125rem', opacity: 0.6 }}>
          Quit and reopen the app. If it keeps happening, the bundled backend may be missing or
          blocked by macOS security settings. Your documents and progress are not affected.
        </p>
      </div>
    </div>
  );
}

// The window paints this immediately; the shell starts the engine in parallel.
root.render(<Starting />);

/**
 * The webview must know the sidecar's address and token before the first
 * request, so the shell is asked for them before the app renders. A failure
 * here means the engine did not start, which is reported rather than left as
 * an app that silently cannot load anything.
 */
prepareDesktopPlatform()
  .then(() => {
    root.render(
      <StrictMode>
        {/* Hash routing avoids depending on the custom protocol's path handling. */}
        <HashRouter>
          <StudyPilotApp platform={desktopPlatform} />
        </HashRouter>
      </StrictMode>,
    );
  })
  .catch((error: unknown) => {
    root.render(
      <StartupFailure
        message={
          error instanceof Error ? error.message : String(error ?? 'The engine reported no address.')
        }
      />,
    );
  });
