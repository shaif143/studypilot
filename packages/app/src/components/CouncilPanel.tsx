/**
 * An assignment worked out by several models together.
 *
 * Two steps, in order: the models agree a step-by-step guide, then complete
 * the assignment by following it. Each step is a discussion — every model
 * drafts alone, they review each other, a dispute is ruled on by a model that
 * took neither side, and they vote on one agreed version.
 *
 * This panel does three things besides starting that. It shows which of the
 * four models can take part and why the others cannot, because a discussion
 * between two is not the one the student asked for. It lets the student pick
 * the files the models may use as references. And it shows the discussion
 * itself, not only its result: who found what, who gave way, who ruled, and
 * whether they all agreed in the end. If they did not, that is said first.
 */
import type {
  Citation,
  CouncilEvent,
  CouncilIssue,
  CouncilRun,
  CouncilSeat,
  CourseFolder,
  DocumentSummary,
} from '@studypilot/shared';
import { Badge, Button, Callout, Card, SectionHeading, Spinner, Tabs, type Tone } from '@studypilot/ui';
import {
  AlertTriangle,
  Check,
  CircleSlash,
  Download,
  Folder,
  Gavel,
  ListOrdered,
  MessagesSquare,
  PenLine,
  Scale,
  Square,
  Users,
} from 'lucide-react';
import { useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';

import {
  describeError,
  useCouncil,
  useDocuments,
  useFolders,
  useSettings,
  useStartCouncil,
  useStopCouncil,
} from '../hooks/queries';
import { GroundedAnswer } from './GroundedAnswer';

type Kind = 'guide' | 'solution';

const KIND_TEXT: Record<Kind, { title: string; start: string; again: string; running: string }> = {
  guide: {
    title: 'Step-by-step guide',
    start: 'Work out the step-by-step guide',
    again: 'Work out the guide again',
    running: 'Working out the guide',
  },
  solution: {
    title: 'Completed assignment',
    start: 'Complete the assignment',
    again: 'Complete it again',
    running: 'Completing the assignment',
  },
};

const ISSUE_STATUS: Record<string, { tone: Tone; label: (issue: CouncilIssue) => string }> = {
  accepted: { tone: 'grounded', label: (i) => `Accepted by ${i.author}` },
  upheld: { tone: 'grounded', label: (i) => `Upheld by ${i.judge || 'the judge'}` },
  dismissed: { tone: 'neutral', label: (i) => `Dismissed by ${i.judge || 'the judge'}` },
  contested: { tone: 'caution', label: () => 'Left contested' },
  defended: { tone: 'caution', label: (i) => `Defended by ${i.author}` },
  open: { tone: 'neutral', label: () => 'Raised' },
};

const isBusy = (run: CouncilRun | null | undefined) =>
  Boolean(run && (run.status === 'queued' || run.status === 'running'));

export function CouncilPanel({
  courseId,
  brief,
}: {
  courseId: string;
  brief: DocumentSummary;
}) {
  const settings = useSettings();
  const allowCloud = Boolean(settings.data?.allow_cloud_processing);
  const council = useCouncil(brief.id, allowCloud);
  const start = useStartCouncil(brief.id, allowCloud);
  const stop = useStopCouncil(brief.id);
  const documents = useDocuments(courseId);
  const folders = useFolders(courseId);

  const guide = council.data?.guide ?? null;
  const solution = council.data?.solution ?? null;
  const seats = council.data?.seats ?? [];
  // Every model that is ready takes part unless the student leaves it out:
  // the local one is slow, and a run without it finishes much sooner.
  const [leftOut, setLeftOut] = useState<Set<string>>(() => new Set());
  const ready = seats.filter((seat) => seat.ready && !leftOut.has(seat.key));
  const busy = isBusy(guide) || isBusy(solution);
  const guideDone = guide?.status === 'done';

  // What may be a reference: anything readable in the course except the brief itself.
  const candidates = useMemo(
    () =>
      (documents.data ?? []).filter(
        (document) =>
          document.id !== brief.id &&
          (document.status === 'indexed' || document.status === 'partial'),
      ),
    [brief.id, documents.data],
  );
  // Until the student chooses, the references are what was used last time, or every lecture.
  const [chosen, setChosen] = useState<Set<string> | null>(null);
  const selection = useMemo(() => {
    if (chosen) return chosen;
    const last = solution?.reference_document_ids ?? guide?.reference_document_ids;
    if (last && last.length > 0) return new Set(last);
    return new Set(
      candidates
        .filter((document) => document.kind === 'lecture' || document.kind === 'textbook')
        .map((document) => document.id),
    );
  }, [candidates, chosen, guide, solution]);

  const [view, setView] = useState<Kind | null>(null);
  const shown: Kind = view ?? (solution ? 'solution' : 'guide');
  const current = shown === 'solution' ? solution : guide;

  const begin = (kind: Kind) => {
    setView(kind);
    start.mutate({
      kind,
      allow_cloud: allowCloud,
      reference_document_ids: [...selection],
      members: ready.map((seat) => seat.key),
    });
  };

  return (
    <Card>
      <SectionHeading
        title="Work it out with all your models"
        description="Each model answers alone, they check each other's work, a disagreement is ruled on by a model that took neither side, and they vote on one version. You get the version they agreed, and the discussion behind it."
      />

      {council.isError ? (
        <Callout tone="danger" className="mt-3" icon={<AlertTriangle size={14} />}>
          {describeError(council.error)}
        </Callout>
      ) : null}

      <Seats
        seats={seats}
        loading={council.isPending}
        leftOut={leftOut}
        disabled={busy}
        onToggle={(key, takePart) =>
          setLeftOut((current) => {
            const next = new Set(current);
            if (takePart) next.delete(key);
            else next.add(key);
            return next;
          })
        }
      />

      <ReferencePicker
        documents={candidates}
        folders={folders.data ?? []}
        selection={selection}
        disabled={busy}
        onChange={setChosen}
      />

      <div className="mt-4 grid grid-cols-1 gap-2 @2xl:grid-cols-2">
        <StepButton
          number={1}
          kind="guide"
          run={guide}
          disabled={busy || ready.length < 2}
          loading={start.isPending && start.variables?.kind === 'guide'}
          onStart={() => begin('guide')}
          onStop={() => stop.mutate('guide')}
          hint="How to do it, in order, with the pages to use. Nothing is done for you yet."
        />
        <StepButton
          number={2}
          kind="solution"
          run={solution}
          disabled={busy || ready.length < 2 || !guideDone}
          loading={start.isPending && start.variables?.kind === 'solution'}
          onStart={() => begin('solution')}
          onStop={() => stop.mutate('solution')}
          hint={
            guideDone
              ? 'The finished work, written by following the agreed guide.'
              : 'Available once the guide in step 1 has been agreed.'
          }
        />
      </div>

      {ready.length < 2 && !council.isPending ? (
        <Callout tone="caution" className="mt-3" icon={<AlertTriangle size={14} />}>
          A discussion needs at least two models, and{' '}
          {ready.length === 1 ? `only ${ready[0]?.name} is ready` : 'none is ready'}.{' '}
          {allowCloud ? null : 'Cloud processing is off, so only the local model may be used. '}
          <Link to="/settings" className="underline">
            Set the others up in Settings
          </Link>
          .
        </Callout>
      ) : null}
      {start.error ? (
        <Callout tone="danger" className="mt-3" icon={<AlertTriangle size={14} />}>
          {describeError(start.error)}
        </Callout>
      ) : null}

      {guide || solution ? (
        <div className="mt-5 border-t border-[var(--border-subtle)] pt-4">
          {guide && solution ? (
            <Tabs
              value={shown}
              onChange={setView}
              options={[
                { value: 'guide', label: KIND_TEXT.guide.title },
                { value: 'solution', label: KIND_TEXT.solution.title },
              ]}
            />
          ) : null}
          {current ? <RunView key={current.id} run={current} title={brief.title} /> : null}
        </div>
      ) : null}
    </Card>
  );
}

// --- who is at the table -----------------------------------------------------------------
function Seats({
  seats,
  loading,
  leftOut,
  disabled,
  onToggle,
}: {
  seats: CouncilSeat[];
  loading: boolean;
  leftOut: Set<string>;
  disabled: boolean;
  onToggle: (key: string, takePart: boolean) => void;
}) {
  if (loading) {
    return (
      <p className="mt-3 flex items-center gap-2 text-[0.75rem] text-[var(--text-secondary)]">
        <Spinner size={12} /> Checking which models are ready…
      </p>
    );
  }
  return (
    <ul className="mt-3 grid grid-cols-1 gap-2 @2xl:grid-cols-2 @5xl:grid-cols-4" aria-label="Models">
      {seats.map((seat) => {
        const takingPart = seat.ready && !leftOut.has(seat.key);
        return (
          <li
            key={seat.key}
            className={[
              'rounded-[var(--radius-card)] border px-3 py-2',
              takingPart
                ? 'border-[var(--border-subtle)] bg-[var(--surface-page)]'
                : 'border-dashed border-[var(--border-strong)] bg-[var(--surface-sunken)]',
            ].join(' ')}
          >
            <label
              className={[
                'flex items-center gap-2 text-[0.8125rem] font-semibold text-[var(--text-primary)]',
                seat.ready ? 'cursor-pointer' : '',
              ].join(' ')}
            >
              {seat.ready ? (
                <input
                  type="checkbox"
                  className="accent-[var(--color-accent-600)]"
                  checked={takingPart}
                  disabled={disabled}
                  onChange={(event) => onToggle(seat.key, event.target.checked)}
                />
              ) : (
                <CircleSlash size={13} className="text-[var(--text-muted)]" />
              )}
              {seat.name}
              <span className="sr-only">
                {seat.ready ? (takingPart ? 'taking part' : 'left out') : 'not ready'}
              </span>
            </label>
            <p className="mt-0.5 text-[0.6875rem] leading-snug text-[var(--text-secondary)]">
              {seat.ready
                ? seat.local
                  ? `${seat.model} · on this Mac. Slow: it can add several minutes to each stage.`
                  : seat.model || 'ready'
                : seat.detail || 'Not set up.'}
            </p>
          </li>
        );
      })}
    </ul>
  );
}

// --- which files the models may use ------------------------------------------------------
function ReferencePicker({
  documents,
  folders,
  selection,
  disabled,
  onChange,
}: {
  documents: DocumentSummary[];
  folders: CourseFolder[];
  selection: Set<string>;
  disabled: boolean;
  onChange: (next: Set<string>) => void;
}) {
  const [open, setOpen] = useState(false);
  const groups = useMemo(() => {
    const byFolder = new Map<string, DocumentSummary[]>();
    for (const document of documents) {
      const key = document.folder_id ?? '';
      byFolder.set(key, [...(byFolder.get(key) ?? []), document]);
    }
    const named = folders
      .filter((folder) => byFolder.has(folder.id))
      .map((folder) => ({ id: folder.id, name: folder.name, items: byFolder.get(folder.id) ?? [] }));
    const loose = byFolder.get('') ?? [];
    return loose.length > 0
      ? [...named, { id: '', name: named.length > 0 ? 'Not in a folder' : '', items: loose }]
      : named;
  }, [documents, folders]);

  const toggle = (ids: string[], on: boolean) => {
    const next = new Set(selection);
    for (const id of ids) {
      if (on) next.add(id);
      else next.delete(id);
    }
    onChange(next);
  };

  const count = documents.filter((document) => selection.has(document.id)).length;

  return (
    <div className="mt-4 rounded-[var(--radius-card)] border border-[var(--border-subtle)] bg-[var(--surface-sunken)]">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        className="flex w-full items-center gap-2 rounded-[var(--radius-card)] px-3 py-2 text-left hover:bg-[var(--surface-hover)]"
      >
        <Folder size={14} className="shrink-0 text-[var(--text-secondary)]" />
        <span className="min-w-0 flex-1">
          <span className="block text-[0.8125rem] font-medium text-[var(--text-primary)]">
            References: {count} of {documents.length} file{documents.length === 1 ? '' : 's'}
          </span>
          <span className="block text-[0.75rem] text-[var(--text-secondary)]">
            The models may quote and cite only these files from your course.
          </span>
        </span>
        <span className="text-[0.75rem] font-medium text-[var(--text-accent)]">
          {open ? 'Done' : 'Choose'}
        </span>
      </button>

      {open ? (
        <div className="border-t border-[var(--border-subtle)] px-3 py-2">
          {documents.length === 0 ? (
            <p className="text-[0.75rem] text-[var(--text-secondary)]">
              This course has no other readable files yet. Add lectures to the course first.
            </p>
          ) : (
            <>
              <div className="mb-2 flex gap-3 text-[0.75rem]">
                <button
                  type="button"
                  disabled={disabled}
                  className="font-medium text-[var(--text-accent)] disabled:opacity-50"
                  onClick={() => toggle(documents.map((d) => d.id), true)}
                >
                  Select all
                </button>
                <button
                  type="button"
                  disabled={disabled}
                  className="font-medium text-[var(--text-accent)] disabled:opacity-50"
                  onClick={() => toggle(documents.map((d) => d.id), false)}
                >
                  Clear
                </button>
              </div>
              <div className="sp-scroll max-h-64 space-y-3 overflow-y-auto pr-1">
                {groups.map((group) => {
                  const ids = group.items.map((item) => item.id);
                  const picked = ids.filter((id) => selection.has(id)).length;
                  return (
                    <fieldset key={group.id || 'loose'} disabled={disabled}>
                      {group.name ? (
                        <label className="flex cursor-pointer items-center gap-2 text-[0.75rem] font-semibold text-[var(--text-primary)]">
                          <input
                            type="checkbox"
                            className="accent-[var(--color-accent-600)]"
                            checked={picked === ids.length}
                            ref={(element) => {
                              if (element) element.indeterminate = picked > 0 && picked < ids.length;
                            }}
                            onChange={(event) => toggle(ids, event.target.checked)}
                          />
                          <Folder size={12} />
                          {group.name}
                          <span className="font-normal text-[var(--text-muted)]">
                            {picked}/{ids.length}
                          </span>
                        </label>
                      ) : null}
                      <ul className={group.name ? 'ml-5 mt-1 space-y-0.5' : 'space-y-0.5'}>
                        {group.items.map((document) => (
                          <li key={document.id}>
                            <label className="flex cursor-pointer items-center gap-2 text-[0.75rem] text-[var(--text-secondary)]">
                              <input
                                type="checkbox"
                                className="accent-[var(--color-accent-600)]"
                                checked={selection.has(document.id)}
                                onChange={(event) => toggle([document.id], event.target.checked)}
                              />
                              <span className="min-w-0 flex-1 truncate">{document.title}</span>
                              <span className="shrink-0 tabular-nums text-[var(--text-muted)]">
                                {document.page_count} p
                              </span>
                            </label>
                          </li>
                        ))}
                      </ul>
                    </fieldset>
                  );
                })}
              </div>
            </>
          )}
        </div>
      ) : null}
    </div>
  );
}

// --- the two steps -----------------------------------------------------------------------
function StepButton({
  number,
  kind,
  run,
  disabled,
  loading,
  hint,
  onStart,
  onStop,
}: {
  number: number;
  kind: Kind;
  run: CouncilRun | null;
  disabled: boolean;
  loading: boolean;
  hint: string;
  onStart: () => void;
  onStop: () => void;
}) {
  const text = KIND_TEXT[kind];
  const busy = isBusy(run);
  return (
    <div className="rounded-[var(--radius-card)] border border-[var(--border-subtle)] bg-[var(--surface-page)] p-3">
      <p className="flex items-center gap-2 text-[0.75rem] font-semibold uppercase tracking-wide text-[var(--text-muted)]">
        <span className="flex h-5 w-5 items-center justify-center rounded-full bg-[var(--surface-selected)] text-[var(--text-accent)]">
          {number}
        </span>
        {text.title}
        {run?.status === 'done' ? (
          <Badge tone={verdict(run).tone}>{verdict(run).badge}</Badge>
        ) : null}
      </p>
      <p className="mt-1.5 min-h-[2.2rem] text-[0.75rem] leading-snug text-[var(--text-secondary)]">
        {busy ? run?.phase_detail || 'Starting…' : hint}
      </p>
      {busy ? (
        <div className="mt-2 flex items-center gap-2">
          <span className="flex min-w-0 flex-1 items-center gap-2 text-[0.8125rem] font-medium text-[var(--text-primary)]">
            <Spinner size={13} /> {text.running}…
          </span>
          <Button size="sm" variant="danger" icon={<Square size={11} />} onClick={onStop}>
            Stop
          </Button>
        </div>
      ) : (
        <Button
          className="mt-2 w-full"
          variant={run?.status === 'done' ? 'secondary' : 'primary'}
          icon={kind === 'guide' ? <ListOrdered size={14} /> : <PenLine size={14} />}
          disabled={disabled}
          loading={loading}
          onClick={onStart}
        >
          {run ? text.again : text.start}
        </Button>
      )}
    </div>
  );
}

// --- one discussion and what it ended on -------------------------------------------------
function RunView({ run, title }: { run: CouncilRun; title: string }) {
  const navigate = useNavigate();
  const text = KIND_TEXT[run.kind];
  const busy = isBusy(run);
  const consensus = run.consensus;
  const issues = useMemo(() => finalIssues(run.events), [run.events]);
  const citations = run.citations as Citation[];

  const download = () => {
    const blob = new Blob([run.result_markdown], { type: 'text/markdown;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `${title.replace(/[^\w\- ]+/g, '').trim() || 'assignment'} - ${run.kind}.md`;
    anchor.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="mt-3 space-y-3">
      {run.warnings.map((warning) => (
        <Callout key={warning} tone="caution" icon={<AlertTriangle size={14} />}>
          {warning}
        </Callout>
      ))}
      {run.status === 'failed' ? (
        <Callout tone="danger" icon={<AlertTriangle size={14} />} title="This did not finish">
          {run.error || 'The discussion failed.'}
        </Callout>
      ) : null}
      {run.status === 'cancelled' ? (
        <Callout tone="neutral" icon={<Square size={13} />}>
          {run.phase_detail || 'Stopped.'} Start it again to get a result.
        </Callout>
      ) : null}

      {run.status === 'done' ? (
        <>
          <Callout
            tone={verdict(run).tone}
            icon={consensus.unanimous ? <Check size={14} /> : <AlertTriangle size={14} />}
            title={verdict(run).title}
          >
            {summaryLine(run)}
          </Callout>
          {(consensus.overruled?.length ?? 0) > 0 ? (
            <div className="rounded-[var(--radius-card)] border border-[var(--border-subtle)] bg-[var(--surface-page)] p-3">
              <p className="text-[0.8125rem] font-semibold text-[var(--text-primary)]">
                Objections that were ruled against
              </p>
              <ul className="mt-1.5 space-y-2">
                {consensus.overruled?.map((objection, index) => (
                  <li key={index} className="text-[0.8125rem] text-[var(--text-primary)]">
                    <span className="font-semibold">{objection.member} objected: </span>
                    {objection.problem}{' '}
                    <span className="font-semibold">
                      {objection.judge || 'A neutral model'} ruled against it:
                    </span>{' '}
                    {objection.ruling}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          {(consensus.objections?.length ?? 0) > 0 ? (
            <div className="rounded-[var(--radius-card)] border border-[var(--signal-caution)]/40 bg-[var(--signal-caution-bg)] p-3">
              <p className="text-[0.8125rem] font-semibold text-[var(--text-primary)]">
                What is still objected to
              </p>
              <ul className="mt-1.5 space-y-2">
                {consensus.objections?.map((objection, index) => (
                  <li key={index} className="text-[0.8125rem] text-[var(--text-primary)]">
                    <span className="font-semibold">{objection.member}: </span>
                    {objection.problem}
                    {objection.reason ? ` Reason: ${objection.reason}` : ''}
                    {objection.fix ? ` Its fix: ${objection.fix}` : ''}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          <article className="rounded-[var(--radius-card)] border border-[var(--border-subtle)] bg-[var(--surface-raised)] p-5 shadow-[var(--shadow-card)]">
            <header className="mb-2 flex flex-wrap items-center gap-2">
              <h3 className="min-w-0 flex-1 text-base font-semibold text-[var(--text-primary)]">
                {text.title}
              </h3>
              <Button size="sm" variant="ghost" icon={<Download size={13} />} onClick={download}>
                Save as a file
              </Button>
            </header>
            <GroundedAnswer
              markdown={run.result_markdown}
              citations={citations}
              onCitationClick={(citation) =>
                navigate(`/study/${citation.document_id}?page=${citation.page_number}`)
              }
            />
          </article>
          <p className="text-[0.6875rem] leading-relaxed text-[var(--text-muted)]">
            Written by AI models. Agreement between them is not proof: models can share a
            mistake, so check the work yourself. Check your module's rules on using AI before
            you submit anything based on this.
          </p>
        </>
      ) : null}

      {run.events.length > 0 || busy ? (
        <Discussion run={run} issues={issues} busy={busy} />
      ) : null}
    </div>
  );
}

/** How a finished discussion ended, in the words the screen uses for it. */
function verdict(run: CouncilRun): { tone: Tone; badge: string; title: string } {
  const c = run.consensus;
  const overruled = c.overruled?.length ?? 0;
  if (c.unanimous) {
    return {
      tone: 'grounded',
      badge: 'Agreed',
      title: `All ${c.agreed?.length ?? 0} models agreed on this`,
    };
  }
  if ((c.took_part?.length ?? 0) < 2) {
    return {
      tone: 'caution',
      badge: 'Unchecked',
      title: 'Only one model answered, so nothing checked this',
    };
  }
  if (c.settled && overruled > 0) {
    return {
      tone: 'info',
      badge: 'Agreed by ruling',
      title: `${c.agreed?.length ?? 0} models agreed; ${overruled} objection${overruled === 1 ? ' was' : 's were'} ruled against by a neutral model`,
    };
  }
  return {
    tone: 'caution',
    badge: 'Not agreed',
    title: 'The models did not all agree on this',
  };
}

function summaryLine(run: CouncilRun): string {
  const c = run.consensus;
  const names = (c.took_part ?? []).join(', ');
  const parts = [`${names} took part.`];
  const raised = c.issues_raised ?? 0;
  if (raised > 0) {
    const corrected = (c.issues_accepted ?? 0) + (c.issues_upheld ?? 0);
    parts.push(
      `They raised ${raised} issue${raised === 1 ? '' : 's'} about each other's drafts: ` +
        `${corrected} corrected, ${c.issues_dismissed ?? 0} dismissed by a neutral model` +
        ((c.issues_contested ?? 0) > 0 ? `, ${c.issues_contested} left contested.` : '.'),
    );
  } else if ((c.took_part?.length ?? 0) > 1) {
    parts.push('None of them found a mistake in another draft.');
  }
  if (c.lead) parts.push(`${c.lead} wrote the agreed version.`);
  if ((c.rounds ?? 0) > 1) parts.push(`It took ${c.rounds} rounds of voting.`);
  for (const drop of c.dropped ?? []) {
    parts.push(`${drop.member} dropped out while ${PHASE_VERB[drop.phase] ?? drop.phase}.`);
  }
  return parts.join(' ');
}

const PHASE_VERB: Record<string, string> = {
  propose: 'drafting',
  review: 'reviewing',
  defend: 'answering its reviewers',
  judge: 'judging',
  synthesis: 'writing the agreed version',
  vote: 'voting',
  revise: 'revising',
};

/** The issues as they ended, or as far as the discussion has got. */
function finalIssues(events: CouncilEvent[]): CouncilIssue[] {
  const settled = events.find((event) => event.type === 'issues');
  if (settled && settled.type === 'issues') return settled.issues;
  const issues = new Map<number, CouncilIssue>();
  for (const event of events) {
    if (event.type === 'review') {
      for (const issue of event.issues) issues.set(issue.id, { ...issue });
    } else if (event.type === 'defence') {
      for (const response of event.responses) {
        const issue = issues.get(response.issue);
        if (issue) {
          issue.status = response.stance as CouncilIssue['status'];
          issue.response = response.reason;
        }
      }
    } else if (event.type === 'ruling') {
      for (const ruling of event.rulings) {
        const issue = issues.get(ruling.issue);
        if (issue) {
          issue.status = ruling.ruling as CouncilIssue['status'];
          issue.ruling = ruling.reason;
          issue.judge = event.member;
        }
      }
    }
  }
  return [...issues.values()];
}

function Discussion({
  run,
  issues,
  busy,
}: {
  run: CouncilRun;
  issues: CouncilIssue[];
  busy: boolean;
}) {
  const drafts = run.events.filter((event) => event.type === 'draft');
  const votes = run.events.filter((event) => event.type === 'vote');
  const errors = run.events.filter((event) => event.type === 'error');
  const rounds = [...new Set(votes.map((vote) => (vote.type === 'vote' ? vote.round : 0)))];
  const reviewIssues = issues.filter((issue) => issue.stage !== 'vote');
  const voteIssues = issues.filter((issue) => issue.stage === 'vote');

  return (
    <details
      className="rounded-[var(--radius-card)] border border-[var(--border-subtle)] bg-[var(--surface-page)]"
      open={busy || run.status !== 'done'}
    >
      <summary className="flex cursor-pointer select-none items-center gap-2 px-3 py-2 text-[0.8125rem] font-semibold text-[var(--text-primary)]">
        <MessagesSquare size={14} className="text-[var(--text-secondary)]" />
        The discussion
        <span className="font-normal text-[var(--text-secondary)]">
          {drafts.length} draft{drafts.length === 1 ? '' : 's'}, {reviewIssues.length} issue
          {reviewIssues.length === 1 ? '' : 's'}, {votes.length} vote
          {votes.length === 1 ? '' : 's'}
        </span>
        {busy ? <Spinner size={12} /> : null}
      </summary>

      <div className="space-y-4 border-t border-[var(--border-subtle)] p-3">
        {busy && run.phase_detail ? (
          <p className="text-[0.8125rem] text-[var(--text-secondary)]" aria-live="polite">
            {run.phase_detail}
          </p>
        ) : null}

        {drafts.length > 0 ? (
          <section>
            <StageHeading icon={<Users size={13} />} title="1. Each model's own draft" />
            <div className="mt-1.5 space-y-1.5">
              {drafts.map((event, index) =>
                event.type === 'draft' ? (
                  <details
                    key={`${event.member}-${index}`}
                    className="rounded-[var(--radius-control)] border border-[var(--border-subtle)]"
                  >
                    <summary className="cursor-pointer select-none px-3 py-1.5 text-[0.8125rem] font-medium text-[var(--text-primary)]">
                      {event.member}'s draft
                    </summary>
                    <div className="border-t border-[var(--border-subtle)] px-3 py-2">
                      <GroundedAnswer markdown={event.markdown} citations={[]} />
                    </div>
                  </details>
                ) : null,
              )}
            </div>
          </section>
        ) : null}

        {reviewIssues.length > 0 ? (
          <section>
            <StageHeading
              icon={<Scale size={13} />}
              title="2 and 3. Issues raised, answered and ruled on"
            />
            <ul className="mt-1.5 space-y-2">
              {reviewIssues.map((issue) => (
                <IssueCard key={issue.id} issue={issue} />
              ))}
            </ul>
          </section>
        ) : drafts.length > 1 && !busy ? (
          <p className="text-[0.8125rem] text-[var(--text-secondary)]">
            No model raised an issue about another's draft.
          </p>
        ) : null}

        {votes.length > 0 ? (
          <section>
            <StageHeading icon={<Check size={13} />} title="5. The vote on the agreed version" />
            <div className="mt-1.5 space-y-2">
              {rounds.map((round) => (
                <div key={round}>
                  {rounds.length > 1 ? (
                    <p className="text-[0.75rem] font-medium text-[var(--text-secondary)]">
                      Round {round}
                    </p>
                  ) : null}
                  <ul className="mt-1 space-y-1.5">
                    {votes.map((vote, index) =>
                      vote.type === 'vote' && vote.round === round ? (
                        <li key={`${vote.member}-${index}`} className="text-[0.8125rem]">
                          <Badge tone={vote.vote === 'agree' ? 'grounded' : 'caution'}>
                            {vote.member}: {vote.vote === 'agree' ? 'agrees' : 'objects'}
                          </Badge>
                          {vote.objections.map((objection, at) => (
                            <p
                              key={at}
                              className="ml-1 mt-1 border-l-2 border-[var(--border-strong)] pl-2 text-[var(--text-primary)]"
                            >
                              {objection.problem}
                              {objection.reason ? ` Reason: ${objection.reason}` : ''}
                            </p>
                          ))}
                        </li>
                      ) : null,
                    )}
                  </ul>
                </div>
              ))}
            </div>
          </section>
        ) : null}

        {voteIssues.length > 0 ? (
          <section>
            <StageHeading
              icon={<Gavel size={13} />}
              title="Objections to the agreed version, answered and ruled on"
            />
            <ul className="mt-1.5 space-y-2">
              {voteIssues.map((issue) => (
                <IssueCard key={issue.id} issue={issue} />
              ))}
            </ul>
          </section>
        ) : null}

        {errors.length > 0 ? (
          <section>
            <StageHeading icon={<AlertTriangle size={13} />} title="Models that could not answer" />
            <ul className="mt-1.5 space-y-1 text-[0.8125rem] text-[var(--text-secondary)]">
              {errors.map((event, index) =>
                event.type === 'error' ? (
                  <li key={index}>
                    <span className="font-medium text-[var(--text-primary)]">{event.member}</span>{' '}
                    while {PHASE_VERB[event.phase] ?? event.phase}: {event.text}
                  </li>
                ) : null,
              )}
            </ul>
          </section>
        ) : null}
      </div>
    </details>
  );
}

function StageHeading({ icon, title }: { icon: React.ReactNode; title: string }) {
  return (
    <h4 className="flex items-center gap-1.5 text-[0.75rem] font-semibold uppercase tracking-wide text-[var(--text-muted)]">
      {icon}
      {title}
    </h4>
  );
}

function IssueCard({ issue }: { issue: CouncilIssue }) {
  const status = ISSUE_STATUS[issue.status] ?? ISSUE_STATUS.open;
  return (
    <li className="rounded-[var(--radius-control)] border border-[var(--border-subtle)] p-2.5 text-[0.8125rem]">
      <p className="flex flex-wrap items-center gap-1.5">
        <span className="font-semibold text-[var(--text-primary)]">
          {issue.stage === 'vote'
            ? `${issue.reviewer} objected${issue.round ? ` in round ${issue.round}` : ''}`
            : `${issue.reviewer} on ${issue.author}'s draft`}
        </span>
        <Badge tone={issue.kind === 'mistake' ? 'danger' : 'info'}>
          {issue.kind === 'mistake' ? 'Mistake' : 'Better approach'}
        </Badge>
        <span className="flex-1" />
        {status ? <Badge tone={status.tone}>{status.label(issue)}</Badge> : null}
      </p>
      {issue.where ? (
        <p className="mt-1 text-[0.75rem] italic text-[var(--text-secondary)]">
          “{issue.where.replace(/^["“”'\s]+|["“”'\s]+$/g, '')}”
        </p>
      ) : null}
      <div className="mt-1">
        <GroundedAnswer markdown={issue.problem} citations={[]} />
      </div>
      <Line label="Reason" text={issue.reason} />
      <Line label="Its fix" text={issue.fix} />
      {issue.response ? (
        <Line
          label={issue.status === 'accepted' ? `${issue.author} accepted` : `${issue.author} answered`}
          text={issue.response}
        />
      ) : null}
      {issue.ruling ? (
        <Line
          label={`${issue.judge || 'The judge'} ruled`}
          text={issue.ruling}
          icon={<Gavel size={12} />}
        />
      ) : null}
    </li>
  );
}

function Line({ label, text, icon }: { label: string; text: string; icon?: React.ReactNode }) {
  if (!text) return null;
  return (
    <div className="mt-1.5 border-l-2 border-[var(--border-strong)] pl-2">
      <p className="flex items-center gap-1 text-[0.6875rem] font-semibold uppercase tracking-wide text-[var(--text-muted)]">
        {icon}
        {label}
      </p>
      <GroundedAnswer markdown={text} citations={[]} />
    </div>
  );
}
