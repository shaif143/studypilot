/**
 * Personalised notes in three modes.
 *
 * Concept notes follow the material's structure; exam notes reorganise it
 * around what is likely to be assessed; weakness notes are driven by the
 * student's own mastery estimates and recent mistakes. Notes are editable, and
 * an edited note is marked as such so a later regeneration does not quietly
 * replace the student's own work.
 */
import type { Note, NoteMode } from '@studypilot/shared';
import { formatRelativeTime } from '@studypilot/shared';
import {
  Badge,
  Button,
  Callout,
  Card,
  Dialog,
  EmptyState,
  Field,
  SectionHeading,
  Select,
  Skeleton,
  Tabs,
  Textarea,
} from '@studypilot/ui';
import { Download, FolderInput, NotebookPen, Pencil, Sparkles, Trash2 } from 'lucide-react';
import { useState } from 'react';

import { ErrorPanel, PageBody, PageHeader } from '../components/AppShell';
import { GroundedAnswer } from '../components/GroundedAnswer';
import { ProviderNotice } from '../components/ProviderNotice';
import {
  describeError,
  useGoogleDrive,
  useLocalFolders,
  useSaveNote,
  useCourses,
  useDeleteNote,
  useDocuments,
  useGenerateNote,
  useNotes,
  useProviders,
  useSettings,
  useUpdateNote,
} from '../hooks/queries';

const MODES: Array<{ value: NoteMode; label: string; description: string }> = [
  {
    value: 'concept',
    label: 'Concept',
    description: 'Definitions, relationships and equations, following the lecture structure.',
  },
  {
    value: 'exam',
    label: 'Exam-focused',
    description: 'What is most likely to be examined, with pitfalls and how to answer.',
  },
  {
    value: 'weakness',
    label: 'Weakness',
    description: 'Aimed at the topics your answers show you are weakest on.',
  },
];

