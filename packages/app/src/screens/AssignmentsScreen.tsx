/**
 * Assignments for one course: upload a brief, draft a guide, approve steps.
 *
 * The guide is a plan, not an answer. Each step says what to do and points at
 * the lecture material that shows how. A step the material does not cover is
 * held back with a disclosure until the student approves it — that is the
 * contract the planner enforces, and this screen's job is to make the two
 * kinds of step look as different as they are.
 */
import type {
  Assignment,
  AssignmentPlan,
  AttemptReview,
  Citation,
  PlanCitation,
  PlanStep,
} from '@studypilot/shared';
import {
  Badge,
  Button,
  Callout,
  Card,
  EmptyState,
  SectionHeading,
  Skeleton,
  Textarea,
  type Tone,
} from '@studypilot/ui';
import {
  AlertTriangle,
  BookOpenCheck,
  Check,
  ClipboardList,
  FileAudio,
  FileImage,
  FileText,
  Lightbulb,
  MessageSquareText,
  Presentation,
  Sparkles,
  Upload,
  X,
} from 'lucide-react';
import { useId, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';

import { ErrorPanel, PageBody, PageHeader } from '../components/AppShell';
import { GroundedAnswer } from '../components/GroundedAnswer';
import {
  describeError,
  useAssignments,
  useAttemptReviews,
  useCheckAttempt,
  useCourse,
  useDraftAssignmentPlan,
  useSetStepApproval,
  useSettings,
  useUploadDocument,
} from '../hooks/queries';

const ACCEPT = [
  'application/pdf',
  '.pdf',
  'application/vnd.openxmlformats-officedocument.presentationml.presentation',
  '.pptx',
  'image/png',
  'image/jpeg',
  'image/webp',
  '.png',
  '.jpg',
  '.jpeg',
  '.webp',
  'audio/wav',
  'audio/x-wav',
  'audio/mpeg',
  'audio/mp4',
  'audio/m4a',
  '.wav',
  '.mp3',
  '.m4a',
].join(',');

export function AssignmentsScreen() {
  const { courseId = '' } = useParams();
  const course = useCourse(courseId);
  const assignments = useAssignments(courseId);
  const [selected, setSelected] = useState<string | null>(null);

  const current =
    assignments.data?.find((a) => a.document.id === selected) ?? assignments.data?.[0] ?? null;

  return (
    <>
      <PageHeader
        title="Assignments"
        description={
          course.data
            ? `${course.data.title} — upload a brief and get a step-by-step guide grounded in this course's lectures.`
            : 'Upload a brief and get a step-by-step guide grounded in your lectures.'
        }
        actions={
          <Link to={`/library?course=${courseId}`}>
            <Button variant="ghost" icon={<FileText size={15} />}>
              Lecture library
            </Button>
          </Link>
        }
      />
      <PageBody>
        {assignments.isError ? (
          <ErrorPanel
            title="Could not load assignments"
            message={describeError(assignments.error)}
            onRetry={() => void assignments.refetch()}
          />
        ) : null}

        <div className="grid gap-4 lg:grid-cols-[280px_1fr]">
          <div className="space-y-3">
            <UploadBrief courseId={courseId} onUploaded={(id) => setSelected(id)} />
            {assignments.isPending ? (
              <Skeleton className="h-24" />
            ) : assignments.data && assignments.data.length > 0 ? (
              <Card>
                <SectionHeading title="Briefs" />
                <ul className="mt-2 space-y-1">
                  {assignments.data.map((a) => (
                    <li key={a.document.id}>
                      <button
                        type="button"
                        onClick={() => setSelected(a.document.id)}
                        className={[
                          'flex w-full items-center gap-2 rounded-[var(--radius-control)] px-2 py-1.5 text-left text-[0.8125rem]',
                          current?.document.id === a.document.id
                            ? 'bg-[var(--surface-raised)] text-[var(--text-primary)]'
                            : 'text-[var(--text-secondary)] hover:bg-[var(--surface-raised)]',
                        ].join(' ')}
                      >
                        <BriefIcon mediaType={a.document.media_type} />
                        <span className="min-w-0 flex-1 truncate">{a.document.title}</span>
                        {a.plan ? <PlanBadge plan={a.plan} /> : null}
                      </button>
                    </li>
                  ))}
                </ul>
              </Card>
            ) : null}
          </div>

          <div>
            {assignments.isPending ? (
              <Skeleton className="h-64" />
            ) : !current ? (
              <EmptyState
                icon={<ClipboardList size={22} />}
                title="No assignment briefs yet"
                description="Upload the brief — a PDF, a photo of it, or a recording of the lecturer explaining it — and StudyPilot will draft a guide that points to your lecture slides at every step."
              />
            ) : (
              <AssignmentDetail key={current.document.id} courseId={courseId} assignment={current} />
            )}
          </div>
        </div>
      </PageBody>
    </>
  );
}

function BriefIcon({ mediaType }: { mediaType: string }) {
  if (mediaType.includes('presentationml')) return <Presentation size={14} className="shrink-0" />;
  if (mediaType.startsWith('image/')) return <FileImage size={14} className="shrink-0" />;
  if (mediaType.startsWith('audio/')) return <FileAudio size={14} className="shrink-0" />;
  return <FileText size={14} className="shrink-0" />;
}

function PlanBadge({ plan }: { plan: AssignmentPlan }) {
  const pending = plan.steps.filter((s) => s.approval === 'pending').length;
  if (plan.status === 'failed') return <Badge tone="danger">failed</Badge>;
  if (pending > 0) return <Badge tone="caution">{pending} to approve</Badge>;
  return <Badge tone="grounded">guide ready</Badge>;
}

// --- upload ------------------------------------------------------------------
function UploadBrief({ courseId, onUploaded }: { courseId: string; onUploaded: (id: string) => void }) {
  const upload = useUploadDocument();
  const settings = useSettings();
  const allowCloud = Boolean(settings.data?.allow_cloud_processing);
  const inputId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);

  return (
    <Card>
      <SectionHeading
        title="Add a brief"
        description="PDF, a photo of the brief, or a recording of it being explained."
      />
      <label
        htmlFor={inputId}
        className="mt-3 block cursor-pointer rounded-[var(--radius-card)] border border-dashed border-[var(--border-strong)] bg-[var(--surface-sunken)] p-4 text-center"
      >
        <input
          id={inputId}
          ref={inputRef}
          type="file"
          accept={ACCEPT}
          className="sr-only"
          onChange={(event) => setFile(event.target.files?.[0] ?? null)}
        />
        <Upload size={18} className="mx-auto text-[var(--text-muted)]" />
        <p className="mt-1.5 text-[0.75rem] text-[var(--text-secondary)]">
          {file ? file.name : 'Choose the assignment file'}
        </p>
      </label>
      {upload.error ? (
        <Callout tone="danger" className="mt-3" icon={<AlertTriangle size={14} />}>
          {describeError(upload.error)}
        </Callout>
      ) : null}
      {upload.data?.warnings.length ? (
        <Callout tone="caution" className="mt-3">
          {upload.data.warnings.join(' ')}
        </Callout>
      ) : null}
      <Button
        variant="primary"
        className="mt-3 w-full"
        loading={upload.isPending}
        disabled={!file}
        onClick={async () => {
          if (!file) return;
          try {
            const result = await upload.mutateAsync({
              courseId,
              file,
              kind: 'assignment',
              allowCloud,
            });
            setFile(null);
            if (inputRef.current) inputRef.current.value = '';
            onUploaded(result.document.id);
          } catch {
            // Shown above via upload.error.
          }
        }}
      >
        {upload.isPending ? 'Reading…' : 'Upload brief'}
      </Button>
    </Card>
  );
}

