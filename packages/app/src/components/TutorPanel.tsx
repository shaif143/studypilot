/**
 * The tutor conversation.
 *
 * Three modes share one panel: asking a question about the course, explaining
 * the page in view, and Socratic assignment coaching. The mode changes the
 * prompt and the retrieval scope on the backend; here it changes the composer
 * and, for assignment mode, exposes the hint level so the student escalates
 * deliberately rather than being handed an answer.
 */
import type { Citation, ExplanationLevel, Message, TutorMode } from '@studypilot/shared';
import { Badge, Button, Callout, Select, Spinner, Tabs, Textarea } from '@studypilot/ui';
import { AlertTriangle, CornerDownLeft, Lightbulb, Sparkles } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';

import { AnswerMeta } from './AnswerMeta';
import { CitationList } from './CitationList';
import { GroundedAnswer } from './GroundedAnswer';
import { ProviderNotice } from './ProviderNotice';
import { describeError, useAsk, useMessages, useProviders, useSettings } from '../hooks/queries';

export interface TutorPanelProps {
  sessionId: string | undefined;
  courseId: string | undefined;
  documentId: string | undefined;
  pageNumber?: number;
  onCitationClick?: (citation: Citation) => void;
  /** Hides the mode switch when the panel is used outside the reader. */
  availableModes?: TutorMode[];
  className?: string;
}

const MODE_PLACEHOLDERS: Record<TutorMode, string> = {
  question: 'Ask anything about this course…',
  explain: 'Anything specific about this page? (optional)',
  assignment: 'Paste the assignment question, or describe where you are stuck…',
};

