/**
 * Quizzes and mock examinations.
 *
 * One screen serves both, because the only real differences are the blueprint
 * (question count, weighting, time limit) and the label. The reader links here
 * with a page range so a checkpoint quiz covers exactly what was just read.
 */
import type { AssessmentKind, QuestionKind } from '@studypilot/shared';
import { formatRelativeTime } from '@studypilot/shared';
import {
  Badge,
  Button,
  Callout,
  Card,
  EmptyState,
  Field,
  Input,
  SectionHeading,
  Select,
  Skeleton,
} from '@studypilot/ui';
import { AlertTriangle, FileQuestion, Sparkles } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import { ErrorPanel, PageBody, PageHeader } from '../components/AppShell';
import { ProviderNotice } from '../components/ProviderNotice';
import { QuizRunner } from '../components/QuizRunner';
import {
  describeError,
  useAssessment,
  useAssessments,
  useCourses,
  useDocuments,
  useGenerateAssessment,
  useProviders,
  useSettings,
} from '../hooks/queries';

const QUESTION_KINDS: Array<{ value: QuestionKind; label: string }> = [
  { value: 'mcq', label: 'Multiple choice' },
  { value: 'short_answer', label: 'Short answer' },
  { value: 'numeric', label: 'Numerical' },
  { value: 'explanation', label: 'Explanation' },
];

