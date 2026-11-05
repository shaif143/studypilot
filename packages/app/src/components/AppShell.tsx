/**
 * The application frame: sidebar navigation, a backend-status line, and the
 * routed content area. Identical in both hosts; the only difference is that the
 * desktop host adds a draggable title-bar strip.
 */
import { Badge, Button, Spinner } from '@studypilot/ui';
import {
  BookMarked,
  BookOpen,
  BrainCircuit,
  ChartNoAxesColumn,
  CloudOff,
  FileQuestion,
  GraduationCap,
  LayoutDashboard,
  Library,
  MessagesSquare,
  NotebookPen,
  Settings as SettingsIcon,
} from 'lucide-react';
import type { ReactNode } from 'react';
import { NavLink } from 'react-router-dom';

import { useApp } from '../context';
import { useDashboard, useHealth, useProviders } from '../hooks/queries';

interface NavItem {
  to: string;
  label: string;
  icon: ReactNode;
  /** A live count shown next to the label, when there is something waiting. */
  badge?: number;
}

export function AppShell({ children }: { children: ReactNode }) {
  const { platform } = useApp();
  const health = useHealth();
  const providers = useProviders();
  const dashboard = useDashboard();

  const due = dashboard.data?.due_flashcards ?? 0;

  const primary: NavItem[] = [
    { to: '/', label: 'Dashboard', icon: <LayoutDashboard size={16} /> },
    { to: '/courses', label: 'My Courses', icon: <GraduationCap size={16} /> },
    { to: '/library', label: 'Lecture Library', icon: <Library size={16} /> },
    { to: '/tutor', label: 'AI Tutor', icon: <MessagesSquare size={16} /> },
  ];
  const study: NavItem[] = [
    { to: '/flashcards', label: 'Flashcards', icon: <BookMarked size={16} />, badge: due },
    { to: '/quizzes', label: 'Quizzes', icon: <FileQuestion size={16} /> },
    { to: '/exams', label: 'Mock Exams', icon: <BookOpen size={16} /> },
    { to: '/notes', label: 'My Notes', icon: <NotebookPen size={16} /> },
  ];
  const insight: NavItem[] = [
    { to: '/analytics', label: 'Learning Analytics', icon: <ChartNoAxesColumn size={16} /> },
    { to: '/settings', label: 'Settings', icon: <SettingsIcon size={16} /> },
  ];

  return (
    <div className="flex h-full w-full overflow-hidden bg-[var(--surface-page)]">
      <aside
        className="flex w-[15rem] shrink-0 flex-col border-r border-[var(--border-subtle)] bg-[var(--surface-sunken)]"
        aria-label="Main navigation"
      >
        {platform.kind === 'desktop' ? (
          // Space for the macOS traffic lights; the strip is the window drag area.
          <div data-tauri-drag-region className="h-[2.1rem] shrink-0" />
        ) : null}

        <div className="flex items-center gap-2 px-4 pb-3 pt-4">
          <BrainCircuit size={20} className="text-[var(--text-accent)]" />
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold text-[var(--text-primary)]">
              StudyPilot
            </p>
            <p className="text-[0.6875rem] text-[var(--text-muted)]">
              {platform.kind === 'desktop' ? 'On this Mac' : 'Web'}
            </p>
          </div>
        </div>

        <nav className="sp-scroll flex-1 overflow-y-auto px-2 pb-2">
          <NavGroup items={primary} />
          <NavGroup title="Study" items={study} />
          <NavGroup title="Progress" items={insight} />
        </nav>

        <div className="border-t border-[var(--border-subtle)] px-3 py-2.5 text-[0.6875rem] leading-relaxed text-[var(--text-muted)]">
          {health.isPending ? (
            <span className="flex items-center gap-1.5">
              <Spinner size={11} /> Connecting to the backend…
            </span>
          ) : health.isError ? (
            <span className="text-[var(--signal-danger)]">
              {platform.kind === 'desktop'
                ? 'The local engine stopped. Quit and reopen StudyPilot.'
                : 'No backend is connected to this site yet.'}
            </span>
          ) : (
            <div className="space-y-1">
              <div className="flex items-center gap-1">
                {providers.data?.active_is_generative ? null : (
                  <CloudOff size={11} className="shrink-0" />
                )}
                <span className="truncate" title={providers.data?.active_llm}>
                  {providers.data?.active_is_generative
                    ? `Model: ${providers.data.active_model || providers.data.active_llm}`
                    : 'No AI model configured'}
                </span>
              </div>
              <p className="truncate" title={health.data?.runtime.embedding_model}>
                {/* This account's own passages. `runtime.vector_count` counts
                    the whole store, which on a shared deployment is both
                    wrong for the reader and another account's business. */}
                {dashboard.data?.indexed_chunk_count ?? 0} indexed passages
              </p>
            </div>
          )}
        </div>
      </aside>

      <main className="sp-scroll min-w-0 flex-1 overflow-y-auto">
        {platform.kind === 'desktop' ? (
          <div data-tauri-drag-region className="h-[2.1rem] w-full" />
        ) : null}
        {children}
      </main>
    </div>
  );
}

