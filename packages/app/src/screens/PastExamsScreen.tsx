/**
 * A course's past exam papers, and what they say about the next exam.
 *
 * Papers are added here (or an existing file is marked as one), then
 * analysed: the paper's fixed format, the mix of question forms, and a ranked
 * forecast of topics with the evidence for each. From then on quizzes, mock
 * exams and flashcards for the course follow that analysis.
 *
 * The forecast is shown as what it is: likelihoods drawn from the papers
 * supplied, with the question references behind each one, and a plain
 * statement of how many papers it rests on. It is never worded as a promise.
 */
import type { ExamProfileBody, ExamTopicForecast } from '@studypilot/shared';
import {
  Badge,
  Button,
  Callout,
  Card,
  EmptyState,
  ProgressBar,
  SectionHeading,
  Select,
  Skeleton,
} from '@studypilot/ui';
import {
  AlertTriangle,
  BookMarked,
  BookOpen,
  FileQuestion,
  FileText,
  GraduationCap,
  RefreshCw,
  Sparkles,
  Target,
  Upload,
  X,
} from 'lucide-react';
import { useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';

import { ErrorPanel, PageBody, PageHeader } from '../components/AppShell';
import {
  describeError,
  useAnalysePastPapers,
  useCourse,
  useDocuments,
  useExamProfile,
  useProviders,
  useSetDocumentKind,
  useSettings,
  useUploadDocument,
} from '../hooks/queries';

const FORM_LABELS: Record<string, string> = {
  mcq: 'Multiple choice',
  short_answer: 'Short answer',
  numeric: 'Calculation',
  explanation: 'Long written answer',
};

const CONFIDENCE: Record<string, { tone: 'grounded' | 'caution' | 'neutral'; label: string }> = {
  high: { tone: 'grounded', label: 'Good evidence' },
  medium: { tone: 'neutral', label: 'Some evidence' },
  low: { tone: 'caution', label: 'Thin evidence' },
};

export function PastExamsScreen() {
  const { courseId } = useParams<{ courseId: string }>();
  const id = courseId ?? '';
  const course = useCourse(courseId);
  const exam = useExamProfile(courseId);
  const documents = useDocuments(courseId);
  const settings = useSettings();
  const providers = useProviders();
  const analyse = useAnalysePastPapers(id);
  const upload = useUploadDocument();
  const setKind = useSetDocumentKind();

  const inputRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState<{ done: number; total: number } | null>(null);
  const [failures, setFailures] = useState<string[]>([]);
  const [existing, setExisting] = useState('');

  const activeProvider = providers.data?.llm.find((p) => p.key === providers.data?.active_llm);
  const allowCloud =
    Boolean(settings.data?.allow_cloud_processing) &&
    Boolean(activeProvider?.sends_data_off_device);

  const papers = exam.data?.papers ?? [];
  const paperIds = new Set(papers.map((paper) => paper.document_id));
  const others = (documents.data ?? []).filter(
    (document) => !paperIds.has(document.id) && document.page_count > 0,
  );

  const addFiles = async (files: File[]) => {
    setFailures([]);
    setUploading({ done: 0, total: files.length });
    const failed: string[] = [];
    for (const [index, file] of files.entries()) {
      try {
        await upload.mutateAsync({ courseId: id, file, kind: 'past_paper', allowCloud });
      } catch (error) {
        failed.push(`${file.name}: ${describeError(error)}`);
      }
      setUploading({ done: index + 1, total: files.length });
    }
    setUploading(null);
    setFailures(failed);
    void exam.refetch();
  };

  const body = exam.data?.profile ?? null;

  return (
    <>
      <PageHeader
        title="Past exam papers"
        description={`${course.data?.title ?? 'This course'}: add old papers and their solutions, and practice follows the real exam.`}
        actions={
          <>
            <input
              ref={inputRef}
              type="file"
              multiple
              accept=".pdf,.pptx,.ppt,.png,.jpg,.jpeg,.webp"
              className="hidden"
              aria-label="Past exam papers to add"
              onChange={(event) => {
                const files = [...(event.target.files ?? [])];
                event.target.value = '';
                if (files.length > 0) void addFiles(files);
              }}
            />
            <Button
              variant="primary"
              icon={<Upload size={15} />}
              loading={uploading !== null}
              onClick={() => inputRef.current?.click()}
            >
              {uploading
                ? `Adding ${uploading.done + 1} of ${uploading.total}`
                : 'Add past papers and solutions'}
            </Button>
          </>
        }
      />
      <PageBody>
        <Link
          to={`/courses/${id}`}
          className="mb-3 inline-block text-[0.75rem] font-medium text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
        >
          ← Back to the course
        </Link>

        {exam.isError ? (
          <ErrorPanel message={describeError(exam.error)} onRetry={() => void exam.refetch()} />
        ) : exam.isPending ? (
          <div className="space-y-3">
            <Skeleton className="h-24" />
            <Skeleton className="h-48" />
          </div>
        ) : (
          <div className="space-y-4">
            {failures.length > 0 ? (
              <Callout tone="danger" icon={<AlertTriangle size={14} />} title="Some files were not added">
                <ul className="list-disc pl-4">
                  {failures.map((failure) => (
                    <li key={failure}>{failure}</li>
                  ))}
                </ul>
              </Callout>
            ) : null}

            <Card>
              <SectionHeading
                title={`Papers in this course (${papers.length})`}
                description="Question papers and their solutions both count. More years give a better forecast."
              />
              {papers.length === 0 ? (
                <p className="mt-2 text-[0.8125rem] text-[var(--text-secondary)]">
                  None yet. Add them with the button above, or mark a file already in the course
                  below.
                </p>
              ) : (
                <ul className="mt-2 divide-y divide-[var(--border-subtle)]">
                  {papers.map((paper) => (
                    <li key={paper.document_id} className="flex items-center gap-2 py-2">
                      <FileText size={14} className="shrink-0 text-[var(--text-secondary)]" />
                      <Link
                        to={`/read/${paper.document_id}`}
                        className="min-w-0 flex-1 truncate text-[0.8125rem] font-medium text-[var(--text-primary)] hover:underline"
                      >
                        {paper.title}
                      </Link>
                      <span className="shrink-0 text-[0.75rem] tabular-nums text-[var(--text-secondary)]">
                        {paper.page_count} page{paper.page_count === 1 ? '' : 's'}
                      </span>
                      <Button
                        size="sm"
                        variant="ghost"
                        icon={<X size={12} />}
                        title="Keep the file in the course, but stop treating it as a past paper"
                        onClick={() => setKind.mutate({ id: paper.document_id, kind: 'lecture' })}
                      >
                        Not a past paper
                      </Button>
                    </li>
                  ))}
                </ul>
              )}
              {others.length > 0 ? (
                <div className="mt-3 flex flex-wrap items-end gap-2 border-t border-[var(--border-subtle)] pt-3">
                  <label className="min-w-[14rem] flex-1 text-[0.75rem] text-[var(--text-secondary)]">
                    Already in the course? Mark a file as a past paper
                    <Select
                      className="mt-1"
                      value={existing}
                      onChange={(event) => setExisting(event.target.value)}
                    >
                      <option value="">Choose a file…</option>
                      {others.map((document) => (
                        <option key={document.id} value={document.id}>
                          {document.title}
                        </option>
                      ))}
                    </Select>
                  </label>
                  <Button
                    disabled={!existing}
                    loading={setKind.isPending}
                    onClick={() =>
                      setKind.mutate(
                        { id: existing, kind: 'past_paper' },
                        { onSuccess: () => setExisting('') },
                      )
                    }
                  >
                    Mark as past paper
                  </Button>
                </div>
              ) : null}
            </Card>

            {papers.length === 0 ? (
              <EmptyState
                icon={<GraduationCap size={22} />}
                title="No forecast without papers"
                description="Add at least one past paper. With two or more years, StudyPilot can see what repeats, what rotates and what is due."
              />
            ) : (
              <Card>
                <div className="flex flex-wrap items-start gap-3">
                  <div className="min-w-0 flex-1">
                    <SectionHeading
                      title="What the papers say about the next exam"
                      description={
                        body
                          ? `Drawn from ${papers.length} paper${papers.length === 1 ? '' : 's'}. A forecast with its evidence, not a promise.`
                          : 'Every question is inventoried, counted and compared with what is taught this year.'
                      }
                    />
                  </div>
                  <Button
                    variant={body && !exam.data?.stale ? 'secondary' : 'primary'}
                    icon={body ? <RefreshCw size={14} /> : <Sparkles size={14} />}
                    loading={analyse.isPending}
                    onClick={() => analyse.mutate({ allowCloud })}
                  >
                    {analyse.isPending
                      ? 'Reading the papers…'
                      : body
                        ? 'Analyse again'
                        : 'Analyse and forecast'}
                  </Button>
                </div>

                {analyse.isPending ? (
                  <p className="mt-2 text-[0.75rem] text-[var(--text-secondary)]">
                    This reads every paper in full and can take a minute or two.
                  </p>
                ) : null}
                {analyse.error ? (
                  <div className="mt-3"><ErrorPanel message={describeError(analyse.error)} /></div>
                ) : null}
                {(analyse.data?.warnings ?? []).map((warning) => (
                  <Callout key={warning} className="mt-3" tone="caution" icon={<AlertTriangle size={14} />}>
                    {warning}
                  </Callout>
                ))}
                {exam.data?.stale ? (
                  <Callout className="mt-3" tone="caution" icon={<AlertTriangle size={14} />}>
                    The papers have changed since this was worked out. Analyse again to include
                    them.
                  </Callout>
                ) : null}

                {body ? (
                  <ProfileView
                    body={body}
                    counted={exam.data?.generated_by === 'counted'}
                    model={exam.data?.model ?? ''}
                  />
                ) : null}
              </Card>
            )}

            {body ? (
              <Card>
                <SectionHeading
                  title="Practise for this exam"
                  description="Each follows the forecast: the real exam's question forms and marks, likeliest topics first, new questions every time. Everything you generate is kept under its screen to revisit."
                />
                <div className="mt-3 flex flex-wrap gap-2">
                  <Link to={`/exams?course=${id}`}>
                    <Button variant="primary" icon={<BookOpen size={15} />}>
                      Mock exam
                    </Button>
                  </Link>
                  <Link to={`/quizzes?course=${id}`}>
                    <Button icon={<FileQuestion size={15} />}>Quiz</Button>
                  </Link>
                  <Link to={`/flashcards?course=${id}`}>
                    <Button icon={<BookMarked size={15} />}>Flashcards</Button>
                  </Link>
                </div>
              </Card>
            ) : null}
          </div>
        )}
      </PageBody>
    </>
  );
}

function ProfileView({
  body,
  counted,
  model,
}: {
  body: ExamProfileBody;
  counted: boolean;
  model: string;
}) {
  const confidence = CONFIDENCE[body.confidence] ?? CONFIDENCE.low;
  const mix = Object.entries(body.question_mix)
    .filter(([, share]) => share > 0.005)
    .sort((a, b) => b[1] - a[1]);

  return (
    <div className="mt-4 space-y-5">
      <section>
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-[0.8125rem] font-semibold text-[var(--text-primary)]">
            The paper's format
          </h3>
          <Badge tone={confidence?.tone}>{confidence?.label}</Badge>
          {body.format.duration_minutes > 0 ? (
            <Badge tone="neutral">{body.format.duration_minutes} minutes</Badge>
          ) : null}
          {body.format.total_marks > 0 ? (
            <Badge tone="neutral">{body.format.total_marks} marks</Badge>
          ) : null}
        </div>
        {body.format.summary ? (
          <p className="mt-1.5 text-[0.8125rem] leading-relaxed text-[var(--text-primary)]">
            {body.format.summary}
          </p>
        ) : null}
        {body.format.sections.length > 0 ? (
          <div className="sp-prose mt-2">
            <div className="sp-table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Section</th>
                    <th style={{ textAlign: 'right' }}>Questions</th>
                    <th style={{ textAlign: 'right' }}>Marks each</th>
                    <th>Form</th>
                    <th>Instructions</th>
                  </tr>
                </thead>
                <tbody>
                  {body.format.sections.map((section, index) => (
                    <tr key={`${index}-${section.name}`}>
                      <td>{section.name || `Section ${index + 1}`}</td>
                      <td style={{ textAlign: 'right' }}>{section.questions || '—'}</td>
                      <td style={{ textAlign: 'right' }}>{section.marks_each || '—'}</td>
                      <td>{FORM_LABELS[section.form] ?? '—'}</td>
                      <td>{section.instructions || '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ) : null}

        <ul className="mt-3 grid gap-x-6 gap-y-2 sm:grid-cols-2">
          {mix.map(([form, share]) => (
            <li key={form}>
              <p className="mb-1 flex items-baseline justify-between text-[0.75rem] text-[var(--text-secondary)]">
                <span>{FORM_LABELS[form] ?? form}</span>
                <strong className="font-semibold tabular-nums text-[var(--text-primary)]">
                  {Math.round(share * 100)}%
                </strong>
              </p>
              <ProgressBar value={share} tone="neutral" label={`${FORM_LABELS[form] ?? form} share`} />
            </li>
          ))}
        </ul>
        {body.command_words.length > 0 ? (
          <p className="mt-3 text-[0.75rem] text-[var(--text-secondary)]">
            The examiner's words: {body.command_words.join(', ')}.
          </p>
        ) : null}
      </section>

      {body.topics.length > 0 ? (
        <section>
          <h3 className="flex items-center gap-1.5 text-[0.8125rem] font-semibold text-[var(--text-primary)]">
            <Target size={14} className="text-[var(--text-accent)]" />
            Topics, by how likely they are to come up next
          </h3>
          <ol className="mt-2 space-y-2">
            {body.topics.map((topic, index) => (
              <TopicRow key={`${index}-${topic.topic}`} topic={topic} rank={index + 1} />
            ))}
          </ol>
        </section>
      ) : null}

      <div className="grid gap-4 md:grid-cols-3">
        <TextList title="Patterns across the papers" items={body.patterns} />
        <TextList title="What earns marks" items={body.marking_habits} />
        <TextList title="How to spend your revision" items={body.advice} />
      </div>

      <p className="text-[0.6875rem] leading-relaxed text-[var(--text-muted)]">
        {counted
          ? 'Counted from the text of the papers; no AI model read them.'
          : `Analysed by ${model || 'an AI model'}.`}{' '}
        {body.caveat}
      </p>
    </div>
  );
}

function TopicRow({ topic, rank }: { topic: ExamTopicForecast; rank: number }) {
  const percent = Math.round(topic.likelihood * 100);
  return (
    <li className="rounded-[var(--radius-card)] border border-[var(--border-subtle)] bg-[var(--surface-page)] p-3">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="text-[0.75rem] tabular-nums text-[var(--text-muted)]">{rank}</span>
        <p className="min-w-0 flex-1 text-[0.875rem] font-semibold text-[var(--text-primary)]">
          {topic.topic}
        </p>
        {topic.typical_marks > 0 ? (
          <span className="text-[0.75rem] tabular-nums text-[var(--text-secondary)]">
            about {topic.typical_marks} marks
          </span>
        ) : null}
        {topic.form ? <Badge tone="neutral">{FORM_LABELS[topic.form] ?? topic.form}</Badge> : null}
        <div className="flex w-40 items-center gap-2">
          <ProgressBar value={topic.likelihood} label={`Likelihood of ${topic.topic}`} />
          <strong className="w-9 text-right text-[0.8125rem] font-semibold tabular-nums text-[var(--text-primary)]">
            {percent}%
          </strong>
        </div>
      </div>
      {topic.pattern || topic.appearances.length > 0 ? (
        <p className="mt-1.5 text-[0.75rem] text-[var(--text-secondary)]">
          {topic.pattern ? `${topic.pattern[0]?.toUpperCase()}${topic.pattern.slice(1)}. ` : ''}
          {topic.appearances.length > 0 ? `Seen in ${topic.appearances.join(', ')}.` : ''}
        </p>
      ) : null}
      {topic.rationale ? (
        <p className="mt-1 text-[0.8125rem] leading-relaxed text-[var(--text-primary)]">
          {topic.rationale}
        </p>
      ) : null}
      {topic.next_variation ? (
        <p className="mt-1 text-[0.8125rem] leading-relaxed text-[var(--text-primary)]">
          <span className="font-semibold">Likely next: </span>
          {topic.next_variation}
        </p>
      ) : null}
    </li>
  );
}

function TextList({ title, items }: { title: string; items: string[] }) {
  if (items.length === 0) return null;
  return (
    <section>
      <h3 className="text-[0.8125rem] font-semibold text-[var(--text-primary)]">{title}</h3>
      <ul className="mt-1.5 list-disc space-y-1 pl-4 text-[0.8125rem] leading-relaxed text-[var(--text-primary)]">
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </section>
  );
}
