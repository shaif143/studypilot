/** Course management: create, edit, archive and delete courses. */
import type { Course } from '@studypilot/shared';
import {
  Button,
  Card,
  Dialog,
  EmptyState,
  Field,
  Input,
  SectionHeading,
  Skeleton,
  Textarea,
} from '@studypilot/ui';
import { GraduationCap, Plus, Trash2 } from 'lucide-react';
import { useState } from 'react';
import { Link } from 'react-router-dom';

import { ErrorPanel, PageBody, PageHeader } from '../components/AppShell';
import {
  describeError,
  useCourses,
  useCreateCourse,
  useDeleteCourse,
  useUpdateCourse,
} from '../hooks/queries';

export function CoursesScreen() {
  const courses = useCourses();
  const createCourse = useCreateCourse();
  const updateCourse = useUpdateCourse();
  const deleteCourse = useDeleteCourse();

  const [editing, setEditing] = useState<Course | 'new' | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<Course | null>(null);

  return (
    <>
      <PageHeader
        title="My Courses"
        description="A course is the retrieval boundary: the tutor only ever answers from the material inside the course you are studying."
        actions={
          <Button variant="primary" icon={<Plus size={15} />} onClick={() => setEditing('new')}>
            New course
          </Button>
        }
      />
      <PageBody>
        {courses.isError ? (
          <ErrorPanel
            message={describeError(courses.error)}
            onRetry={() => void courses.refetch()}
          />
        ) : null}

        {courses.isPending ? (
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {Array.from({ length: 3 }, (_, i) => (
              <Skeleton key={i} className="h-32" />
            ))}
          </div>
        ) : courses.data?.length === 0 ? (
          <EmptyState
            icon={<GraduationCap size={22} />}
            title="No courses yet"
            description="Create a course for each module you are studying, then upload its lectures."
            action={
              <Button variant="primary" onClick={() => setEditing('new')}>
                Create a course
              </Button>
            }
          />
        ) : (
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {courses.data?.map((course) => (
              <Card key={course.id} className="flex flex-col">
                <SectionHeading
                  title={
                    <Link
                      to={`/library?course=${course.id}`}
                      className="hover:text-[var(--text-accent)] hover:underline"
                    >
                      {course.title}
                    </Link>
                  }
                  description={
                    course.code || course.institution
                      ? [course.code, course.institution].filter(Boolean).join(' · ')
                      : undefined
                  }
                />
                {course.description ? (
                  <p className="mt-2 line-clamp-3 text-[0.8125rem] leading-relaxed text-[var(--text-secondary)]">
                    {course.description}
                  </p>
                ) : null}
                <dl className="mt-3 flex gap-4 text-[0.75rem] text-[var(--text-secondary)]">
                  <div>
                    <dt className="text-[var(--text-muted)]">Documents</dt>
                    <dd className="tabular-nums text-[var(--text-primary)]">
                      {course.document_count}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-[var(--text-muted)]">Pages</dt>
                    <dd className="tabular-nums text-[var(--text-primary)]">
                      {course.page_count}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-[var(--text-muted)]">Topics</dt>
                    <dd className="tabular-nums text-[var(--text-primary)]">
                      {course.topic_count}
                    </dd>
                  </div>
                </dl>
                <div className="mt-auto flex gap-2 pt-3">
                  <Link to={`/library?course=${course.id}`} className="flex-1">
                    <Button size="sm" className="w-full">
                      Open
                    </Button>
                  </Link>
                  <Button size="sm" variant="ghost" onClick={() => setEditing(course)}>
                    Edit
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    aria-label={`Delete ${course.title}`}
                    onClick={() => setConfirmDelete(course)}
                  >
                    <Trash2 size={14} />
                  </Button>
                </div>
              </Card>
            ))}
          </div>
        )}

        <CourseDialog
          key={editing === 'new' ? 'new' : (editing?.id ?? 'closed')}
          course={editing === 'new' ? null : editing}
          open={editing !== null}
          onClose={() => setEditing(null)}
          busy={createCourse.isPending || updateCourse.isPending}
          error={
            createCourse.error
              ? describeError(createCourse.error)
              : updateCourse.error
                ? describeError(updateCourse.error)
                : null
          }
          onSubmit={async (values) => {
            if (editing && editing !== 'new') {
              await updateCourse.mutateAsync({ id: editing.id, patch: values });
            } else {
              await createCourse.mutateAsync(values);
            }
            setEditing(null);
          }}
        />

        <Dialog
          open={confirmDelete !== null}
          onClose={() => setConfirmDelete(null)}
          title={`Delete ${confirmDelete?.title ?? 'course'}?`}
          description="This permanently removes the course, its documents, its index, and all progress recorded against it. It cannot be undone."
          footer={
            <>
              <Button onClick={() => setConfirmDelete(null)}>Cancel</Button>
              <Button
                variant="danger"
                loading={deleteCourse.isPending}
                onClick={async () => {
                  if (confirmDelete) await deleteCourse.mutateAsync(confirmDelete.id);
                  setConfirmDelete(null);
                }}
              >
                Delete permanently
              </Button>
            </>
          }
        />
      </PageBody>
    </>
  );
}

function CourseDialog({
  course,
  open,
  onClose,
  onSubmit,
  busy,
  error,
}: {
  course: Course | null;
  open: boolean;
  onClose: () => void;
  onSubmit: (values: {
    title: string;
    code: string;
    description: string;
    institution: string;
    integrity_policy: string;
  }) => Promise<void>;
  busy: boolean;
  error: string | null;
}) {
  const [title, setTitle] = useState(course?.title ?? '');
  const [code, setCode] = useState(course?.code ?? '');
  const [institution, setInstitution] = useState(course?.institution ?? '');
  const [description, setDescription] = useState(course?.description ?? '');
  const [policy, setPolicy] = useState(course?.integrity_policy ?? '');

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={course ? 'Edit course' : 'New course'}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="primary"
            loading={busy}
            disabled={title.trim().length === 0}
            onClick={() =>
              void onSubmit({
                title: title.trim(),
                code: code.trim(),
                description: description.trim(),
                institution: institution.trim(),
                integrity_policy: policy.trim(),
              })
            }
          >
            {course ? 'Save' : 'Create course'}
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        {error ? <ErrorPanel message={error} /> : null}
        <Field label="Course title">
          <Input
            value={title}
            autoFocus
            placeholder="Deep Learning"
            onChange={(event) => setTitle(event.target.value)}
          />
        </Field>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Course code" hint="Optional">
            <Input
              value={code}
              placeholder="CS3410"
              onChange={(event) => setCode(event.target.value)}
            />
          </Field>
          <Field label="Institution" hint="Optional">
            <Input
              value={institution}
              onChange={(event) => setInstitution(event.target.value)}
            />
          </Field>
        </div>
        <Field label="Description" hint="Optional">
          <Textarea
            rows={2}
            value={description}
            onChange={(event) => setDescription(event.target.value)}
          />
        </Field>
        <Field
          label="Academic integrity note"
          hint="Shown to the assignment coach so its guidance respects your course's rules. Optional."
        >
          <Textarea
            rows={2}
            value={policy}
            placeholder="Collaboration is allowed on problem sheets but all submitted work must be your own."
            onChange={(event) => setPolicy(event.target.value)}
          />
        </Field>
      </div>
    </Dialog>
  );
}