function NavGroup({ title, items }: { title?: string; items: NavItem[] }) {
  return (
    <div className="mb-1">
      {title ? (
        <p className="px-2.5 pb-1 pt-3 text-[0.6875rem] font-semibold uppercase tracking-wide text-[var(--text-muted)]">
          {title}
        </p>
      ) : null}
      <ul className="space-y-px">
        {items.map((item) => (
          <li key={item.to}>
            <NavLink
              to={item.to}
              end={item.to === '/'}
              className={({ isActive }) =>
                [
                  'flex items-center gap-2.5 rounded-[var(--radius-control)] px-2.5 py-[0.4rem]',
                  'text-[0.8125rem] font-medium transition-colors',
                  isActive
                    ? 'bg-[var(--surface-selected)] text-[var(--text-primary)]'
                    : 'text-[var(--text-secondary)] hover:bg-[var(--surface-hover)] hover:text-[var(--text-primary)]',
                ].join(' ')
              }
            >
              <span className="shrink-0 opacity-80">{item.icon}</span>
              <span className="min-w-0 flex-1 truncate">{item.label}</span>
              {item.badge ? (
                <Badge tone="accent" className="shrink-0">
                  {item.badge}
                </Badge>
              ) : null}
            </NavLink>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** A consistent page header for every screen. */
export function PageHeader({
  title,
  description,
  actions,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className="flex flex-wrap items-start justify-between gap-3 border-b border-[var(--border-subtle)] px-6 pb-4 pt-5">
      <div className="min-w-0">
        <h1 className="text-lg font-semibold tracking-[-0.01em] text-[var(--text-primary)]">
          {title}
        </h1>
        {description ? (
          <p className="mt-0.5 max-w-2xl text-[0.8125rem] leading-relaxed text-[var(--text-secondary)]">
            {description}
          </p>
        ) : null}
      </div>
      {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
    </header>
  );
}

export function PageBody({ children }: { children: ReactNode }) {
  return <div className="px-6 py-5">{children}</div>;
}

export function ErrorPanel({
  title = 'Something went wrong',
  message,
  onRetry,
}: {
  title?: string;
  message: string;
  onRetry?: () => void;
}) {
  return (
    <div className="rounded-[var(--radius-card)] border border-[var(--signal-danger)]/30 bg-[var(--signal-danger-bg)] px-4 py-3">
      <p className="text-sm font-semibold text-[var(--signal-danger)]">{title}</p>
      <p className="mt-1 text-[0.8125rem] leading-relaxed text-[var(--text-primary)]">{message}</p>
      {onRetry ? (
        <Button size="sm" className="mt-2.5" onClick={onRetry}>
          Try again
        </Button>
      ) : null}
    </div>
  );
}
