/**
 * Projects in the Claude chat.
 *
 * A project is somewhere to keep the chats about one piece of work, together
 * with what Claude should know for all of them: a set of instructions and a
 * few files. Every chat started in the project is given both.
 */
import type { AssistantFile, AssistantProjectDetail } from '@studypilot/shared';
import { Button, Dialog, EmptyState, Field, Input, Spinner, Textarea } from '@studypilot/ui';
import { FileText, FolderKanban, MessageSquare, Pencil, Plus, Trash2, Upload } from 'lucide-react';
import { type FormEvent, useEffect, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';

import {
  describeError,
  useAssistantActions,
  useAssistantProject,
  useAssistantProjects,
} from '../../hooks/queries';
import { formatBytes } from '../../lib/assistant';

function when(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
}

export function ProjectDialog({
  open,
  onClose,
  project,
}: {
  open: boolean;
  onClose: () => void;
  /** Present when editing; absent when making a new one. */
  project?: AssistantProjectDetail;
}) {
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={project ? 'Edit project' : 'New project'}
      description={
        project
          ? undefined
          : 'A place for the chats about one piece of work, with instructions and files they all share.'
      }
    >
      {/* Built afresh each time it opens, so it starts from what is saved. */}
      {open ? <ProjectForm project={project} onClose={onClose} /> : null}
    </Dialog>
  );
}

function ProjectForm({
  project,
  onClose,
}: {
  project?: AssistantProjectDetail;
  onClose: () => void;
}) {
  const navigate = useNavigate();
  const { createProject, updateProject } = useAssistantActions();
  const [name, setName] = useState(project?.name ?? '');
  const [description, setDescription] = useState(project?.description ?? '');
  const action = project ? updateProject : createProject;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const body = { name: name.trim(), description: description.trim() };
    if (!body.name) return;
    if (project) {
      updateProject.mutate({ id: project.id, ...body }, { onSuccess: onClose });
    } else {
      createProject.mutate(body, {
        onSuccess: (made) => {
          onClose();
          navigate(`/claude/projects/${made.id}`);
        },
      });
    }
  };

  return (
    <form onSubmit={submit} className="space-y-3">
      <Field label="Name">
        <Input
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder="Dissertation, Algorithms revision…"
          maxLength={200}
          autoFocus
        />
      </Field>
      <Field label="What is it for?" hint="Optional. Claude is told this too.">
        <Textarea
          value={description}
          onChange={(event) => setDescription(event.target.value)}
          rows={3}
          maxLength={2000}
        />
      </Field>
      {action.isError ? (
        <p className="text-[0.8125rem] text-[var(--signal-danger)]">
          {describeError(action.error)}
        </p>
      ) : null}
      <div className="flex justify-end gap-2 pt-1">
        <Button type="button" variant="ghost" onClick={onClose}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" loading={action.isPending} disabled={!name.trim()}>
          {project ? 'Save' : 'Create project'}
        </Button>
      </div>
    </form>
  );
}

export function ProjectsList() {
  const projects = useAssistantProjects();
  const [creating, setCreating] = useState(false);

  return (
    <div className="mx-auto w-full max-w-4xl px-6 py-8">
      <div className="flex items-center justify-between gap-3">
        <h1 className="font-[family-name:var(--font-serif)] text-[1.5rem] text-[var(--text-primary)]">
          Projects
        </h1>
        <Button variant="primary" icon={<Plus size={15} />} onClick={() => setCreating(true)}>
          New project
        </Button>
      </div>

      {projects.isPending ? (
        <div className="flex justify-center py-16 text-[var(--text-muted)]">
          <Spinner size={16} />
        </div>
      ) : projects.isError ? (
        <p className="mt-6 text-[0.8125rem] text-[var(--signal-danger)]">
          {describeError(projects.error)}
        </p>
      ) : projects.data.length === 0 ? (
        <div className="mt-8">
          <EmptyState
            icon={<FolderKanban size={22} />}
            title="No projects yet"
            description="Make one for a module, an essay or anything you will come back to. Its chats share the instructions and files you give it."
            action={
              <Button variant="primary" onClick={() => setCreating(true)}>
                New project
              </Button>
            }
          />
        </div>
      ) : (
        <ul className="mt-6 grid grid-cols-1 gap-3 @2xl:grid-cols-2">
          {projects.data.map((project) => (
            <li key={project.id}>
              <Link
                to={`/claude/projects/${project.id}`}
                className="block h-full rounded-[var(--radius-card)] border border-[var(--border-subtle)] bg-[var(--surface-raised)] p-4 transition-colors hover:border-[var(--border-strong)]"
              >
                <p className="truncate text-sm font-semibold text-[var(--text-primary)]">
                  {project.name}
                </p>
                <p className="mt-1 line-clamp-2 min-h-[2.4em] text-[0.8125rem] leading-relaxed text-[var(--text-secondary)]">
                  {project.description || 'No description.'}
                </p>
                <p className="mt-3 text-[0.75rem] text-[var(--text-muted)]">
                  {project.thread_count} {project.thread_count === 1 ? 'chat' : 'chats'} ·{' '}
                  {project.file_count} {project.file_count === 1 ? 'file' : 'files'} · updated{' '}
                  {when(project.updated_at)}
                </p>
              </Link>
            </li>
          ))}
        </ul>
      )}
      <ProjectDialog open={creating} onClose={() => setCreating(false)} />
    </div>
  );
}

