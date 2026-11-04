/**
 * Sitting a quiz or mock examination.
 *
 * The answer key is never sent with the questions, so this component cannot
 * mark anything locally — it posts the responses and renders what the backend
 * marked. That also means the feedback shown is the real marking method
 * (deterministic, rubric, or unmarked), which the student can see.
 */
import type { Assessment, AttemptResult, Question, ResponseInput } from '@studypilot/shared';
import { formatDuration, provenanceLabel } from '@studypilot/shared';
import {
  Badge,
  Button,
  Callout,
  Card,
  Input,
  ProgressBar,
  SectionHeading,
  Textarea,
} from '@studypilot/ui';
import {
  AlertTriangle,
  CheckCircle2,
  CircleDot,
  Clock,
  RotateCcw,
  XCircle,
} from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';

import { ErrorPanel } from './AppShell';
import { describeError, useSubmitAssessment } from '../hooks/queries';

interface DraftAnswer {
  selected_option?: number;
  answer_text?: string;
  numeric_value?: string;
}

export function QuizRunner({
  assessment,
  onFinished,
  onRestart,
}: {
  assessment: Assessment;
  onFinished?: (result: AttemptResult) => void;
  onRestart?: () => void;
}) {
  const submit = useSubmitAssessment();
  const [answers, setAnswers] = useState<Record<string, DraftAnswer>>({});
  const [result, setResult] = useState<AttemptResult | null>(null);
  const [remaining, setRemaining] = useState<number | null>(
    assessment.time_limit_seconds ?? null,
  );
  const [reviewMode, setReviewMode] = useState(false);

  const questions = assessment.questions;
  const answeredCount = useMemo(
    () =>
      questions.filter((question) => {
        const draft = answers[question.id];
        if (!draft) return false;
        if (question.kind === 'mcq') return draft.selected_option !== undefined;
        if (question.kind === 'numeric') return Boolean(draft.numeric_value?.trim());
        return Boolean(draft.answer_text?.trim());
      }).length,
    [answers, questions],
  );

  const handleSubmit = async () => {
    if (submit.isPending || result) return;
    const responses: ResponseInput[] = questions.map((question) => {
      const draft = answers[question.id] ?? {};
      const parsed =
        draft.numeric_value !== undefined && draft.numeric_value.trim() !== ''
          ? Number.parseFloat(draft.numeric_value)
          : null;
      return {
        question_id: question.id,
        selected_option: draft.selected_option ?? null,
        answer_text: draft.answer_text ?? '',
        numeric_value: parsed !== null && Number.isFinite(parsed) ? parsed : null,
      };
    });
    try {
      const marked = await submit.mutateAsync({ id: assessment.id, responses });
      setResult(marked);
      setReviewMode(true);
      onFinished?.(marked);
    } catch {
      // Rendered below from submit.error.
    }
  };

  // Countdown for timed papers. Submits automatically when it runs out, so a
  // mock exam under a time limit behaves like one.
  useEffect(() => {
    if (remaining === null || result) return;
    const handle = window.setTimeout(() => {
      // Both the tick and the auto-submit happen in the timer callback, which
      // is the external event this effect subscribes to.
      if (remaining <= 1) {
        setRemaining(0);
        void handleSubmit();
      } else {
        setRemaining(remaining - 1);
      }
    }, 1000);
    return () => window.clearTimeout(handle);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [remaining, result]);


  const gradedByQuestion = useMemo(
    () => new Map((result?.responses ?? []).map((row) => [row.question_id, row])),
    [result],
  );

  return (
    <div className="space-y-4">
      <Card>
        <SectionHeading
          title={assessment.title || (assessment.kind === 'mock_exam' ? 'Mock exam' : 'Quiz')}
          description={
            result
              ? `Marked: ${result.score} of ${result.max_score} marks (${result.percentage}%).`
              : `${questions.length} questions · ${assessment.total_marks} marks`
          }
          action={
            <div className="flex items-center gap-2">
              {remaining !== null && !result ? (
                <Badge tone={remaining < 60 ? 'danger' : 'neutral'} icon={<Clock size={11} />}>
                  {formatDuration(remaining)}
                </Badge>
              ) : null}
              {result && onRestart ? (
                <Button size="sm" icon={<RotateCcw size={13} />} onClick={onRestart}>
                  New quiz
                </Button>
              ) : null}
            </div>
          }
        />

        {!result ? (
          <div className="mt-3">
            <ProgressBar
              value={questions.length ? answeredCount / questions.length : 0}
              label="Questions answered"
            />
            <p className="mt-1 text-[0.75rem] text-[var(--text-secondary)]">
              {answeredCount} of {questions.length} answered
            </p>
          </div>
        ) : (
          <div className="mt-3">
            <ProgressBar
              value={result.max_score ? result.score / result.max_score : 0}
              tone={
                result.percentage >= 80 ? 'grounded' : result.percentage >= 50 ? 'caution' : 'danger'
              }
              label="Score"
            />
          </div>
        )}

        {assessment.generator.startsWith('source_derived') && !result ? (
          <Callout tone="neutral" className="mt-3">
            These questions were derived straight from your slides (the answer is the term that
            was removed from the sentence). Configure an AI model for richer question types.
          </Callout>
        ) : null}

        {assessment.warnings.length > 0 && !result ? (
          <Callout tone="caution" icon={<AlertTriangle size={14} />} className="mt-3">
            <ul className="space-y-0.5">
              {assessment.warnings.map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          </Callout>
        ) : null}
      </Card>

      {submit.error ? (
        <ErrorPanel title="Could not submit" message={describeError(submit.error)} />
      ) : null}

      {result ? <ResultSummary result={result} /> : null}

      <ol className="space-y-3">
        {questions.map((question, index) => (
          <li key={question.id}>
            <QuestionCard
              index={index + 1}
              question={question}
              draft={answers[question.id] ?? {}}
              graded={gradedByQuestion.get(question.id)}
              disabled={Boolean(result)}
              reviewMode={reviewMode}
              onChange={(patch) =>
                setAnswers((current) => ({
                  ...current,
                  [question.id]: { ...current[question.id], ...patch },
                }))
              }
            />
          </li>
        ))}
      </ol>

      {!result ? (
        <div className="flex items-center justify-between gap-3 rounded-[var(--radius-card)] border border-[var(--border-subtle)] bg-[var(--surface-raised)] px-4 py-3">
          <p className="text-[0.8125rem] text-[var(--text-secondary)]">
            {answeredCount < questions.length
              ? `${questions.length - answeredCount} unanswered. Unanswered questions score zero.`
              : 'All questions answered.'}
          </p>
          <Button variant="primary" loading={submit.isPending} onClick={() => void handleSubmit()}>
            Submit for marking
          </Button>
        </div>
      ) : null}
    </div>
  );
}

function ResultSummary({ result }: { result: AttemptResult }) {
  return (
    <Card>
      <SectionHeading
        title="Where you stand"
        description="Mastery estimates move with every answer, and stay marked as uncertain until there is enough evidence."
      />
      {result.weak_topics.length > 0 ? (
        <Callout tone="caution" className="mt-3" icon={<AlertTriangle size={14} />}>
          Weakest on: {result.weak_topics.join(', ')}.
        </Callout>
      ) : null}
      {result.mastery_changes.length > 0 ? (
        <ul className="mt-3 space-y-1.5">
          {result.mastery_changes.map((change) => {
            const delta = change.probability - change.previous_probability;
            return (
              <li
                key={`${change.topic_slug}-${change.observations}`}
                className="flex items-center justify-between gap-3 text-[0.8125rem]"
              >
                <span className="truncate text-[var(--text-primary)]">{change.topic_name}</span>
                <span className="flex shrink-0 items-center gap-2 tabular-nums">
                  <span className="text-[var(--text-secondary)]">
                    {Math.round(change.previous_probability * 100)}% →{' '}
                    {Math.round(change.probability * 100)}%
                  </span>
                  <Badge tone={delta >= 0 ? 'grounded' : 'danger'}>
                    {delta >= 0 ? '+' : ''}
                    {Math.round(delta * 100)}
                  </Badge>
                  {!change.confident ? (
                    <Badge tone="neutral" title={`${change.observations} answers so far`}>
                      low evidence
                    </Badge>
                  ) : null}
                </span>
              </li>
            );
          })}
        </ul>
      ) : null}
      {result.recommended_action ? (
        <Callout tone="accent" className="mt-3">
          <strong>Next:</strong> {result.recommendation_reason || result.recommended_action}
        </Callout>
      ) : null}
    </Card>
  );
}

function QuestionCard({
  index,
  question,
  draft,
  graded,
  disabled,
  reviewMode,
  onChange,
}: {
  index: number;
  question: Question;
  draft: DraftAnswer;
  graded: AttemptResult['responses'][number] | undefined;
  disabled: boolean;
  reviewMode: boolean;
  onChange: (patch: DraftAnswer) => void;
}) {
  const correct = graded?.correct;

  return (
    <Card>
      <div className="flex items-start gap-3">
        <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-[var(--surface-sunken)] text-[0.75rem] font-semibold tabular-nums text-[var(--text-secondary)]">
          {index}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <Badge tone="neutral">
              {question.kind === 'mcq'
                ? 'Multiple choice'
                : question.kind === 'numeric'
                  ? 'Numerical'
                  : question.kind === 'explanation'
                    ? 'Explanation'
                    : 'Short answer'}
            </Badge>
            <Badge tone="neutral">
              {question.marks} mark{question.marks === 1 ? '' : 's'}
            </Badge>
            <Badge tone="neutral">{question.difficulty}</Badge>
            <Badge
              tone={question.provenance === 'past_paper' ? 'accent' : 'neutral'}
              title="Where this question came from."
            >
              {provenanceLabel(question.provenance)}
            </Badge>
            {question.source_page ? (
              <Badge tone="neutral">page {question.source_page}</Badge>
            ) : null}
            {reviewMode && graded ? (
              <Badge
                tone={correct === true ? 'grounded' : correct === false ? 'danger' : 'caution'}
                icon={
                  correct === true ? (
                    <CheckCircle2 size={11} />
                  ) : correct === false ? (
                    <XCircle size={11} />
                  ) : (
                    <CircleDot size={11} />
                  )
                }
              >
                {graded.awarded_marks} / {graded.max_marks}
              </Badge>
            ) : null}
          </div>

          <p className="mt-2 whitespace-pre-wrap text-[0.875rem] leading-relaxed text-[var(--text-primary)]">
            {question.prompt}
          </p>

          <div className="mt-3">
            {question.kind === 'mcq' ? (
              <ul className="space-y-1.5">
                {question.options.map((option, optionIndex) => {
                  const selected = draft.selected_option === optionIndex;
                  const isAnswer = reviewMode && graded?.correct_option === optionIndex;
                  const wrongPick = reviewMode && selected && correct === false;
                  return (
                    <li key={option}>
                      <label
                        className={[
                          'flex cursor-pointer items-start gap-2.5 rounded-[var(--radius-control)]',
                          'border px-3 py-2 text-[0.8125rem] transition-colors',
                          isAnswer
                            ? 'border-[var(--signal-grounded)] bg-[var(--signal-grounded-bg)]'
                            : wrongPick
                              ? 'border-[var(--signal-danger)] bg-[var(--signal-danger-bg)]'
                              : selected
                                ? 'border-[var(--color-accent-500)] bg-[var(--surface-selected)]'
                                : 'border-[var(--border-subtle)] hover:bg-[var(--surface-hover)]',
                          disabled ? 'cursor-default' : '',
                        ].join(' ')}
                      >
                        <input
                          type="radio"
                          name={question.id}
                          className="mt-0.5 accent-[var(--color-accent-600)]"
                          checked={selected}
                          disabled={disabled}
                          onChange={() => onChange({ selected_option: optionIndex })}
                        />
                        <span className="min-w-0 flex-1 text-[var(--text-primary)]">{option}</span>
                        {isAnswer ? (
                          <span className="shrink-0 text-[0.6875rem] font-medium text-[var(--signal-grounded)]">
                            correct
                          </span>
                        ) : null}
                      </label>
                    </li>
                  );
                })}
              </ul>
            ) : question.kind === 'numeric' ? (
              <Input
                type="text"
                inputMode="decimal"
                placeholder="Your answer"
                className="max-w-48"
                value={draft.numeric_value ?? ''}
                disabled={disabled}
                onChange={(event) => onChange({ numeric_value: event.target.value })}
              />
            ) : (
              <Textarea
                rows={question.kind === 'explanation' ? 5 : 2}
                placeholder="Your answer"
                value={draft.answer_text ?? ''}
                disabled={disabled}
                onChange={(event) => onChange({ answer_text: event.target.value })}
              />
            )}
          </div>

          {reviewMode && graded ? (
            <div className="mt-3 space-y-2">
              <Callout
                tone={correct === true ? 'grounded' : correct === false ? 'danger' : 'caution'}
              >
                <p className="whitespace-pre-wrap">{graded.feedback}</p>
                <p className="mt-1 text-[0.6875rem] opacity-80">
                  Marked{' '}
                  {graded.marking_method === 'deterministic'
                    ? 'by exact comparison'
                    : graded.marking_method === 'rubric'
                      ? 'against a keyword rubric'
                      : graded.marking_method === 'llm_rubric'
                        ? 'by a model applying the rubric'
                        : 'not marked automatically'}
                  .
                </p>
              </Callout>
              {graded.explanation ? (
                <p className="text-[0.8125rem] leading-relaxed text-[var(--text-secondary)]">
                  {graded.explanation}
                </p>
              ) : null}
            </div>
          ) : null}
        </div>
      </div>
    </Card>
  );
}
