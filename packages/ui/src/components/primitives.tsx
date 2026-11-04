/**
 * Design-system primitives.
 *
 * Each component is a thin, accessible wrapper that resolves colour from the
 * theme's CSS custom properties, so no caller needs a `dark:` variant and the
 * two hosts cannot drift apart visually.
 */
import clsx from 'clsx';
import {
  type ButtonHTMLAttributes,
  type HTMLAttributes,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
  forwardRef,
  useEffect,
  useId,
  useRef,
} from 'react';

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger';
type Size = 'sm' | 'md' | 'lg';

const BUTTON_BASE =
  'inline-flex items-center justify-center gap-2 font-medium rounded-[var(--radius-control)] ' +
  'transition-[background-color,border-color,opacity] duration-150 select-none ' +
  'disabled:opacity-50 disabled:cursor-not-allowed whitespace-nowrap';

const BUTTON_SIZES: Record<Size, string> = {
  sm: 'h-7 px-2.5 text-[0.8125rem]',
  md: 'h-9 px-3.5 text-sm',
  lg: 'h-11 px-5 text-[0.9375rem]',
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  /** Renders a spinner and blocks interaction. */
  loading?: boolean;
  icon?: ReactNode;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = 'secondary', size = 'md', loading, icon, className, children, disabled, ...rest },
  ref,
) {
  const variants: Record<Variant, string> = {
    primary:
      'text-[var(--text-inverse)] bg-[var(--color-accent-600)] hover:bg-[var(--color-accent-700)] ' +
      'border border-[var(--color-accent-600)]',
    secondary:
      'text-[var(--text-primary)] bg-[var(--surface-raised)] hover:bg-[var(--surface-hover)] ' +
      'border border-[var(--border-strong)]',
    ghost:
      'text-[var(--text-secondary)] hover:text-[var(--text-primary)] ' +
      'hover:bg-[var(--surface-hover)] border border-transparent',
    danger:
      'text-[var(--signal-danger)] bg-[var(--signal-danger-bg)] border ' +
      'border-[var(--signal-danger)]/30 hover:border-[var(--signal-danger)]/60',
  };
  return (
    <button
      ref={ref}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={clsx(BUTTON_BASE, BUTTON_SIZES[size], variants[variant], className)}
      {...rest}
    >
      {loading ? <Spinner size={size === 'lg' ? 16 : 13} /> : icon}
      {children}
    </button>
  );
});

export function Spinner({ size = 14, label }: { size?: number; label?: string }) {
  return (
    <span
      role="status"
      aria-label={label ?? 'Loading'}
      className="inline-block animate-spin rounded-full border-2 border-current border-t-transparent align-[-0.125em]"
      style={{ width: size, height: size, opacity: 0.7 }}
    />
  );
}

export function Card({
  className,
  padded = true,
  children,
  ...rest
}: HTMLAttributes<HTMLDivElement> & { padded?: boolean }) {
  return (
    <div
      className={clsx(
        'rounded-[var(--radius-card)] border border-[var(--border-subtle)]',
        'bg-[var(--surface-raised)] shadow-[var(--shadow-card)]',
        padded && 'p-4',
        className,
      )}
      {...rest}
    >
      {children}
    </div>
  );
}

export function SectionHeading({
  title,
  description,
  action,
  className,
}: {
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div className={clsx('flex items-start justify-between gap-4', className)}>
      <div className="min-w-0">
        <h2 className="text-[0.9375rem] font-semibold text-[var(--text-primary)]">{title}</h2>
        {description ? (
          <p className="mt-0.5 text-[0.8125rem] leading-relaxed text-[var(--text-secondary)]">
            {description}
          </p>
        ) : null}
      </div>
      {action ? <div className="shrink-0">{action}</div> : null}
    </div>
  );
}

export type Tone = 'neutral' | 'accent' | 'grounded' | 'caution' | 'danger' | 'info';

const TONES: Record<Tone, string> = {
  neutral:
    'text-[var(--text-secondary)] bg-[var(--surface-sunken)] border-[var(--border-subtle)]',
  accent:
    'text-[var(--color-accent-700)] bg-[var(--color-accent-100)] border-[var(--color-accent-200)]',
  grounded:
    'text-[var(--signal-grounded)] bg-[var(--signal-grounded-bg)] border-[var(--signal-grounded)]/25',
  caution:
    'text-[var(--signal-caution)] bg-[var(--signal-caution-bg)] border-[var(--signal-caution)]/25',
  danger:
    'text-[var(--signal-danger)] bg-[var(--signal-danger-bg)] border-[var(--signal-danger)]/25',
  info: 'text-[var(--signal-info)] bg-[var(--signal-info-bg)] border-[var(--signal-info)]/25',
};

export function Badge({
  tone = 'neutral',
  icon,
  className,
  children,
  title,
}: {
  tone?: Tone;
  icon?: ReactNode;
  className?: string;
  children: ReactNode;
  title?: string;
}) {
  return (
    <span
      title={title}
      className={clsx(
        'inline-flex items-center gap-1 rounded-full border px-2 py-0.5',
        'text-[0.6875rem] font-medium leading-[1.4] whitespace-nowrap',
        TONES[tone],
        className,
      )}
    >
      {icon}
      {children}
    </span>
  );
}