export function ProjectPage({ projectId }: { projectId: string }) {
  const navigate = useNavigate();
  const project = useAssistantProject(projectId);
  const { deleteProject } = useAssistantActions();
  const [editing, setEditing] = useState(false);
  const [deleting, setDeleting] = useState(false);

  if (project.isPending) {
    return (
      <div className="flex justify-center py-16 text-[var(--text-muted)]">
        <Spinner size={16} />
      </div>
    );
  }
  if (project.isError) {
    return (
      <div className="mx-auto max-w-xl px-6 py-12 text-center">
        <p className="text-sm font-semibold text-[var(--text-primary)]">
          This project could not be opened
        </p>
        <p className="mt-1 text-[0.8125rem] text-[var(--text-secondary)]">
          {describeError(project.error)}
        </p>
        <Link to="/claude/projects" className="mt-3 inline-block text-sm text-[var(--text-accent)]">
          All projects
        </Link>
      </div>
    );
  }
  const data = project.data;

  return (
    <div className="mx-auto w-full max-w-5xl px-6 py-8">
      <Link to="/claude/projects" className="text-[0.8125rem] text-[var(--text-secondary)] hover:underline">
        All projects
      </Link>
      <div className="mt-2 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="font-[family-name:var(--font-serif)] text-[1.5rem] text-[var(--text-primary)]">
            {data.name}
          </h1>
          {data.description ? (
            <p className="mt-1 max-w-2xl text-[0.8125rem] leading-relaxed text-[var(--text-secondary)]">
              {data.description}
            </p>
          ) : null}
        </div>
        <div className="flex shrink-0 gap-2">
          <Button size="sm" variant="ghost" icon={<Pencil size={13} />} onClick={() => setEditing(true)}>
            Edit
          </Button>
          <Button size="sm" variant="ghost" icon={<Trash2 size={13} />} onClick={() => setDeleting(true)}>
            Delete
          </Button>
        </div>
      </div>

      <div className="mt-6 grid grid-cols-1 gap-6 @5xl:grid-cols-[minmax(0,1fr)_22rem]">
        <section aria-label="Chats in this project">
          <Button
            variant="primary"
            icon={<Plus size={15} />}
            onClick={() => navigate(`/claude?project=${data.id}`)}
          >
            New chat in this project
          </Button>
          {data.threads.length === 0 ? (
            <p className="mt-5 text-[0.8125rem] text-[var(--text-muted)]">
              No chats here yet. Start one and it will follow the instructions and see the files
              on the right.
            </p>
          ) : (
            <ul className="mt-4 divide-y divide-[var(--border-subtle)] rounded-[var(--radius-card)] border border-[var(--border-subtle)] bg-[var(--surface-raised)]">
              {data.threads.map((thread) => (
                <li key={thread.id}>
                  <Link
                    to={`/claude/chat/${thread.id}`}
                    className="flex items-center gap-3 px-4 py-2.5 hover:bg-[var(--surface-hover)]"
                  >
                    <MessageSquare size={14} className="shrink-0 text-[var(--text-muted)]" />
                    <span className="min-w-0 flex-1 truncate text-sm text-[var(--text-primary)]">
                      {thread.title}
                    </span>
                    <span className="shrink-0 text-[0.75rem] text-[var(--text-muted)]">
                      {when(thread.updated_at)}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </section>

        <aside className="space-y-5">
          <Instructions project={data} />
          <Files project={data} />
        </aside>
      </div>

      <ProjectDialog open={editing} onClose={() => setEditing(false)} project={data} />
      <Dialog
        open={deleting}
        onClose={() => setDeleting(false)}
        title={`Delete “${data.name}”?`}
        description="The project, its instructions and its files are removed. Its chats are kept, outside any project."
        footer={
          <>
            <Button variant="ghost" onClick={() => setDeleting(false)}>
              Cancel
            </Button>
            <Button
              variant="danger"
              loading={deleteProject.isPending}
              onClick={() =>
                deleteProject.mutate(data.id, { onSuccess: () => navigate('/claude/projects') })
              }
            >
              Delete project
            </Button>
          </>
        }
      />
    </div>
  );
}

function Instructions({ project }: { project: AssistantProjectDetail }) {
  const { updateProject } = useAssistantActions();
  const [value, setValue] = useState(project.instructions);
  const saved = useRef(project.instructions);

  useEffect(() => {
    // Another tab or a save changed it underneath: follow, unless it is being edited.
    if (project.instructions !== saved.current) {
      saved.current = project.instructions;
      setValue(project.instructions);
    }
  }, [project.instructions]);

  const dirty = value.trim() !== project.instructions;

  return (
    <section className="rounded-[var(--radius-card)] border border-[var(--border-subtle)] bg-[var(--surface-raised)] p-4">
      <h2 className="text-sm font-semibold text-[var(--text-primary)]">Instructions</h2>
      <p className="mt-0.5 text-[0.75rem] leading-relaxed text-[var(--text-muted)]">
        What Claude should know or do in every chat here: the module, how you like answers, what
        to avoid.
      </p>
      <Textarea
        value={value}
        onChange={(event) => setValue(event.target.value)}
        rows={7}
        maxLength={40_000}
        placeholder="For example: this is for my second-year databases module. Explain with small worked examples and use British spelling."
        className="mt-3"
        aria-label="Project instructions"
      />
      <div className="mt-2 flex items-center justify-between gap-2">
        <span className="text-[0.75rem] text-[var(--signal-danger)]">
          {updateProject.isError ? describeError(updateProject.error) : ''}
        </span>
        <Button
          size="sm"
          variant={dirty ? 'primary' : 'secondary'}
          disabled={!dirty}
          loading={updateProject.isPending}
          onClick={() => updateProject.mutate({ id: project.id, instructions: value.trim() })}
        >
          {dirty ? 'Save instructions' : 'Saved'}
        </Button>
      </div>
    </section>
  );
}

function Files({ project }: { project: AssistantProjectDetail }) {
  const { addProjectFile, removeProjectFile } = useAssistantActions();
  const input = useRef<HTMLInputElement>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(0);

  const add = async (picked: File[]) => {
    setProblem(null);
    setBusy((count) => count + picked.length);
    for (const file of picked) {
      try {
        await addProjectFile.mutateAsync({ projectId: project.id, file });
      } catch (error) {
        setProblem(`${file.name}: ${describeError(error)}`);
      } finally {
        setBusy((count) => count - 1);
      }
    }
  };

  return (
    <section className="rounded-[var(--radius-card)] border border-[var(--border-subtle)] bg-[var(--surface-raised)] p-4">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-[var(--text-primary)]">Files</h2>
        <Button
          size="sm"
          icon={busy > 0 ? undefined : <Upload size={13} />}
          loading={busy > 0}
          onClick={() => input.current?.click()}
        >
          Add
        </Button>
        <input
          ref={input}
          type="file"
          multiple
          hidden
          onChange={(event) => {
            void add(Array.from(event.target.files ?? []));
            event.target.value = '';
          }}
        />
      </div>
      <p className="mt-0.5 text-[0.75rem] leading-relaxed text-[var(--text-muted)]">
        PDFs, images and text files. Claude reads them in every chat of this project, so large
        files make every message cost more.
      </p>
      {problem ? (
        <p className="mt-2 text-[0.75rem] text-[var(--signal-danger)]">{problem}</p>
      ) : null}
      {project.files.length === 0 ? (
        <p className="mt-3 text-[0.8125rem] text-[var(--text-muted)]">No files yet.</p>
      ) : (
        <ul className="mt-3 space-y-1.5">
          {project.files.map((file: AssistantFile) => (
            <li
              key={file.id}
              className="flex items-center gap-2 rounded-lg border border-[var(--border-subtle)] px-2.5 py-1.5"
            >
              <FileText size={14} className="shrink-0 text-[var(--text-muted)]" />
              <span className="min-w-0 flex-1 truncate text-[0.8125rem] text-[var(--text-primary)]">
                {file.filename}
              </span>
              <span className="shrink-0 text-[0.6875rem] text-[var(--text-muted)]">
                {formatBytes(file.byte_size)}
              </span>
              <button
                type="button"
                aria-label={`Remove ${file.filename}`}
                title="Remove"
                onClick={() => removeProjectFile.mutate({ projectId: project.id, fileId: file.id })}
                className="shrink-0 rounded p-1 text-[var(--text-muted)] hover:bg-[var(--surface-hover)] hover:text-[var(--signal-danger)]"
              >
                <Trash2 size={13} />
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