export function NotesScreen() {
  const courses = useCourses();
  const settings = useSettings();
  const providers = useProviders();
  const notes = useNotes();
  const generate = useGenerateNote();
  const updateNote = useUpdateNote();
  const deleteNote = useDeleteNote();

  const [courseId, setCourseId] = useState('');
  const [documentId, setDocumentId] = useState('');
  const [mode, setMode] = useState<NoteMode>('concept');
  const [selected, setSelected] = useState<Note | null>(null);
  const [editing, setEditing] = useState<Note | null>(null);
  const [draft, setDraft] = useState('');
  const [confirmDelete, setConfirmDelete] = useState<Note | null>(null);

  const documents = useDocuments(courseId || undefined);
  const effectiveCourse = courseId || courses.data?.[0]?.id || '';
  const cloudAllowed = settings.data?.allow_cloud_processing ?? false;
  const activeProvider = providers.data?.llm.find((p) => p.key === providers.data?.active_llm);
  const needsCloudOptIn = Boolean(activeProvider?.sends_data_off_device);

  const download = (note: Note) => {
    const blob = new Blob([note.body_markdown], { type: 'text/markdown;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `${note.title.replace(/[^\w\- ]+/g, '').trim() || 'notes'}.md`;
    anchor.click();
    URL.revokeObjectURL(url);
  };

  return (
    <>
      <PageHeader
        title="My Notes"
        description="Revision notes built from your material and your results, editable and exportable."
      />
      <PageBody>
        <div className="space-y-4">
          <ProviderNotice compact />

          <Card>
            <SectionHeading title="Generate notes" />
            {generate.error ? <ErrorPanel message={describeError(generate.error)} /> : null}
            <div className="mt-3 grid gap-3 sm:grid-cols-3">
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
              <Field label="Document">
                <Select value={documentId} onChange={(event) => setDocumentId(event.target.value)}>
                  <option value="">Whole course</option>
                  {documents.data?.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.title}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Mode" hint={MODES.find((m) => m.value === mode)?.description}>
                <Tabs
                  value={mode}
                  onChange={setMode}
                  options={MODES.map((item) => ({ value: item.value, label: item.label }))}
                />
              </Field>
            </div>
            <div className="mt-4 flex justify-end">
              <Button
                variant="primary"
                icon={<Sparkles size={15} />}
                loading={generate.isPending}
                disabled={!effectiveCourse}
                onClick={async () => {
                  const note = await generate.mutateAsync({
                    course_id: effectiveCourse,
                    document_id: documentId || null,
                    mode,
                    allow_cloud: cloudAllowed && needsCloudOptIn,
                  });
                  setSelected(note);
                }}
              >
                Generate notes
              </Button>
            </div>
          </Card>

          {notes.isPending ? (
            <Skeleton className="h-32" />
          ) : notes.data?.length === 0 ? (
            <EmptyState
              icon={<NotebookPen size={22} />}
              title="No notes yet"
              description="Generate a set above. With no AI model configured you get a structured outline of your own material with page references, clearly labelled as such."
            />
          ) : (
            <div className="grid gap-4 lg:grid-cols-5">
              <Card className="lg:col-span-2" padded={false}>
                <div className="px-4 pb-2 pt-4">
                  <SectionHeading title="Your notes" />
                </div>
                <ul className="divide-y divide-[var(--border-subtle)]">
                  {notes.data?.map((note) => (
                    <li key={note.id}>
                      <button
                        type="button"
                        onClick={() => setSelected(note)}
                        className={[
                          'flex w-full items-start justify-between gap-2 px-4 py-2.5 text-left transition-colors',
                          selected?.id === note.id
                            ? 'bg-[var(--surface-selected)]'
                            : 'hover:bg-[var(--surface-hover)]',
                        ].join(' ')}
                      >
                        <span className="min-w-0">
                          <span className="block truncate text-[0.8125rem] font-medium text-[var(--text-primary)]">
                            {note.title}
                          </span>
                          <span className="text-[0.75rem] text-[var(--text-secondary)]">
                            {formatRelativeTime(note.updated_at)}
                          </span>
                        </span>
                        <span className="flex shrink-0 flex-col items-end gap-1">
                          <Badge tone="neutral">{note.mode}</Badge>
                          {note.edited_by_user ? <Badge tone="accent">edited</Badge> : null}
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              </Card>

              <div className="lg:col-span-3">
                {selected ? (
                  <Card>
                    <SectionHeading
                      title={selected.title}
                      description={
                        selected.generated_by === 'source_outline'
                          ? 'An outline assembled from your material — not written prose.'
                          : `Written by ${selected.generated_by.replace('llm:', '')}.`
                      }
                      action={
                        <div className="flex gap-1.5">
                          <Button
                            size="sm"
                            variant="ghost"
                            aria-label="Edit"
                            onClick={() => {
                              setEditing(selected);
                              setDraft(selected.body_markdown);
                            }}
                          >
                            <Pencil size={14} />
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            aria-label="Export as Markdown"
                            onClick={() => download(selected)}
                          >
                            <Download size={14} />
                          </Button>
                          <SaveToFolder note={selected} />
                          <Button
                            size="sm"
                            variant="ghost"
                            aria-label="Delete"
                            onClick={() => setConfirmDelete(selected)}
                          >
                            <Trash2 size={14} />
                          </Button>
                        </div>
                      }
                    />
                    {selected.generated_by === 'source_outline' ? (
                      <Callout tone="neutral" className="mt-3">
                        Each bullet is a passage from your own documents with its citation number.
                        Configure an AI model for notes written in prose.
                      </Callout>
                    ) : null}
                    <div className="mt-4">
                      <GroundedAnswer
                        markdown={selected.body_markdown}
                        citations={selected.citations ?? []}
                      />
                    </div>
                  </Card>
                ) : (
                  <EmptyState title="Select a note" description="Choose one from the list." />
                )}
              </div>
            </div>
          )}
        </div>

        <Dialog
          open={editing !== null}
          onClose={() => setEditing(null)}
          title="Edit notes"
          description="Your edits are kept; the note is marked as edited by you."
          width="lg"
          footer={
            <>
              <Button onClick={() => setEditing(null)}>Cancel</Button>
              <Button
                variant="primary"
                loading={updateNote.isPending}
                onClick={async () => {
                  if (!editing) return;
                  const updated = await updateNote.mutateAsync({
                    id: editing.id,
                    patch: { body_markdown: draft },
                  });
                  setSelected(updated);
                  setEditing(null);
                }}
              >
                Save
              </Button>
            </>
          }
        >
          <Textarea
            rows={18}
            value={draft}
            className="font-mono text-[0.8125rem]"
            onChange={(event) => setDraft(event.target.value)}
          />
        </Dialog>

        <Dialog
          open={confirmDelete !== null}
          onClose={() => setConfirmDelete(null)}
          title={`Delete "${confirmDelete?.title ?? ''}"?`}
          description="This cannot be undone."
          footer={
            <>
              <Button onClick={() => setConfirmDelete(null)}>Cancel</Button>
              <Button
                variant="danger"
                loading={deleteNote.isPending}
                onClick={async () => {
                  if (confirmDelete) {
                    await deleteNote.mutateAsync(confirmDelete.id);
                    if (selected?.id === confirmDelete.id) setSelected(null);
                  }
                  setConfirmDelete(null);
                }}
              >
                Delete
              </Button>
            </>
          }
        />
      </PageBody>
    </>
  );
}

/**
 * Save a note back next to the material: into "StudyPilot Notes" under a
 * folder linked on this Mac, or under a linked Drive folder. Only offered
 * when the note's course has such a folder.
 */
function SaveToFolder({ note }: { note: Note }) {
  const local = useLocalFolders();
  const drive = useGoogleDrive();
  const save = useSaveNote();
  const [choice, setChoice] = useState('');
  const [saved, setSaved] = useState<string | null>(null);

  const options: Array<{ value: string; label: string }> = [
    ...(local.data?.links ?? [])
      .filter((link) => link.course_id === note.course_id)
      .map((link) => ({
        value: `local:${link.id}`,
        label: `This Mac: …/${link.path.split('/').filter(Boolean).slice(-1)[0]}/StudyPilot Notes`,
      })),
    ...(drive.data?.connected && drive.data.can_write ? drive.data.links : [])
      .filter((link) => link.course_id === note.course_id)
      .map((link) => ({
        value: `drive:${link.id}`,
        label: `Google Drive: ${link.folder_name}/StudyPilot Notes`,
      })),
  ];
  if (options.length === 0) return null;
  const selected = choice || options[0]!.value;

  return (
    <div className="flex items-center gap-1">
      {options.length > 1 ? (
        <Select
          aria-label="Where to save"
          className="h-7 max-w-[14rem] text-[0.75rem]"
          value={selected}
          onChange={(event) => setChoice(event.target.value)}
        >
          {options.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </Select>
      ) : null}
      <Button
        size="sm"
        variant="ghost"
        icon={<FolderInput size={14} />}
        loading={save.isPending}
        title={options.find((o) => o.value === selected)?.label}
        onClick={() => {
          const [target, linkId] = selected.split(':') as ['local' | 'drive', string];
          save.mutate(
            { noteId: note.id, target, link_id: linkId },
            { onSuccess: (result) => setSaved(result.location) },
          );
        }}
      >
        Save to folder
      </Button>
      {saved ? (
        <span className="max-w-[16rem] truncate text-[0.6875rem] text-[var(--signal-grounded)]" title={saved}>
          Saved: {saved}
        </span>
      ) : null}
      {save.error ? (
        <span className="text-[0.6875rem] text-[var(--signal-danger)]">{describeError(save.error)}</span>
      ) : null}
    </div>
  );
}