// --- detail ------------------------------------------------------------------
function AssignmentDetail({ courseId, assignment }: { courseId: string; assignment: Assignment }) {
  const settings = useSettings();
  const draft = useDraftAssignmentPlan(courseId);
  const plan = assignment.plan;
  const allowCloud = Boolean(settings.data?.allow_cloud_processing);
  const readable = assignment.document.status === 'indexed' || assignment.document.status === 'partial';

  return (
    <div className="space-y-4">
      <Card>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <SectionHeading
            title={assignment.document.title}
            description={`${assignment.document.original_filename} · ${assignment.document.page_count} page${assignment.document.page_count === 1 ? '' : 's'}`}
          />
          <div className="flex gap-2">
            {readable ? (
              <Link to={`/read/${assignment.document.id}`}>
                <Button size="sm" variant="ghost">
                  Open brief
                </Button>
              </Link>
            ) : null}
            <Button
              size="sm"
              variant="primary"
              icon={<Sparkles size={14} />}
              loading={draft.isPending}
              disabled={!readable}
              onClick={() =>
                draft.mutate({ documentId: assignment.document.id, allowCloud })
              }
            >
              {plan ? 'Draft again' : 'Draft the guide'}
            </Button>
          </div>
        </div>

        {!readable ? (
          <Callout tone="caution" className="mt-3" icon={<AlertTriangle size={14} />}>
            This brief could not be read ({assignment.document.status}).{' '}
            {assignment.document.status_detail}
          </Callout>
        ) : null}
        {draft.isPending ? (
          <Callout tone="neutral" className="mt-3">
            Reading the brief, finding the lecture material for each requirement, and drafting
            the steps. With a cloud model this takes ten to forty seconds.
          </Callout>
        ) : null}
        {draft.error ? (
          <Callout tone="danger" className="mt-3" icon={<AlertTriangle size={14} />}>
            {describeError(draft.error)}
          </Callout>
        ) : null}
        {!allowCloud && !plan ? (
          <p className="mt-3 text-[0.75rem] text-[var(--text-muted)]">
            Cloud processing is off in Settings, so the guide will be drafted without a language
            model: the brief's requirements with matching lecture passages, not written steps.
          </p>
        ) : null}
      </Card>

      {plan ? <PlanView courseId={courseId} plan={plan} /> : null}
    </div>
  );
}