export function TutorPanel({
  sessionId,
  courseId,
  documentId,
  pageNumber,
  onCitationClick,
  availableModes = ['question', 'explain', 'assignment'],
  className,
}: TutorPanelProps) {
  const settings = useSettings();
  const providers = useProviders();
  const messages = useMessages(sessionId);
  const ask = useAsk();

  const [mode, setMode] = useState<TutorMode>(availableModes[0] ?? 'question');
  const [draft, setDraft] = useState('');
  const [hintLevel, setHintLevel] = useState(1);
  const [level, setLevel] = useState<ExplanationLevel | ''>('');
  const scrollRef = useRef<HTMLDivElement>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);

  const policy = settings.data?.assignment_policy ?? 'socratic';
  const cloudAllowed = settings.data?.allow_cloud_processing ?? false;
  const activeProvider = providers.data?.llm.find((p) => p.key === providers.data?.active_llm);
  const needsCloudOptIn = Boolean(activeProvider?.sends_data_off_device);

  // Keep the newest turn in view.
  useEffect(() => {
    const element = scrollRef.current;
    if (element) element.scrollTop = element.scrollHeight;
  }, [messages.data?.length, ask.isPending]);

  const submit = async () => {
    const question = draft.trim();
    if (mode !== 'explain' && question.length === 0) return;
    if (ask.isPending) return;

    setDraft('');
    try {
      await ask.mutateAsync({
        question,
        session_id: sessionId ?? null,
        course_id: courseId ?? null,
        document_id: documentId ?? null,
        page_number: pageNumber ?? null,
        mode,
        hint_level: hintLevel,
        explanation_level: level || null,
        allow_cloud: cloudAllowed && needsCloudOptIn,
      });
      if (mode === 'assignment') setHintLevel((current) => Math.min(6, current + 1));
    } catch {
      // Rendered below from ask.error; the draft is restored for retry.
      setDraft(question);
    }
  };

  const turns = messages.data ?? [];

  return (
    <div className={['flex min-h-0 flex-col', className].filter(Boolean).join(' ')}>
      {availableModes.length > 1 ? (
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[var(--border-subtle)] px-4 py-2.5">
          <Tabs
            size="sm"
            value={mode}
            onChange={(next) => {
              setMode(next);
              setHintLevel(1);
            }}
            options={availableModes.map((value) => ({
              value,
              label:
                value === 'question'
                  ? 'Ask'
                  : value === 'explain'
                    ? 'Explain this page'
                    : 'Assignment help',
            }))}
          />
          <Select
            aria-label="Explanation depth"
            className="h-7 w-auto text-[0.75rem]"
            value={level}
            onChange={(event) => setLevel(event.target.value as ExplanationLevel | '')}
          >
            <option value="">
              Default depth ({settings.data?.explanation_level ?? 'intermediate'})
            </option>
            <option value="simple">Simple</option>
            <option value="intermediate">Intermediate</option>
            <option value="advanced">Advanced</option>
          </Select>
        </div>
      ) : null}

      <div ref={scrollRef} className="sp-scroll min-h-0 flex-1 overflow-y-auto px-4 py-3">
        <div className="space-y-4">
          <ProviderNotice compact />

          {mode === 'assignment' ? (
            policy === 'off' ? (
              <Callout tone="caution" icon={<AlertTriangle size={14} />}>
                Assignment help is switched off for this profile. You can change that in
                Settings if your course allows it.
              </Callout>
            ) : (
              <Callout tone="info" icon={<Lightbulb size={14} />}>
                {policy === 'socratic'
                  ? `Socratic mode: you get one hint at a time, not the answer. Currently at hint level ${hintLevel} of 6.`
                  : 'Full-explanation mode: the coach will work through the material directly.'}
              </Callout>
            )
          ) : null}

          {messages.isPending && sessionId ? (
            <div className="flex items-center gap-2 text-[0.8125rem] text-[var(--text-muted)]">
              <Spinner size={12} /> Loading this conversation…
            </div>
          ) : null}

          {turns.length === 0 && !ask.isPending ? (
            <div className="rounded-[var(--radius-control)] border border-dashed border-[var(--border-strong)] px-4 py-5 text-center">
              <Sparkles size={18} className="mx-auto text-[var(--text-muted)]" />
              <p className="mt-2 text-[0.8125rem] font-medium text-[var(--text-primary)]">
                Ask about anything in this course
              </p>
              <p className="mt-1 text-[0.75rem] leading-relaxed text-[var(--text-secondary)]">
                Answers come only from the material you have uploaded, with citations you can
                click. If your material does not cover something, the tutor says so instead of
                guessing.
              </p>
            </div>
          ) : null}

          {turns.map((message) => (
            <TurnView key={message.id} message={message} onCitationClick={onCitationClick} />
          ))}

          {ask.isPending ? (
            <div className="flex items-center gap-2 text-[0.8125rem] text-[var(--text-secondary)]">
              <Spinner size={12} />
              Searching your material…
            </div>
          ) : null}

          {ask.error ? (
            <Callout tone="danger" icon={<AlertTriangle size={14} />} title="Could not answer">
              {describeError(ask.error)}
            </Callout>
          ) : null}
        </div>
      </div>

      <div className="border-t border-[var(--border-subtle)] bg-[var(--surface-sunken)] p-3">
        <Textarea
          ref={composerRef}
          rows={2}
          value={draft}
          placeholder={MODE_PLACEHOLDERS[mode]}
          disabled={mode === 'assignment' && policy === 'off'}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            // Enter sends; Shift+Enter adds a line.
            if (event.key === 'Enter' && !event.shiftKey) {
              event.preventDefault();
              void submit();
            }
          }}
          className="bg-[var(--surface-raised)]"
        />
        <div className="mt-2 flex items-center justify-between gap-2">
          <span className="text-[0.6875rem] text-[var(--text-muted)]">
            {mode === 'assignment' && policy !== 'off'
              ? `Next: hint level ${hintLevel}`
              : 'Enter to send · Shift+Enter for a new line'}
          </span>
          <Button
            size="sm"
            variant="primary"
            icon={<CornerDownLeft size={13} />}
            loading={ask.isPending}
            disabled={(mode !== 'explain' && draft.trim().length === 0) || (mode === 'assignment' && policy === 'off')}
            onClick={() => void submit()}
          >
            {mode === 'explain' && draft.trim().length === 0 ? 'Explain this page' : 'Send'}
          </Button>
        </div>
      </div>
    </div>
  );
}

function TurnView({
  message,
  onCitationClick,
}: {
  message: Message;
  onCitationClick?: (citation: Citation) => void;
}) {
  if (message.role === 'user') {
    return (
      <div className="flex justify-end">
        <p className="max-w-[85%] rounded-[var(--radius-card)] rounded-br-sm bg-[var(--surface-selected)] px-3 py-2 text-[0.8125rem] leading-relaxed text-[var(--text-primary)]">
          {message.content}
        </p>
      </div>
    );
  }

  const meta = message.meta ?? {};
  return (
    <div>
      {meta.mode && meta.mode !== 'question' ? (
        <Badge tone="neutral" className="mb-1.5">
          {meta.mode === 'explain' ? 'Page explanation' : 'Assignment hint'}
        </Badge>
      ) : null}
      <GroundedAnswer
        markdown={message.content}
        citations={message.citations}
        onCitationClick={onCitationClick}
      />
      <AnswerMeta
        grounded={Boolean(meta.grounded)}
        generative={Boolean(meta.generative)}
        abstained={Boolean(meta.abstained)}
        provider={meta.provider ?? ''}
        model={meta.model ?? ''}
        evidenceSufficient={meta.evidence_sufficient !== false}
        sourceCount={message.citations.length}
        warnings={meta.warnings ?? []}
      />
      <CitationList citations={message.citations} onSelect={onCitationClick} compact />
    </div>
  );
}