/** A full-width coloured notice. Used for provider status and privacy notes. */
export function Callout({
  tone = 'info',
  title,
  icon,
  children,
  action,
  className,
}: {
  tone?: Tone;
  title?: ReactNode;
  icon?: ReactNode;
  children?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={clsx(
        'flex items-start gap-2.5 rounded-[var(--radius-control)] border px-3 py-2.5',
        TONES[tone],
        className,
      )}
    >
      {icon ? <span className="mt-px shrink-0">{icon}</span> : null}
      <div className="min-w-0 flex-1 text-[0.8125rem] leading-relaxed">
        {title ? <p className="font-semibold">{title}</p> : null}
        {children}
      </div>
      {action ? <div className="shrink-0">{action}</div> : null}
    </div>
  );
}

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  function Input({ className, ...rest }, ref) {
    return (
      <input
        ref={ref}
        className={clsx(
          'h-9 w-full rounded-[var(--radius-control)] border border-[var(--border-strong)]',
          'bg-[var(--surface-raised)] px-3 text-sm text-[var(--text-primary)]',
          'placeholder:text-[var(--text-muted)] disabled:opacity-60',
          className,
        )}
        {...rest}
      />
    );
  },
);

export const Textarea = forwardRef<
  HTMLTextAreaElement,
  TextareaHTMLAttributes<HTMLTextAreaElement>
>(function Textarea({ className, ...rest }, ref) {
  return (
    <textarea
      ref={ref}
      className={clsx(
        'w-full rounded-[var(--radius-control)] border border-[var(--border-strong)]',
        'bg-[var(--surface-raised)] px-3 py-2 text-sm leading-relaxed text-[var(--text-primary)]',
        'placeholder:text-[var(--text-muted)] resize-y',
        className,
      )}
      {...rest}
    />
  );
});

export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(
  function Select({ className, children, ...rest }, ref) {
    return (
      <select
        ref={ref}
        className={clsx(
          'h-9 w-full rounded-[var(--radius-control)] border border-[var(--border-strong)]',
          'bg-[var(--surface-raised)] px-2.5 text-sm text-[var(--text-primary)]',
          className,
        )}
        {...rest}
      >
        {children}
      </select>
    );
  },
);

export function Field({
  label,
  hint,
  error,
  children,
  className,
}: {
  label: ReactNode;
  hint?: ReactNode;
  error?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <label className={clsx('block', className)}>
      <span className="mb-1.5 block text-[0.8125rem] font-medium text-[var(--text-primary)]">
        {label}
      </span>
      {children}
      {hint && !error ? (
        <span className="mt-1 block text-[0.75rem] leading-relaxed text-[var(--text-muted)]">
          {hint}
        </span>
      ) : null}
      {error ? (
        <span className="mt-1 block text-[0.75rem] text-[var(--signal-danger)]">{error}</span>
      ) : null}
    </label>
  );
}

/** An accessible switch. The label is clickable and the state is announced. */
export function Toggle({
  checked,
  onChange,
  label,
  description,
  disabled,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  label: ReactNode;
  description?: ReactNode;
  disabled?: boolean;
}) {
  const id = useId();
  return (
    <div className="flex items-start justify-between gap-4 py-2.5">
      <div className="min-w-0">
        <label
          htmlFor={id}
          className="block text-[0.8125rem] font-medium text-[var(--text-primary)]"
        >
          {label}
        </label>
        {description ? (
          <p className="mt-0.5 text-[0.75rem] leading-relaxed text-[var(--text-secondary)]">
            {description}
          </p>
        ) : null}
      </div>
      <button
        id={id}
        type="button"
        role="switch"
        aria-checked={checked}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={clsx(
          'relative mt-0.5 h-[1.3rem] w-[2.25rem] shrink-0 rounded-full border transition-colors',
          'disabled:opacity-50 disabled:cursor-not-allowed',
          checked
            ? 'border-[var(--color-accent-600)] bg-[var(--color-accent-600)]'
            : 'border-[var(--border-strong)] bg-[var(--surface-sunken)]',
        )}
      >
        <span
          className={clsx(
            'absolute top-[0.125rem] h-[0.9rem] w-[0.9rem] rounded-full bg-white shadow transition-[left]',
            checked ? 'left-[1.2rem]' : 'left-[0.125rem]',
          )}
        />
      </button>
    </div>
  );
}