export function AssessmentScreen({ kind }: { kind: AssessmentKind }) {
  const [params, setParams] = useSearchParams();
  const courses = useCourses();
  const settings = useSettings();
  const providers = useProviders();
  const generate = useGenerateAssessment();
  const history = useAssessments(params.get('course') ?? undefined, kind);

  const [courseId, setCourseId] = useState(params.get('course') ?? '');
  const [documentId, setDocumentId] = useState(params.get('document') ?? '');
  const [pageFrom, setPageFrom] = useState(params.get('from') ?? '');
  const [pageTo, setPageTo] = useState(params.get('to') ?? '');
  const [questionCount, setQuestionCount] = useState(kind === 'mock_exam' ? 15 : 5);
  const [selectedKinds, setSelectedKinds] = useState<QuestionKind[]>(['mcq', 'short_answer']);
  const [timeLimitMinutes, setTimeLimitMinutes] = useState(kind === 'mock_exam' ? 60 : 0);
  const [activeId, setActiveId] = useState<string | null>(params.get('assessment'));

  const documents = useDocuments(courseId || undefined);
  const active = useAssessment(activeId ?? undefined);

  const effectiveCourse = courseId || courses.data?.[0]?.id || '';
  const cloudAllowed = settings.data?.allow_cloud_processing ?? false;
  const activeProvider = providers.data?.llm.find((p) => p.key === providers.data?.active_llm);
  const needsCloudOptIn = Boolean(activeProvider?.sends_data_off_device);

  const run = async () => {
    if (!effectiveCourse) return;
    const assessment = await generate.mutateAsync({
      course_id: effectiveCourse,
      document_id: documentId || null,
      page_from: pageFrom ? Number.parseInt(pageFrom, 10) : null,
      page_to: pageTo ? Number.parseInt(pageTo, 10) : null,
      question_count: questionCount,
      kinds: selectedKinds.length > 0 ? selectedKinds : ['mcq'],
      kind,
      time_limit_seconds: timeLimitMinutes > 0 ? timeLimitMinutes * 60 : null,
      allow_cloud: cloudAllowed && needsCloudOptIn,
    });
    setActiveId(assessment.id);
    setParams({ assessment: assessment.id }, { replace: true });
  };

  // The reader can deep-link with ?auto=1 to generate immediately.
  const autoRan = useRef(false);
  useEffect(() => {
    if (autoRan.current) return;
    if (params.get('auto') !== '1' || !params.get('course')) return;
    autoRan.current = true;
    // Kicked off after the first paint rather than synchronously, so the form
    // renders immediately and the user sees the generating state appear.
    const handle = window.setTimeout(() => void run(), 0);
    return () => window.clearTimeout(handle);
    // Deliberately runs once on mount: this is a deep link from the reader, and
    // the ref guard makes it idempotent. Depending on `run` or `params` would
    // regenerate the quiz on every keystroke in the form above.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const label = kind === 'mock_exam' ? 'Mock Exams' : 'Quizzes';

  return (
    <>
      <PageHeader
        title={label}
        description={
          kind === 'mock_exam'
            ? 'A full paper built from your material, against a blueprint you control.'
            : 'Short checks on what you have just read. Every question traces back to a page.'
        }
      />
      <PageBody>
        <div className="space-y-4">
          <ProviderNotice compact />

          {activeId && active.data ? (
            <>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => {
                  setActiveId(null);
                  setParams({}, { replace: true });
                }}
              >
                ← Back to {label.toLowerCase()}
              </Button>
              <QuizRunner
                key={active.data.id}
                assessment={active.data}
                onRestart={() => {
                  setActiveId(null);
                  setParams({}, { replace: true });
                }}
              />
            </>
          ) : (
            <>
              <Card>
                <SectionHeading
                  title={kind === 'mock_exam' ? 'Build a paper' : 'Generate a quiz'}
                  description="Questions are generated only from material you have uploaded and indexed."
                />

                {generate.error ? (
                  <ErrorPanel
                    title="Could not generate"
                    message={describeError(generate.error)}
                    onRetry={() => void run()}
                  />
                ) : null}

                <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                  <Field label="Course">
                    <Select
                      value={effectiveCourse}
                      onChange={(event) => {
                        setCourseId(event.target.value);
                        setDocumentId('');
                      }}
                    >
                      {courses.data?.length === 0 ? <option value="">No courses</option> : null}
                      {courses.data?.map((course) => (
                        <option key={course.id} value={course.id}>
                          {course.title}
                        </option>
                      ))}
                    </Select>
                  </Field>

                  <Field label="Document" hint="Leave as all for the whole course.">
                    <Select
                      value={documentId}
                      onChange={(event) => setDocumentId(event.target.value)}
                    >
                      <option value="">All documents</option>
                      {documents.data
                        ?.filter((item) => item.status === 'indexed' || item.status === 'partial')
                        .map((item) => (
                          <option key={item.id} value={item.id}>
                            {item.title}
                          </option>
                        ))}
                    </Select>
                  </Field>

                  <Field label="Number of questions">
                    <Input
                      type="number"
                      min={1}
                      max={40}
                      value={questionCount}
                      onChange={(event) =>
                        setQuestionCount(
                          Math.max(1, Math.min(40, Number.parseInt(event.target.value, 10) || 1)),
                        )
                      }
                    />
                  </Field>

                  <Field label="From page" hint="Optional">
                    <Input
                      type="number"
                      min={1}
                      value={pageFrom}
                      onChange={(event) => setPageFrom(event.target.value)}
                    />
                  </Field>

                  <Field label="To page" hint="Optional">
                    <Input
                      type="number"
                      min={1}
                      value={pageTo}
                      onChange={(event) => setPageTo(event.target.value)}
                    />
                  </Field>

                  <Field
                    label="Time limit (minutes)"
                    hint={kind === 'mock_exam' ? 'Set 0 for untimed.' : 'Usually untimed.'}
                  >
                    <Input
                      type="number"
                      min={0}
                      max={240}
                      value={timeLimitMinutes}
                      onChange={(event) =>
                        setTimeLimitMinutes(Math.max(0, Number.parseInt(event.target.value, 10) || 0))
                      }
                    />
                  </Field>
                </div>

                <Field className="mt-3" label="Question types">
                  <div className="flex flex-wrap gap-2">
                    {QUESTION_KINDS.map((option) => {
                      const checked = selectedKinds.includes(option.value);
                      const available =
                        providers.data?.active_is_generative ||
                        option.value === 'mcq' ||
                        option.value === 'short_answer';
                      return (
                        <label
                          key={option.value}
                          title={
                            available
                              ? undefined
                              : 'Needs an AI model; only fill-in-the-blank types can be derived from your slides.'
                          }
                          className={[
                            'flex cursor-pointer items-center gap-1.5 rounded-full border px-2.5 py-1',
                            'text-[0.75rem] font-medium transition-colors',
                            !available ? 'cursor-not-allowed opacity-50' : '',
                            checked
                              ? 'border-[var(--color-accent-500)] bg-[var(--surface-selected)] text-[var(--text-primary)]'
                              : 'border-[var(--border-strong)] text-[var(--text-secondary)]',
                          ].join(' ')}
                        >
                          <input
                            type="checkbox"
                            className="accent-[var(--color-accent-600)]"
                            checked={checked}
                            disabled={!available}
                            onChange={() =>
                              setSelectedKinds((current) =>
                                current.includes(option.value)
                                  ? current.filter((value) => value !== option.value)
                                  : [...current, option.value],
                              )
                            }
                          />
                          {option.label}
                        </label>
                      );
                    })}
                  </div>
                </Field>

                {!providers.data?.active_is_generative ? (
                  <Callout tone="neutral" className="mt-3" icon={<AlertTriangle size={14} />}>
                    With no AI model configured, questions are derived mechanically from your
                    slides: a sentence with one key term removed. Useful for recall, but limited.
                  </Callout>
                ) : null}

                <div className="mt-4 flex justify-end">
                  <Button
                    variant="primary"
                    icon={<Sparkles size={15} />}
                    loading={generate.isPending}
                    disabled={!effectiveCourse}
                    onClick={() => void run()}
                  >
                    {generate.isPending
                      ? 'Generating…'
                      : kind === 'mock_exam'
                        ? 'Build the paper'
                        : 'Generate quiz'}
                  </Button>
                </div>
              </Card>

              <Card>
                <SectionHeading title={`Previous ${label.toLowerCase()}`} />
                {history.isPending ? (
                  <div className="mt-3 space-y-2">
                    {Array.from({ length: 2 }, (_, i) => (
                      <Skeleton key={i} className="h-10" />
                    ))}
                  </div>
                ) : history.data?.length === 0 ? (
                  <EmptyState
                    className="mt-3 border-0 py-6"
                    icon={<FileQuestion size={20} />}
                    title={`No ${label.toLowerCase()} yet`}
                  />
                ) : (
                  <ul className="mt-3 space-y-1.5">
                    {history.data?.map((item) => (
                      <li key={item.id}>
                        <button
                          type="button"
                          onClick={() => {
                            setActiveId(item.id);
                            setParams({ assessment: item.id }, { replace: true });
                          }}
                          className="flex w-full items-center justify-between gap-3 rounded-[var(--radius-control)] px-2.5 py-2 text-left transition-colors hover:bg-[var(--surface-hover)]"
                        >
                          <span className="min-w-0">
                            <span className="block truncate text-[0.8125rem] font-medium text-[var(--text-primary)]">
                              {item.title}
                            </span>
                            <span className="text-[0.75rem] text-[var(--text-secondary)]">
                              {item.total_marks} marks · {formatRelativeTime(item.created_at)}
                            </span>
                          </span>
                          <Badge tone="neutral">
                            {item.generator.startsWith('llm') ? 'AI generated' : 'From slides'}
                          </Badge>
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </Card>
            </>
          )}
        </div>
      </PageBody>
    </>
  );
}
