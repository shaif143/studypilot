/**
 * What the Library accepts from a file picker, a folder picker or a drop.
 *
 * A folder brings everything in it — `.DS_Store`, exported images, a stray
 * `.docx` — and the student should see what was taken and what was not,
 * with a reason, rather than have the rest vanish. The decision is made once
 * here, from the name and declared type; the backend checks the bytes.
 */

const SUPPORTED = /\.(pdf|pptx?|png|jpe?g|webp|gif|bmp|tiff?|wav|mp3|m4a|zip|rar)$/i;
const NEEDS_MODEL = /\.(png|jpe?g|webp|gif|bmp|tiff?|wav|mp3|m4a|zip|rar)$/i;
const ARCHIVE = /\.(zip|rar)$/i;

export const SUPPORTED_DESCRIPTION =
  'PDF, PowerPoint (.pptx and .ppt), PNG/JPEG/WebP/GIF/BMP/TIFF images, WAV/MP3/M4A audio, and .zip or .rar archives of those.';

export const ACCEPT_ATTRIBUTE = [
  'application/pdf',
  '.pdf',
  'application/vnd.openxmlformats-officedocument.presentationml.presentation',
  '.pptx',
  'application/vnd.ms-powerpoint',
  '.ppt',
  'application/zip',
  '.zip',
  'application/vnd.rar',
  'application/x-rar-compressed',
  '.rar',
  'image/png',
  'image/jpeg',
  'image/webp',
  'image/gif',
  'image/bmp',
  'image/tiff',
  '.png',
  '.jpg',
  '.jpeg',
  '.webp',
  '.gif',
  '.bmp',
  '.tif',
  '.tiff',
  'audio/wav',
  'audio/mpeg',
  'audio/mp4',
  '.wav',
  '.mp3',
  '.m4a',
].join(',');

export interface Rejected {
  file: File;
  reason: string;
}

export interface Partition {
  accepted: File[];
  rejected: Rejected[];
}

/** The path a folder picker reports, falling back to the plain name. */
export function relativePathOf(file: File): string {
  const withPath = file as File & { webkitRelativePath?: string };
  return withPath.webkitRelativePath || file.name;
}

function isSupported(file: File): boolean {
  if (file.type === 'application/pdf') return true;
  if (file.type.startsWith('image/') || file.type.startsWith('audio/')) {
    return SUPPORTED.test(file.name);
  }
  return SUPPORTED.test(file.name);
}

/** Split a selection into what can be uploaded and what cannot, saying why. */
export function partitionFiles(candidates: Iterable<File>): Partition {
  const accepted: File[] = [];
  const rejected: Rejected[] = [];
  const seen = new Set<string>();
  for (const file of candidates) {
    const path = relativePathOf(file);
    const base = file.name;
    // Finder metadata and other hidden files ride along with any folder.
    if (base.startsWith('.') || base.startsWith('~$')) continue;
    if (seen.has(path)) continue;
    seen.add(path);
    if (file.size === 0) {
      rejected.push({ file, reason: 'Empty file.' });
    } else if (!isSupported(file)) {
      rejected.push({ file, reason: 'Not a supported type.' });
    } else {
      accepted.push(file);
    }
  }
  accepted.sort((a, b) => relativePathOf(a).localeCompare(relativePathOf(b)));
  return { accepted, rejected };
}

/** Read by a model (or may contain files that are): the dialog then asks about cloud use. */
export function mayNeedModel(file: File): boolean {
  return NEEDS_MODEL.test(file.name);
}

/** A .zip or .rar: uploaded whole, unpacked and indexed member by member on the backend. */
export function isArchive(file: File): boolean {
  return ARCHIVE.test(file.name);
}

/** The file name without its extension, for a default document title. */
export function titleFor(file: File): string {
  return file.name.replace(/\.[^.]+$/, '');
}

// --- dropped folders ------------------------------------------------------------
// A drop of a folder arrives as a directory *entry*, not as files. Walking it
// is the only way to get at what is inside; browsers that lack the API just
// yield the files that were dropped directly.
function entryOf(item: DataTransferItem): FileSystemEntry | null {
  return typeof item.webkitGetAsEntry === 'function' ? item.webkitGetAsEntry() : null;
}

async function walkEntry(entry: FileSystemEntry, into: File[]): Promise<void> {
  if (entry.isFile) {
    const fileEntry = entry as FileSystemFileEntry;
    const file = await new Promise<File>((ok, fail) => fileEntry.file(ok, fail));
    // Carry the folder path the same way a folder picker does.
    Object.defineProperty(file, 'webkitRelativePath', {
      value: entry.fullPath.replace(/^\//, ''),
    });
    into.push(file);
    return;
  }
  if (!entry.isDirectory) return;
  const reader = (entry as FileSystemDirectoryEntry).createReader();
  // readEntries returns a batch at a time and an empty batch at the end.
  for (;;) {
    const batch = await new Promise<FileSystemEntry[]>((ok, fail) =>
      reader.readEntries(ok, fail),
    );
    if (batch.length === 0) break;
    for (const child of batch) await walkEntry(child, into);
  }
}

/** Every file in a drop, including those inside dropped folders. */
export async function filesFromDrop(transfer: DataTransfer): Promise<File[]> {
  const found: File[] = [];
  const entries = Array.from(transfer.items ?? []).map(entryOf);
  if (entries.some((entry) => entry !== null)) {
    for (const entry of entries) {
      if (entry) await walkEntry(entry, found);
    }
    return found;
  }
  return Array.from(transfer.files);
}
