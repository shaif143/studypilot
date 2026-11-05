/**
 * The course concept map.
 *
 * Topics as a graph, coloured by what the student's quiz history says about
 * each. The graph is built by code on the server from the stored topics and
 * mastery estimates — no model is involved, so it shows only concepts the
 * material contains — and drawn here with Mermaid in strict mode.
 *
 * The one piece of guidance it adds over the analytics screen is "where to
 * start": a weak topic whose predecessors are not weak. Revising padding is
 * wasted effort while convolution itself is shaky.
 */
import type { ConceptMapNode } from '@studypilot/shared';
import { Badge, Button, Callout, Card, EmptyState, SectionHeading, Skeleton } from '@studypilot/ui';
import type { Tone } from '@studypilot/ui';
import { ArrowLeft, Waypoints } from 'lucide-react';
import { Link, useParams } from 'react-router-dom';

import { ErrorPanel, PageBody, PageHeader } from '../components/AppShell';
import { MermaidDiagram } from '../components/MermaidDiagram';
import { describeError, useConceptMap, useCourse } from '../hooks/queries';

const LABEL_TONE: Record<ConceptMapNode['mastery_label'], Tone> = {
  unknown: 'neutral',
  weak: 'danger',
  developing: 'caution',
  strong: 'grounded',
};

const LABEL_TEXT: Record<ConceptMapNode['mastery_label'], string> = {
  unknown: 'Not enough evidence yet',
  weak: 'Weak',
  developing: 'Developing',
  strong: 'Strong',
};

export function ConceptMapScreen() {
  const { courseId = '' } = useParams();
  const course = useCourse(courseId || undefined);
  const map = useConceptMap(courseId || undefined);

  const nodes = map.data?.nodes ?? [];
  const bySlug = new Map(nodes.map((node) => [node.slug, node]));
  const startHere = (map.data?.revisit_first ?? [])
    .map((slug) => bySlug.get(slug))
    .filter((node): node is ConceptMapNode => Boolean(node));
  const counts = nodes.reduce<Record<string, number>>((acc, node) => {
    acc[node.mastery_label] = (acc[node.mastery_label] ?? 0) + 1;
    return acc;
  }, {});

  return (
    <>
      <PageHeader
        title={course.data ? `${course.data.title} — concept map` : 'Concept map'}
        description="How the topics in this course lead into each other, coloured by your quiz results. Solid arrows are stated prerequisites; dotted arrows follow the order of the lecture material."
        actions={
          <Link to="/courses">
            <Button variant="ghost" size="sm" icon={<ArrowLeft size={14} />}>
              My Courses
            </Button>
          </Link>
        }
      />
      <PageBody>
        {map.isLoading ? (
          <Skeleton className="h-64 w-full" />
        ) : map.isError ? (
          <ErrorPanel message={describeError(map.error)} onRetry={() => void map.refetch()} />
        ) : nodes.length === 0 ? (
          <EmptyState
            icon={<Waypoints size={24} />}
            title="No topics yet"
            description="Upload a lecture to this course and its topics will appear here as a map."
            action={
              <Link to={`/library?course=${courseId}`}>
                <Button size="sm">Open the library</Button>
              </Link>
            }
          />
        ) : (
          <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
            <Card>
              <MermaidDiagram
                code={map.data?.mermaid ?? ''}
                title={`Concept map of ${course.data?.title ?? 'this course'}`}
                className="min-h-48"
              />
              <div className="mt-3 flex flex-wrap gap-2 text-xs">
                {(['weak', 'developing', 'strong', 'unknown'] as const).map((label) => (
                  <Badge key={label} tone={LABEL_TONE[label]}>
                    {LABEL_TEXT[label]} · {counts[label] ?? 0}
                  </Badge>
                ))}
              </div>
            </Card>

            <div className="flex flex-col gap-4">
              {startHere.length > 0 ? (
                <Card>
                  <SectionHeading title="Start here" />
                  <p className="mt-1 text-sm text-[var(--text-muted)]">
                    Weak topics whose earlier topics are not weak. Fixing these first makes the ones
                    that build on them easier.
                  </p>
                  <ul className="mt-3 flex flex-col gap-2">
                    {startHere.map((node) => (
                      <li key={node.id}>
                        <TopicLink node={node} />
                      </li>
                    ))}
                  </ul>
                </Card>
              ) : (
                <Callout tone="neutral">
                  {counts.weak
                    ? 'Every weak topic here depends on another weak one; the map above shows the chain.'
                    : 'No topic is marked weak. Take a quiz and the map will colour itself from your answers.'}
                </Callout>
              )}

              <Card>
                <SectionHeading title="All topics" />
                <ul className="mt-3 flex flex-col gap-2">
                  {nodes.map((node) => (
                    <li key={node.id}>
                      <TopicLink node={node} />
                    </li>
                  ))}
                </ul>
              </Card>
            </div>
          </div>
        )}
      </PageBody>
    </>
  );
}

function TopicLink({ node }: { node: ConceptMapNode }) {
  const target = node.document_id
    ? `/read/${node.document_id}?page=${Math.max(1, node.first_page)}`
    : undefined;
  const body = (
    <span className="flex items-center justify-between gap-2">
      <span className="truncate text-sm text-[var(--text-primary)]">{node.name}</span>
      <Badge tone={LABEL_TONE[node.mastery_label]}>{LABEL_TEXT[node.mastery_label]}</Badge>
    </span>
  );
  return target ? (
    <Link to={target} className="block rounded-md px-2 py-1 hover:bg-[var(--surface-sunken)]">
      {body}
    </Link>
  ) : (
    <span className="block px-2 py-1">{body}</span>
  );
}
