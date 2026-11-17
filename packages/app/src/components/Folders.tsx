/**
 * Folders a student makes to organise a course.
 *
 * A folder groups files; it holds none itself. Deleting one leaves its files
 * in the course, unfiled, which is why delete asks nothing: nothing is lost.
 * The same pieces serve the course's lectures and its assignments, told apart
 * by `section`.
 */
import type { CourseFolder, DocumentSummary } from '@studypilot/shared';
import { Button, Dialog, Input, Select } from '@studypilot/ui';
import { Folder, FolderPlus, Pencil, Trash2 } from 'lucide-react';
import { useState, type ReactNode } from 'react';

import { describeError, useFolderActions, useFolders, useSetDocumentFolder } from '../hooks/queries';

type Section = CourseFolder['section'];

/** "New folder", and the small dialog that names it. */
export function NewFolderButton({
  courseId,
  section,
  variant = 'ghost',
  size,
}: {
  courseId: string;
  section: Section;
  variant?: 'ghost' | 'secondary';
  size?: 'sm';
}) {
  const { create } = useFolderActions(courseId);
  const [open, setOpen] = useState(false);
  const [name, setName] = useState('');
  const close = () => {
    setOpen(false);
    setName('');
    create.reset();
  };
  const submit = () => {
    if (!name.trim()) return;
    create.mutate({ name, section }, { onSuccess: close });
  };

  return (
    <>
      <Button variant={variant} size={size} icon={<FolderPlus size={15} />} onClick={() => setOpen(true)}>
        New folder
      </Button>
      <Dialog
        open={open}
        onClose={close}
        width="sm"
        title="New folder"
        description={
          section === 'assignments'
            ? 'A folder for one assignment and the files that go with it.'
            : 'A folder to group this course’s files, such as a week or a topic.'
        }
        footer={
          <>
            <Button variant="ghost" onClick={close}>
              Cancel
            </Button>
            <Button variant="primary" loading={create.isPending} disabled={!name.trim()} onClick={submit}>
              Create folder
            </Button>
          </>
        }
      >
        <Input
          autoFocus
          aria-label="Folder name"
          placeholder={section === 'assignments' ? 'Coursework 1' : 'Week 1'}
          value={name}
          maxLength={200}
          onChange={(event) => setName(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') submit();
          }}
        />
        {create.error ? (
          <p className="mt-2 text-[0.75rem] text-[var(--signal-danger)]">
            {describeError(create.error)}
          </p>
        ) : null}
      </Dialog>
    </>
  );
}

/** A folder's heading row: its name, what is in it, and rename and delete. */
export function FolderHeading({
  folder,
  count,
  actions,
}: {
  folder: CourseFolder;
  count: number;
  actions?: ReactNode;
}) {
  const { rename, remove } = useFolderActions(folder.course_id);
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(folder.name);

  const save = () => {
    const next = name.trim();
    if (!next || next === folder.name) {
      setEditing(false);
      setName(folder.name);
      return;
    }
    rename.mutate({ id: folder.id, name: next }, { onSuccess: () => setEditing(false) });
  };

  return (
    <div className="flex flex-wrap items-center gap-2 px-1 pb-1.5 pt-1">
      <Folder size={15} className="shrink-0 text-[var(--text-accent)]" />
      {editing ? (
        <Input
          autoFocus
          aria-label="Folder name"
          className="h-7 max-w-xs text-[0.8125rem]"
          value={name}
          maxLength={200}
          onChange={(event) => setName(event.target.value)}
          onBlur={save}
          onKeyDown={(event) => {
            if (event.key === 'Enter') save();
            if (event.key === 'Escape') {
              setEditing(false);
              setName(folder.name);
            }
          }}
        />
      ) : (
        <h2 className="min-w-0 truncate text-[0.875rem] font-semibold text-[var(--text-primary)]">
          {folder.name}
        </h2>
      )}
      <span className="text-[0.75rem] tabular-nums text-[var(--text-muted)]">
        {count} file{count === 1 ? '' : 's'}
      </span>
      <span className="flex-1" />
      {actions}
      <Button
        size="sm"
        variant="ghost"
        aria-label={`Rename ${folder.name}`}
        onClick={() => setEditing(true)}
      >
        <Pencil size={13} />
      </Button>
      <Button
        size="sm"
        variant="ghost"
        aria-label={`Delete the folder ${folder.name}`}
        title="Delete this folder. The files in it stay in the course."
        loading={remove.isPending && remove.variables === folder.id}
        onClick={() => remove.mutate(folder.id)}
      >
        <Trash2 size={13} />
      </Button>
    </div>
  );
}

/** File a document in one of its course's folders, or take it out. */
export function MoveToFolder({
  courseId,
  document,
  section,
}: {
  courseId: string;
  document: DocumentSummary;
  section: Section;
}) {
  const folders = useFolders(courseId);
  const move = useSetDocumentFolder(courseId);
  const options = (folders.data ?? []).filter((folder) => folder.section === section);
  if (options.length === 0) return null;
  return (
    <label className="flex items-center gap-2 text-[0.75rem] text-[var(--text-secondary)]">
      <Folder size={13} className="shrink-0" />
      Folder
      <Select
        aria-label={`Folder for ${document.title}`}
        className="h-7 w-44 text-[0.75rem]"
        value={document.folder_id ?? ''}
        disabled={move.isPending}
        onChange={(event) => move.mutate({ id: document.id, folderId: event.target.value })}
      >
        <option value="">Not in a folder</option>
        {options.map((folder) => (
          <option key={folder.id} value={folder.id}>
            {folder.name}
          </option>
        ))}
      </Select>
    </label>
  );
}

/** Documents split into the folders of one section, in folder order, then the unfiled rest. */
export function groupByFolder<T extends { folder_id?: string | null }>(
  items: T[],
  folders: CourseFolder[],
  section: Section,
): { groups: Array<{ folder: CourseFolder; items: T[] }>; loose: T[] } {
  const mine = folders.filter((folder) => folder.section === section);
  const ids = new Set(mine.map((folder) => folder.id));
  return {
    groups: mine.map((folder) => ({
      folder,
      items: items.filter((item) => item.folder_id === folder.id),
    })),
    loose: items.filter((item) => !item.folder_id || !ids.has(item.folder_id)),
  };
}