export function ProgressBar({
  value,
  tone = 'accent',
  label,
  className,
}: {
  /** 0..1 */
  value: number;
  tone?: Tone;
  label?: string;
  className?: string;
}) {
  const clamped = Math.max(0, Math.min(1, Number.isFinite(value) ? value : 0));
  const fills: Record<Tone, string> = {
    neutral: 'bg-[var(--border-strong)]',
    accent: 'bg-[var(--color-accent-500)]',
    grounded: 'bg-[var(--signal-grounded)]',
    caution: 'bg-[var(--signal-caution)]',
    danger: 'bg-[var(--signal-danger)]',
    info: 'bg-[var(--signal-info)]',
  };
  return (
    <div
      role="progressbar"
      aria-valuenow={Math.round(clamped * 100)}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-label={label}
      className={clsx(
        'h-1.5 w-full overflow-hidden rounded-full bg-[var(--surface-sunken)]',
        className,
      )}
    >
      <div
        className={clsx('h-full rounded-full transition-[width] duration-300', fills[tone])}
        style={{ width: `${clamped * 100}%` }}
      />
    </div>
  );
}

export function EmptyState({
  icon,
  title,
  description,
  action,
  className,
}: {
  icon?: ReactNode;
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={clsx(
        'flex flex-col items-center justify-center gap-2 rounded-[var(--radius-card)]',
        'border border-dashed border-[var(--border-strong)] px-6 py-12 text-center',
        className,
      )}
    >
      {icon ? <div className="text-[var(--text-muted)]">{icon}</div> : null}
      <p className="text-sm font-medium text-[var(--text-primary)]">{title}</p>
      {description ? (
        <p className="max-w-md text-[0.8125rem] leading-relaxed text-[var(--text-secondary)]">
          {description}
        </p>
      ) : null}
      {action ? <div className="mt-2">{action}</div> : null}
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return (
    <div
      aria-hidden
      className={clsx(
        'animate-pulse rounded-[var(--radius-control)] bg-[var(--surface-sunken)]',
        className,
      )}
    />
  );
}

export function Tabs<T extends string>({
  value,
  onChange,
  options,
  className,
  size = 'md',
}: {
  value: T;
  onChange: (next: T) => void;
  options: Array<{ value: T; label: ReactNode; badge?: ReactNode }>;
  className?: string;
  size?: 'sm' | 'md';
}) {
  return (
    <div
      role="tablist"
      className={clsx(
        'inline-flex items-center gap-0.5 rounded-[var(--radius-control)]',
        'border border-[var(--border-subtle)] bg-[var(--surface-sunken)] p-0.5',
        className,
      )}
    >
      {options.map((option) => {
        const active = option.value === value;
        return (
          <button
            key={option.value}
            role="tab"
            type="button"
            aria-selected={active}
            onClick={() => onChange(option.value)}
            className={clsx(
              'inline-flex items-center gap-1.5 rounded-[calc(var(--radius-control)-2px)]',
              'font-medium transition-colors',
              size === 'sm' ? 'h-6 px-2 text-[0.75rem]' : 'h-7 px-3 text-[0.8125rem]',
              active
                ? 'bg-[var(--surface-raised)] text-[var(--text-primary)] shadow-[var(--shadow-card)]'
                : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]',
            )}
          >
            {option.label}
            {option.badge}
          </button>
        );
      })}
    </div>
  );
}

/** A modal dialog using the platform `<dialog>` element for focus trapping. */
export function Dialog({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  width = 'md',
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  description?: ReactNode;
  children?: ReactNode;
  footer?: ReactNode;
  width?: 'sm' | 'md' | 'lg';
}) {
  const ref = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    if (open && !element.open) element.showModal();
    if (!open && element.open) element.close();
  }, [open]);

  const widths = { sm: 'max-w-sm', md: 'max-w-lg', lg: 'max-w-2xl' };

  return (
    <dialog
      ref={ref}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onClick={(event) => {
        // Clicking the backdrop (the dialog element itself) dismisses it.
        if (event.target === ref.current) onClose();
      }}
      className={clsx(
        'm-auto w-[calc(100vw-2rem)] rounded-[var(--radius-card)] p-0',
        'border border-[var(--border-subtle)] bg-[var(--surface-raised)]',
        'text-[var(--text-primary)] shadow-[var(--shadow-overlay)]',
        'backdrop:bg-black/40 backdrop:backdrop-blur-sm',
        widths[width],
      )}
    >
      <div className="p-5">
        <h2 className="text-base font-semibold">{title}</h2>
        {description ? (
          <p className="mt-1 text-[0.8125rem] leading-relaxed text-[var(--text-secondary)]">
            {description}
          </p>
        ) : null}
        {children ? <div className="mt-4">{children}</div> : null}
      </div>
      {footer ? (
        <div className="flex justify-end gap-2 border-t border-[var(--border-subtle)] bg-[var(--surface-sunken)] px-5 py-3">
          {footer}
        </div>
      ) : null}
    </dialog>
  );
}

export function KeyHint({ children }: { children: ReactNode }) {
  return (
    <kbd
      className={clsx(
        'inline-flex h-[1.1rem] min-w-[1.1rem] items-center justify-center rounded border',
        'border-[var(--border-subtle)] bg-[var(--surface-sunken)] px-1',
        'font-sans text-[0.6875rem] font-medium text-[var(--text-muted)]',
      )}
    >
      {children}
    </kbd>
  );
}
