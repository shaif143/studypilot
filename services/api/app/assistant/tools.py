"""What Claude may do in the chat: look at the student's courses and tidy them.

Every tool acts as the signed-in account and goes through the same
repositories the screens use, so ownership is checked in one place and the
chat can reach nothing the student could not reach by clicking. Nothing here
deletes a file: the most a tool removes is a folder, which leaves its files
in the course.

Each result is plain text whose first line says what happened in words a
person can read; the chat shows that line beside the step.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from app.runtime import RuntimeContext
from app.storage.models import AssistantAttachment, Course, CourseFolder, Document, User
from app.storage.repositories import (
    AssistantRepository,
    ChunkRepository,
    CourseRepository,
    DocumentRepository,
    FolderRepository,
    NoteRepository,
    NotFoundError,
    TopicRepository,
    UserRepository,
)

log = logging.getLogger(__name__)

SECTIONS = ("course", "assignments")
KINDS = ("lecture", "past_paper", "assignment", "textbook")
#: Most text one `read_file` call returns; the model asks again for the rest.
MAX_READ_CHARS = 60_000
MAX_PAGES_PER_READ = 40
MAX_SEARCH_RESULTS = 8
MAX_NOTE_CHARS = 60_000


def _tool(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict:
    return {
        "name": name,
        "description": description,
        # Inputs stream as they are written instead of arriving in one burst;
        # the API then leaves checking them to `validate`, below.
        "eager_input_streaming": True,
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": required,
        },
    }


_COURSE_ID = {"type": "string", "description": "A course id from list_courses."}
_FOLDER_ID = {"type": "string", "description": "A folder id from list_files."}
_DOCUMENT_ID = {"type": "string", "description": "A file id from list_files or search_materials."}

#: Sent with every request of a conversation, unchanged and in this order: the
#: model's thinking is signed against the tool set, so editing a description
#: here invalidates stored conversations until their thinking is dropped.
TOOLS: list[dict[str, Any]] = [
    _tool(
        "list_courses",
        "List the student's courses with their ids and how many files each holds. "
        "Call this first whenever a course is mentioned by name, to get its id.",
        {},
        [],
    ),
    _tool(
        "create_course",
        "Create a new, empty course.",
        {
            "title": {"type": "string", "description": "The course name."},
            "code": {"type": "string", "description": "Its code, such as CS101. Optional."},
            "description": {"type": "string", "description": "One or two lines. Optional."},
        },
        ["title"],
    ),
    _tool(
        "list_files",
        "List one course's folders and files: each file's id, title, kind, page count, "
        "and the folder it is in. Folders are in one of two sections: 'course' (lectures "
        "and other material) or 'assignments' (assignment briefs).",
        {"course_id": _COURSE_ID},
        ["course_id"],
    ),
    _tool(
        "create_folder",
        "Create a folder in a course. Use section 'course' for lectures and reading, "
        "'assignments' for assignment briefs.",
        {
            "course_id": _COURSE_ID,
            "name": {"type": "string", "description": "The folder name."},
            "section": {"type": "string", "enum": list(SECTIONS)},
        },
        ["course_id", "name"],
    ),
    _tool(
        "rename_folder",
        "Rename a folder.",
        {"folder_id": _FOLDER_ID, "name": {"type": "string"}},
        ["folder_id", "name"],
    ),
    _tool(
        "delete_folder",
        "Remove a folder. The files in it are not deleted: they stay in the course, "
        "outside any folder.",
        {"folder_id": _FOLDER_ID},
        ["folder_id"],
    ),
    _tool(
        "move_file",
        "Put a file into a folder of its own course, or take it out of its folder by "
        "passing an empty folder_id.",
        {
            "document_id": _DOCUMENT_ID,
            "folder_id": {
                "type": "string",
                "description": "The folder to file it in, or an empty string for no folder.",
            },
        },
        ["document_id", "folder_id"],
    ),
    _tool(
        "rename_file",
        "Change the title a file is shown under.",
        {"document_id": _DOCUMENT_ID, "title": {"type": "string"}},
        ["document_id", "title"],
    ),
    _tool(
        "save_attachment",
        "Add a file the student attached in this chat to a course, so it is kept, indexed "
        "and searchable there. Use it when the student asks to add, save, upload or file "
        "an attachment. Works for PDF, PowerPoint, Jupyter notebook, HTML, PNG, JPEG and "
        "WebP. The attachment_id is given beside each attached file.",
        {
            "attachment_id": {"type": "string"},
            "course_id": _COURSE_ID,
            "folder_id": {"type": "string", "description": "Optional folder to file it in."},
            "kind": {
                "type": "string",
                "enum": list(KINDS),
                "description": "What the file is. Defaults to lecture.",
            },
            "title": {"type": "string", "description": "Optional title; defaults to the filename."},
        },
        ["attachment_id", "course_id"],
    ),
    _tool(
        "search_materials",
        "Search the text of the student's course files and return the passages that match "
        "best, each with its file and page. Use it to answer questions from their material.",
        {
            "query": {"type": "string"},
            "course_id": {
                "type": "string",
                "description": "Limit the search to one course. Optional.",
            },
        },
        ["query"],
    ),
    _tool(
        "read_file",
        "Read the text of a course file, page by page. Returns up to 40 pages per call; "
        "ask again with a later first_page for more.",
        {
            "document_id": _DOCUMENT_ID,
            "first_page": {"type": "integer", "description": "Defaults to 1."},
            "last_page": {"type": "integer", "description": "Defaults to first_page + 9."},
        },
        ["document_id"],
    ),
    _tool(
        "write_note",
        "Save a note in Markdown to the student's My Notes for a course. Use it only when "
        "the student asks for something to be saved as a note.",
        {
            "course_id": _COURSE_ID,
            "title": {"type": "string"},
            "body_markdown": {"type": "string"},
        },
        ["course_id", "title", "body_markdown"],
    ),
]

_SCHEMAS = {tool["name"]: tool["input_schema"] for tool in TOOLS}
_TYPES: dict[str, type] = {"string": str, "integer": int}


def validate(name: str, args: object) -> str | None:
    """Why this input cannot be run, or ``None`` when it can.

    With eager input streaming the API does not check a tool's input, and a
    cut-off or malformed input can parse as a smaller valid-looking object, so
    it is checked against the schema here before anything acts on it.
    """
    schema = _SCHEMAS.get(name)
    if schema is None:
        return f"There is no tool called {name}."
    if not isinstance(args, dict):
        return "The input is not an object."
    for key in schema["required"]:
        if key not in args:
            return f"The input is missing '{key}'."
    for key, value in args.items():
        spec = schema["properties"].get(key)
        if spec is None:
            continue
        expected = _TYPES[spec["type"]]
        # bool is an int in Python; a page number of True is not a page number.
        if not isinstance(value, expected) or isinstance(value, bool):
            return f"'{key}' must be a {spec['type']}."
        if "enum" in spec and value not in spec["enum"]:
            return f"'{key}' must be one of: {', '.join(spec['enum'])}."
    return None


@dataclass
class ToolOutcome:
    text: str
    is_error: bool = False


def _clean_name(value: str, limit: int) -> str:
    return " ".join(value.split())[:limit]


class Toolbox:
    """The tools, bound to one account for one turn of the conversation."""

    def __init__(self, context: RuntimeContext, session: Any, user: User) -> None:
        self.context = context
        self.session = session
        self.user = user
        self.courses = CourseRepository(session)
        self.documents = DocumentRepository(session)
        self.folders = FolderRepository(session)
        self.notes = NoteRepository(session)
        self.chunks = ChunkRepository(session)
        self.topics = TopicRepository(session)
        self.users = UserRepository(session)
        self.assistant = AssistantRepository(session)

    # --- dispatch --------------------------------------------------------
    def run(self, name: str, args: object) -> ToolOutcome:
        problem = validate(name, args)
        if problem:
            # The raw input goes back so the model can see what it sent and retry.
            return ToolOutcome(
                f"{problem} Nothing was done.\n{json.dumps({'INVALID_JSON': json.dumps(args)})}",
                is_error=True,
            )
        assert isinstance(args, dict)
        try:
            outcome = getattr(self, f"_{name}")(**args)
            # Kept at once: a later step that fails must not undo this one.
            self.session.commit()
            return outcome
        except NotFoundError as exc:
            self.session.rollback()
            what = str(exc) or "item"
            return ToolOutcome(
                f"No {what} with that id belongs to this account. Nothing was done. "
                "Call list_courses or list_files to get the right id.",
                is_error=True,
            )
        except ValueError as exc:
            self.session.rollback()
            return ToolOutcome(f"{exc} Nothing was done.", is_error=True)
        except Exception:
            # Whatever the step had half-written goes, and the session is
            # usable again for the rest of the conversation.
            self.session.rollback()
            log.exception("assistant_tool_failed", extra={"tool": name})
            return ToolOutcome(
                "That step failed inside StudyPilot. Nothing was done.", is_error=True
            )

    # --- helpers ---------------------------------------------------------
    def _course(self, course_id: str) -> Course:
        try:
            return self.courses.get(self.user.id, course_id)
        except NotFoundError as exc:
            raise NotFoundError("course") from exc

    def _folder(self, folder_id: str) -> CourseFolder:
        try:
            return self.folders.get(self.user.id, folder_id)
        except NotFoundError as exc:
            raise NotFoundError("folder") from exc

    def _document(self, document_id: str) -> Document:
        try:
            return self.documents.get(self.user.id, document_id)
        except NotFoundError as exc:
            raise NotFoundError("file") from exc

    @staticmethod
    def _listing(summary: str, data: object) -> ToolOutcome:
        return ToolOutcome(f"{summary}\n{json.dumps(data, ensure_ascii=False)}")

    # --- courses ---------------------------------------------------------
    def _list_courses(self) -> ToolOutcome:
        rows = self.courses.list(self.user.id)
        data = []
        for course in rows:
            counts = self.courses.counts(self.user.id, course.id)
            data.append(
                {
                    "course_id": course.id,
                    "title": course.title,
                    "code": course.code,
                    "files": counts.get("documents", 0),
                }
            )
        noun = "course" if len(rows) == 1 else "courses"
        return self._listing(f"Found {len(rows)} {noun}.", data)

    def _create_course(self, title: str, code: str = "", description: str = "") -> ToolOutcome:
        title = _clean_name(title, 256)
        if not title:
            raise ValueError("A course needs a name.")
        course = self.courses.create(
            self.user.id,
            title=title,
            code=_clean_name(code, 64),
            description=description.strip()[:4000],
        )
        return self._listing(f'Created the course "{course.title}".', {"course_id": course.id})

    # --- files and folders -----------------------------------------------
    def _list_files(self, course_id: str) -> ToolOutcome:
        course = self._course(course_id)
        folders = self.folders.list(self.user.id, course.id)
        names = {folder.id: folder.name for folder in folders}
        documents = self.documents.list(self.user.id, course.id)
        data = {
            "course": course.title,
            "folders": [
                {"folder_id": folder.id, "name": folder.name, "section": folder.section}
                for folder in folders
            ],
            "files": [
                {
                    "document_id": document.id,
                    "title": document.title,
                    "kind": document.kind,
                    "pages": document.page_count,
                    "status": document.status,
                    "folder": names.get(document.folder_id or "", ""),
                }
                for document in documents
            ],
        }
        return self._listing(
            f'"{course.title}" has {len(documents)} files and {len(folders)} folders.', data
        )

    def _create_folder(self, course_id: str, name: str, section: str = "course") -> ToolOutcome:
        course = self._course(course_id)
        name = _clean_name(name, 200)
        if not name:
            raise ValueError("A folder needs a name.")
        for folder in self.folders.list(self.user.id, course.id):
            if folder.name.lower() == name.lower() and folder.section == section:
                return self._listing(
                    f'"{course.title}" already has a folder called "{folder.name}".',
                    {"folder_id": folder.id},
                )
        folder = self.folders.create(self.user.id, course.id, name=name, section=section)
        where = "assignments" if section == "assignments" else "files"
        return self._listing(
            f'Created the folder "{folder.name}" in the {where} of "{course.title}".',
            {"folder_id": folder.id},
        )

    def _rename_folder(self, folder_id: str, name: str) -> ToolOutcome:
        folder = self._folder(folder_id)
        name = _clean_name(name, 200)
        if not name:
            raise ValueError("A folder needs a name.")
        before = folder.name
        folder.name = name
        self.session.flush()
        return ToolOutcome(f'Renamed the folder "{before}" to "{name}".')

    def _delete_folder(self, folder_id: str) -> ToolOutcome:
        folder = self._folder(folder_id)
        name = folder.name
        self.folders.delete(self.user.id, folder.id)
        return ToolOutcome(f'Removed the folder "{name}". Its files are still in the course.')

    def _move_file(self, document_id: str, folder_id: str) -> ToolOutcome:
        document = self._document(document_id)
        if not folder_id:
            document.folder_id = None
            self.session.flush()
            return ToolOutcome(f'Took "{document.title}" out of its folder.')
        folder = self._folder(folder_id)
        if folder.course_id != document.course_id:
            raise ValueError("That folder belongs to another course.")
        document.folder_id = folder.id
        self.session.flush()
        return ToolOutcome(f'Moved "{document.title}" into the folder "{folder.name}".')

    def _rename_file(self, document_id: str, title: str) -> ToolOutcome:
        document = self._document(document_id)
        title = _clean_name(title, 512)
        if not title:
            raise ValueError("A file needs a title.")
        before = document.title
        document.title = title
        self.session.flush()
        return ToolOutcome(f'Renamed "{before}" to "{title}".')

    def _save_attachment(
        self,
        attachment_id: str,
        course_id: str,
        folder_id: str = "",
        kind: str = "lecture",
        title: str = "",
    ) -> ToolOutcome:
        # Imported here: the upload route owns the one path a file takes into
        # a course, and importing it at module load would be a cycle.
        from app.api.routes_documents import _ingest_upload
        from app.ingestion.pipeline import UnsupportedFileError
        from app.storage.files import BlobNotFoundError

        try:
            attachment: AssistantAttachment = self.assistant.attachment(self.user.id, attachment_id)
        except NotFoundError as exc:
            raise NotFoundError("attachment") from exc
        course = self._course(course_id)
        folder = self._folder(folder_id) if folder_id else None
        if folder is not None and folder.course_id != course.id:
            raise ValueError("That folder belongs to another course.")
        try:
            data = self.context.blobs.read_asset(attachment.asset_key)
        except (BlobNotFoundError, KeyError, FileNotFoundError) as exc:
            raise ValueError("That attachment is no longer stored; attach it again.") from exc
        try:
            result = _ingest_upload(
                filename=attachment.filename,
                data=data,
                title=title,
                kind=kind,
                # The chat only runs with cloud processing allowed, twice over.
                allow_cloud=True,
                course_id=course.id,
                context=self.context,
                user=self.user,
                documents=self.documents,
                chunks=self.chunks,
                topics=self.topics,
                users=self.users,
            )
        except UnsupportedFileError as exc:
            raise ValueError(str(exc)) from exc
        document = self._document(result.document.id)
        if folder is not None:
            document.folder_id = folder.id
            self.session.flush()
        where = f'the folder "{folder.name}" of "{course.title}"' if folder else f'"{course.title}"'
        if result.deduplicated:
            summary = f'"{document.title}" was already in {where}; the existing copy was kept.'
        elif result.status in {"indexed", "partial"}:
            summary = f'Added "{document.title}" to {where} ({result.page_count} pages).'
        else:
            detail = document.status_detail or "it could not be read"
            summary = f'Added "{document.title}" to {where}, but {detail}'
        return self._listing(
            summary,
            {"document_id": document.id, "status": result.status, "warnings": result.warnings},
        )

    # --- reading ---------------------------------------------------------
    def _search_materials(self, query: str, course_id: str = "") -> ToolOutcome:
        from app.agents.service import build_retriever

        query = query.strip()
        if not query:
            raise ValueError("The search needs some words.")
        course_ids = [self._course(course_id).id] if course_id else None
        outcome = build_retriever(self.context, self.session).retrieve(
            query, user_id=self.user.id, course_ids=course_ids, top_k=MAX_SEARCH_RESULTS
        )
        data = [
            {
                "document_id": chunk.document_id,
                "file": chunk.document_title,
                "page": chunk.page_number,
                "text": chunk.text[:1200],
            }
            for chunk in outcome.chunks[:MAX_SEARCH_RESULTS]
        ]
        if not data:
            return ToolOutcome(f'Nothing in the course files matched "{query}".')
        noun = "passage" if len(data) == 1 else "passages"
        return self._listing(f'Found {len(data)} {noun} for "{query}".', data)

    def _read_file(
        self, document_id: str, first_page: int = 1, last_page: int | None = None
    ) -> ToolOutcome:
        document = self._document(document_id)
        pages = self.documents.pages(self.user.id, document.id)
        if not pages:
            return ToolOutcome(f'"{document.title}" has no readable text.')
        first = max(1, first_page)
        last = last_page if last_page is not None else first + 9
        last = min(max(first, last), first + MAX_PAGES_PER_READ - 1)
        parts: list[str] = []
        used = 0
        shown_to = first - 1
        for page in pages:
            if page.page_number < first or page.page_number > last:
                continue
            text = f"--- page {page.page_number} ---\n{page.text.strip()}"
            if used + len(text) > MAX_READ_CHARS and parts:
                break
            parts.append(text)
            used += len(text)
            shown_to = page.page_number
        if not parts:
            return ToolOutcome(
                f'"{document.title}" has {len(pages)} pages; there is no page {first}.',
                is_error=True,
            )
        summary = f'Read pages {first} to {shown_to} of "{document.title}" ({len(pages)} pages).'
        if shown_to < len(pages):
            summary += f" Ask again with first_page {shown_to + 1} for more."
        return ToolOutcome(summary + "\n" + "\n\n".join(parts))

    def _write_note(self, course_id: str, title: str, body_markdown: str) -> ToolOutcome:
        course = self._course(course_id)
        title = _clean_name(title, 256)
        body = body_markdown.strip()
        if not title or not body:
            raise ValueError("A note needs a title and some text.")
        if len(body) > MAX_NOTE_CHARS:
            raise ValueError("That note is too long to save in one piece; split it.")
        note = self.notes.create(
            user_id=self.user.id,
            course_id=course.id,
            title=title,
            body_markdown=body,
            generated_by="claude-chat",
            citations=[],
        )
        return self._listing(
            f'Saved the note "{note.title}" to My Notes for "{course.title}".', {"note_id": note.id}
        )