function PlanView({ courseId, plan }: { courseId: string; plan: AssignmentPlan }) {
  const reviews = useAttemptReviews(plan.id);
  const pending = plan.steps.filter((s) => s.approval === 'pending');
  const grounded = plan.steps.filter((s) => s.grounding === 'slides').length;

  return (
    <>
      {plan.warnings.length > 0 ? (
        <Callout tone={plan.generative ? 'caution' : 'neutral'} icon={<AlertTriangle size={14} />}>
          <ul className="space-y-1">
            {plan.warnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </Callout>
      ) : null}

      <Card>
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={plan.generative ? 'accent' : 'neutral'}>
            {plan.generative ? `drafted by ${plan.provider}` : 'no model: requirements only'}
          </Badge>
          <Badge tone="grounded">
            {grounded} of {plan.steps.length} steps grounded in your slides
          </Badge>
          {pending.length > 0 ? (
            <Badge tone="caution">{pending.length} waiting for your approval</Badge>
          ) : null}
        </div>
        {plan.summary ? (
          <p className="mt-3 text-[0.8125rem] leading-relaxed text-[var(--text-secondary)]">
            {plan.summary}
          </p>
        ) : null}
        {plan.requirements.length > 0 ? (
          <details className="mt-3">
            <summary className="cursor-pointer text-[0.75rem] font-medium text-[var(--text-secondary)]">
              What the brief requires ({plan.requirements.length})
            </summary>
            <ul className="mt-2 space-y-1.5 text-[0.8125rem]">
              {plan.requirements.map((r) => (
                <li key={r.id} className="flex gap-2">
                  <Badge tone="neutral">{r.kind}</Badge>
                  <span className="text-[var(--text-primary)]">{r.text}</span>
                </li>
              ))}
            </ul>
          </details>
        ) : null}
      </Card>

      {pending.length > 0 ? (
        <Callout tone="caution" icon={<AlertTriangle size={14} />}>
          {pending.length === 1 ? 'One step goes' : `${pending.length} steps go`} beyond what your
          lecture slides cover. Each one says what is missing. Approve it only if your assignment
          allows methods beyond the taught material — or decline it and work from the grounded
          steps.
        </Callout>
      ) : null}

      <ol className="space-y-3">
        {plan.steps.map((step) => (
          <StepCard
            key={step.index}
            courseId={courseId}
            plan={plan}
            step={step}
            reviews={(reviews.data ?? []).filter((r) => r.step_index === step.index)}
          />
        ))}
      </ol>
    </>
  );
}

function StepCard({
  courseId,
  plan,
  step,
  reviews,
}: {
  courseId: string;
  plan: AssignmentPlan;
  step: PlanStep;
  reviews: AttemptReview[];
}) {
  const approve = useSetStepApproval(courseId);
  const navigate = useNavigate();
  const beyond = step.grounding === 'beyond';
  const declined = step.approval === 'declined';

  return (
    <li>
      <Card
        className={[
          beyond && step.approval === 'pending' ? 'border-[var(--signal-warning)]/50' : '',
          declined ? 'opacity-60' : '',
        ].join(' ')}
      >
        <div className="flex items-start gap-3">
          <span
            className={[
              'flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-[0.75rem] font-semibold',
              beyond
                ? 'bg-[var(--signal-warning-bg)] text-[var(--signal-warning)]'
                : 'bg-[var(--surface-raised)] text-[var(--text-accent)]',
            ].join(' ')}
          >
            {step.index}
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="text-[0.9375rem] font-semibold text-[var(--text-primary)]">
                {step.title}
              </h3>
              {beyond ? (
                <Badge tone="caution">not in your slides</Badge>
              ) : (
                <Badge tone="grounded">
                  <BookOpenCheck size={12} className="mr-1 inline" />
                  grounded in your slides
                </Badge>
              )}
              {step.approval === 'approved' ? <Badge tone="accent">approved by you</Badge> : null}
              {declined ? <Badge tone="neutral">declined</Badge> : null}
            </div>

            {/* Same renderer as the tutor: Markdown, KaTeX for the size
                expressions the model writes, and [n] markers as clickable
                citations that open the reader at the page. */}
            <GroundedAnswer
              className="mt-2 text-[0.8125rem]"
              markdown={step.instruction}
              citations={asCitations(step.citations)}
              onCitationClick={(c) => navigate(`/read/${c.document_id}?page=${c.page_number}`)}
            />
            {step.why ? (
              <p className="mt-1.5 text-[0.75rem] text-[var(--text-secondary)]">
                <span className="font-medium">Why:</span> {step.why}
              </p>
            ) : null}

            {step.citations.length > 0 ? (
              <ul className="mt-2 flex flex-wrap gap-1.5">
                {step.citations.map((c) => (
                  <li key={`${c.chunk_id}-${c.marker}`}>
                    <CitationChip
                      citation={c}
                      onOpen={() => navigate(`/read/${c.document_id}?page=${c.page_number}`)}
                    />
                  </li>
                ))}
              </ul>
            ) : null}

            {beyond ? (
              <Callout tone="caution" className="mt-3" icon={<AlertTriangle size={14} />}>
                {step.disclosure}
              </Callout>
            ) : null}

            {beyond && step.approval === 'pending' ? (
              <div className="mt-3 flex gap-2">
                <Button
                  size="sm"
                  variant="primary"
                  icon={<Check size={14} />}
                  loading={approve.isPending}
                  onClick={() =>
                    approve.mutate({ planId: plan.id, stepIndex: step.index, approval: 'approved' })
                  }
                >
                  Approve this step
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  icon={<X size={14} />}
                  loading={approve.isPending}
                  onClick={() =>
                    approve.mutate({ planId: plan.id, stepIndex: step.index, approval: 'declined' })
                  }
                >
                  Decline
                </Button>
              </div>
            ) : null}
            {approve.error ? (
              <p className="mt-2 text-[0.75rem] text-[var(--signal-danger)]">
                {describeError(approve.error)}
              </p>
            ) : null}

            {!declined ? <AttemptChecker plan={plan} step={step} reviews={reviews} /> : null}
          </div>
        </div>
      </Card>
    </li>
  );
}

const VERDICT_TONE: Record<AttemptReview['verdict'], Tone> = {
  on_track: 'grounded',
  needs_work: 'caution',
  off_track: 'danger',
};

const VERDICT_TEXT: Record<AttemptReview['verdict'], string> = {
  on_track: 'on track',
  needs_work: 'needs work',
  off_track: 'off track',
};

const HINT_LEVEL_TEXT = [
  '',
  'the concept to think about',
  'the rule or formula to look at',
  'an outline of the approach',
  'a similar worked example',
  'your logic, line by line',
];

/**
 * "Check my attempt": the student pastes what they have done; the coach says
 * what is wrong and why, cites the slide, and gives one hint. Each ask about
 * the same step raises the hint one level — decided by the server, shown here.
 */
function AttemptChecker({
  plan,
  step,
  reviews,
}: {
  plan: AssignmentPlan;
  step: PlanStep;
  reviews: AttemptReview[];
}) {
  const settings = useSettings();
  const allowCloud = Boolean(settings.data?.allow_cloud_processing);
  const check = useCheckAttempt(plan.id);
  const navigate = useNavigate();
  const [open, setOpen] = useState(reviews.length > 0);
  const [text, setText] = useState('');
  const textareaId = useId();
  const nextLevel = Math.min(5, reviews.length + 1);
  const openReader = (c: Citation) => navigate(`/read/${c.document_id}?page=${c.page_number}`);

  if (!open) {
    return (
      <div className="mt-3">
        <Button
          size="sm"
          variant="ghost"
          icon={<MessageSquareText size={14} />}
          onClick={() => setOpen(true)}
        >
          Check my attempt
        </Button>
      </div>
    );
  }

  return (
    <div className="mt-3 rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-sunken)] p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-[0.8125rem] font-medium text-[var(--text-primary)]">
          Check my attempt
          <span className="ml-2 font-normal text-[var(--text-muted)]">
            next hint: {HINT_LEVEL_TEXT[nextLevel]}
          </span>
        </p>
        <Button size="sm" variant="ghost" onClick={() => setOpen(false)}>
          Hide
        </Button>
      </div>
      <p className="mt-1 text-[0.75rem] text-[var(--text-muted)]">
        Paste what you have done for this step. You get what is wrong and why, with the slide
        that shows it, and one hint — never the answer. Your attempt stays in your own data.
      </p>

      {reviews.length > 0 ? (
        <ol className="mt-3 space-y-3">
          {reviews.map((review) => (
            <li
              key={review.id}
              className="rounded-md border border-[var(--border-subtle)] bg-[var(--surface-base)] p-3"
            >
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone={VERDICT_TONE[review.verdict]}>{VERDICT_TEXT[review.verdict]}</Badge>
                <Badge tone="neutral">hint level {review.hint_level}</Badge>
                <span className="text-[0.6875rem] text-[var(--text-muted)]">
                  {new Date(review.created_at).toLocaleString()} · {review.provider}
                </span>
              </div>
              <details className="mt-2">
                <summary className="cursor-pointer text-[0.75rem] text-[var(--text-secondary)]">
                  Your attempt ({review.attempt_text.length} characters)
                </summary>
                <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap rounded-md bg-[var(--surface-sunken)] p-2 text-[0.75rem]">
                  {review.attempt_text}
                </pre>
              </details>
              {review.summary ? (
                <GroundedAnswer
                  className="mt-2 text-[0.8125rem]"
                  markdown={review.summary}
                  citations={asCitations(review.citations)}
                  onCitationClick={openReader}
                />
              ) : null}
              {review.issues.length > 0 ? (
                <ul className="mt-2 space-y-1.5">
                  {review.issues.map((issue, i) => (
                    <li key={i} className="text-[0.8125rem]">
                      <GroundedAnswer
                        markdown={`**${issue.what}** — ${issue.why}`}
                        citations={asCitations(review.citations)}
                        onCitationClick={openReader}
                      />
                    </li>
                  ))}
                </ul>
              ) : null}
              {review.next_hint ? (
                <Callout tone="info" className="mt-2" icon={<Lightbulb size={14} />}>
                  <GroundedAnswer
                    markdown={review.next_hint}
                    citations={asCitations(review.citations)}
                    onCitationClick={openReader}
                  />
                </Callout>
              ) : null}
              {review.warnings.length > 0 ? (
                <ul className="mt-2 space-y-0.5 text-[0.6875rem] text-[var(--text-muted)]">
                  {review.warnings.map((w, i) => (
                    <li key={i}>{w}</li>
                  ))}
                </ul>
              ) : null}
            </li>
          ))}
        </ol>
      ) : null}

      <form
        className="mt-3"
        onSubmit={(event) => {
          event.preventDefault();
          if (!text.trim()) return;
          check.mutate({ stepIndex: step.index, text, allowCloud }, { onSuccess: () => setText('') });
        }}
      >
        <label htmlFor={textareaId} className="sr-only">
          Your attempt at step {step.index}
        </label>
        <Textarea
          id={textareaId}
          value={text}
          onChange={(event) => setText(event.target.value)}
          rows={5}
          placeholder="What have you tried for this step? Paste your reasoning, code or working."
        />
        <div className="mt-2 flex items-center gap-2">
          <Button
            type="submit"
            size="sm"
            variant="primary"
            loading={check.isPending}
            disabled={!text.trim()}
          >
            {reviews.length > 0 ? 'Check again' : 'Check it'}
          </Button>
          {check.isPending ? (
            <span className="text-[0.75rem] text-[var(--text-muted)]">
              Reading your attempt against the lecture material…
            </span>
          ) : null}
        </div>
        {check.error ? (
          <Callout tone="danger" className="mt-2" icon={<AlertTriangle size={14} />}>
            {describeError(check.error)}
          </Callout>
        ) : null}
      </form>
    </div>
  );
}

/** Plan citations carry no bounding box or score; the renderer needs the tutor's shape. */
function asCitations(citations: PlanCitation[]): Citation[] {
  return citations.map((c) => ({ ...c, bbox: null, score: 0, cited_inline: true }));
}

function CitationChip({ citation, onOpen }: { citation: PlanCitation; onOpen: () => void }) {
  return (
    <button
      type="button"
      onClick={onOpen}
      title={citation.quote}
      className="inline-flex items-center gap-1 rounded-full border border-[var(--border-subtle)] bg-[var(--surface-sunken)] px-2 py-0.5 text-[0.6875rem] text-[var(--text-secondary)] hover:border-[var(--text-accent)] hover:text-[var(--text-accent)]"
    >
      <span className="font-semibold">[{citation.marker}]</span>
      <span className="max-w-[16rem] truncate">
        {citation.document_title || 'Lecture'} · p.{citation.page_number}
      </span>
    </button>
  );
}
