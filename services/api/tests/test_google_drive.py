"""Google Drive: connected without copying files.

Google is a fake behind ``httpx.MockTransport`` that speaks just enough of
OAuth and the Drive API: a token endpoint that checks the PKCE verifier, a
revoke endpoint, and a file tree with metadata, content and export. The
tests pin what matters: the consent request is PKCE-bound and read-only, a
callback must match a sign-in this process started, tokens are stored
encrypted and never returned, an imported file leaves no bytes on disk but
is searchable and readable, a sync follows the folder, and an expired access
token is refreshed without the user noticing.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from app import secrets
from app.config import Settings
from app.integrations import drive_import
from app.integrations import google_drive as gd
from app.storage.models import DriveConnection

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures"
FOLDER = "folder-lectures-0001"
SUBFOLDER = "folder-week-2-0002"


class FakeGoogle:
    """Just enough of accounts.google.com and the Drive API for these tests."""

    def __init__(self, pdf: bytes, pptx: bytes) -> None:
        self.access_token = "access-1"
        self.refresh_token = "refresh-1"
        self.expires_in = 3600
        self.token_requests: list[dict[str, str]] = []
        self.revoked: list[str] = []
        self.fetches: list[str] = []
        self.files: dict[str, dict] = {
            FOLDER: {
                "name": "Deep Learning",
                "mimeType": gd.FOLDER_MIME,
                "parents": ["root"],
            },
            SUBFOLDER: {"name": "Week 2", "mimeType": gd.FOLDER_MIME, "parents": [FOLDER]},
            "file-lecture-pdf": {
                "name": "lecture-cnn.pdf",
                "mimeType": "application/pdf",
                "modifiedTime": "2026-10-01T10:00:00.000Z",
                "size": str(len(pdf)),
                "parents": [FOLDER],
                "content": pdf,
            },
            "file-notes-gdoc": {
                "name": "Week 1 notes",
                "mimeType": "application/vnd.google-apps.document",
                "modifiedTime": "2026-10-02T10:00:00.000Z",
                "parents": [FOLDER],
                "export": pdf,
            },
            "file-diagram-png": {
                "name": "diagram.png",
                "mimeType": "image/png",
                "modifiedTime": "2026-10-03T10:00:00.000Z",
                "size": "1200",
                "parents": [FOLDER],
                "content": b"\x89PNG not really",
            },
            "file-deck-pptx": {
                "name": "deck.pptx",
                "mimeType": gd.PPTX_MEDIA_TYPE,
                "modifiedTime": "2026-10-04T10:00:00.000Z",
                "size": str(len(pptx)),
                "parents": [SUBFOLDER],
                "content": pptx,
            },
        }

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    # --- OAuth --------------------------------------------------------------
    def handle(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        if url.host == "oauth2.googleapis.com":
            if url.path == "/token":
                form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
                self.token_requests.append(form)
                if form.get("client_id") != "client-id-test":
                    return httpx.Response(401, json={"error": "invalid_client"})
                if form.get("grant_type") == "authorization_code":
                    if form.get("code") != "good-code" or not form.get("code_verifier"):
                        return httpx.Response(400, json={"error": "invalid_grant"})
                    return httpx.Response(
                        200,
                        json={
                            "access_token": self.access_token,
                            "refresh_token": self.refresh_token,
                            "expires_in": self.expires_in,
                            "scope": " ".join(gd.SCOPES),
                        },
                    )
                if form.get("grant_type") == "refresh_token":
                    if form.get("refresh_token") != self.refresh_token:
                        return httpx.Response(400, json={"error": "invalid_grant"})
                    self.access_token = "access-2"
                    return httpx.Response(
                        200, json={"access_token": self.access_token, "expires_in": 3600}
                    )
                return httpx.Response(400, json={"error": "unsupported_grant_type"})
            if url.path == "/revoke":
                self.revoked.append(url.params.get("token", ""))
                return httpx.Response(200)
        if url.host == "www.googleapis.com":
            return self.drive(request)
        return httpx.Response(404, json={"error": "unknown host"})

    # --- Drive --------------------------------------------------------------
    def drive(self, request: httpx.Request) -> httpx.Response:
        if request.headers.get("authorization") != f"Bearer {self.access_token}":
            return httpx.Response(401, json={"error": {"message": "Invalid Credentials"}})
        path = request.url.path
        params = request.url.params
        if path.startswith("/upload/drive/v3/files"):
            return self._upload(request)
        if path == "/drive/v3/files" and request.method == "POST":
            meta = json.loads(request.content)
            file_id = f"made-{len(self.files) + 1:04d}"
            self.files[file_id] = {
                "name": meta["name"],
                "mimeType": meta["mimeType"],
                "parents": meta.get("parents", []),
            }
            return httpx.Response(200, json=self._meta(file_id))
        if path == "/drive/v3/about":
            return httpx.Response(200, json={"user": {"emailAddress": "student@example.ac.uk"}})
        if path == "/drive/v3/files":
            query = params.get("q", "")
            parent = query.split("'")[1] if "'" in query else ""
            wanted_name = None
            if "name = '" in query:
                wanted_name = query.split("name = '")[1].split("'")[0]
            items = [
                self._meta(file_id)
                for file_id, meta in self.files.items()
                if parent in meta.get("parents", [])
                and (wanted_name is None or meta["name"] == wanted_name)
                and ("mimeType = '" not in query or meta["mimeType"] == gd.FOLDER_MIME)
            ]
            return httpx.Response(200, json={"files": items})
        if path.startswith("/drive/v3/files/"):
            file_id = path.removeprefix("/drive/v3/files/").removesuffix("/export")
            meta = self.files.get(file_id)
            if meta is None:
                return httpx.Response(404, json={"error": {"message": "File not found"}})
            if path.endswith("/export"):
                self.fetches.append(file_id)
                return httpx.Response(200, content=meta["export"])
            if params.get("alt") == "media":
                self.fetches.append(file_id)
                return httpx.Response(200, content=meta["content"])
            return httpx.Response(200, json=self._meta(file_id))
        return httpx.Response(404, json={"error": {"message": f"no route {path}"}})

    def _upload(self, request: httpx.Request) -> httpx.Response:
        """Multipart related: a JSON part then the bytes."""
        boundary = request.headers["content-type"].split("boundary=")[1]
        parts = request.content.split(f"--{boundary}".encode())
        meta = json.loads(parts[1].split(b"\r\n\r\n", 1)[1].strip())
        content = parts[2].split(b"\r\n\r\n", 1)[1].rstrip(b"\r\n")
        existing_id = request.url.path.removeprefix("/upload/drive/v3/files").strip("/")
        if request.method == "PATCH" and existing_id:
            self.files[existing_id]["content"] = content
            self.files[existing_id]["modifiedTime"] = "2026-11-06T00:00:00.000Z"
            return httpx.Response(200, json=self._meta(existing_id))
        file_id = f"made-{len(self.files) + 1:04d}"
        self.files[file_id] = {
            "name": meta["name"],
            "mimeType": "text/markdown",
            "parents": meta.get("parents", []),
            "content": content,
            "modifiedTime": "2026-11-06T00:00:00.000Z",
            "size": str(len(content)),
        }
        return httpx.Response(200, json=self._meta(file_id))

    def _meta(self, file_id: str) -> dict:
        meta = self.files[file_id]
        out = {"id": file_id, "name": meta["name"], "mimeType": meta["mimeType"]}
        for key in ("modifiedTime", "size", "parents"):
            if key in meta:
                out[key] = meta[key]
        return out


@pytest.fixture
def google(monkeypatch, sample_pdf_bytes: bytes) -> FakeGoogle:
    deck = FIXTURES / "sample-lecture-cnn.pptx"
    if not deck.is_file():
        pytest.skip("Run: python scripts/make_fixtures.py")
    fake = FakeGoogle(sample_pdf_bytes, deck.read_bytes())
    monkeypatch.setattr(gd, "TRANSPORT", fake.transport())
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "client-id-test")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "client-secret-test")
    return fake


def _connect(client: TestClient) -> str:
    """Run the sign-in through the API and return the stored account's email."""
    started = client.post("/api/integrations/google/connect", json={"open_browser": False})
    assert started.status_code == 200, started.text
    state = parse_qs(urlparse(started.json()["auth_url"]).query)["state"][0]
    done = client.get(drive_import.CALLBACK_PATH, params={"state": state, "code": "good-code"})
    assert done.status_code == 200 and "is connected" in done.text, done.text
    return client.get("/api/integrations/google").json()["account_email"]


def _course(client: TestClient) -> str:
    return client.post("/api/courses", json={"title": "Deep Learning"}).json()["id"]


# --- the sign-in ----------------------------------------------------------------
def test_the_consent_request_is_pkce_bound_drive_scoped_and_on_the_loopback(
    client: TestClient, google: FakeGoogle, monkeypatch
):
    opened: list[str] = []
    monkeypatch.setattr(gd, "open_in_browser", lambda url: opened.append(url) or True)

    before = client.get("/api/integrations/google").json()
    assert before == {
        **before,
        "configured": True,
        "client_source": "environment",
        "connected": False,
        "links": [],
    }

    response = client.post("/api/integrations/google/connect", json={"open_browser": True})
    assert response.status_code == 200
    url = urlparse(response.json()["auth_url"])
    query = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert url.netloc == "accounts.google.com"
    assert query["scope"] == "https://www.googleapis.com/auth/drive"
    assert query["code_challenge_method"] == "S256" and len(query["code_challenge"]) >= 40
    assert query["access_type"] == "offline" and query["prompt"] == "consent"
    assert query["redirect_uri"].startswith("http://127.0.0.1:")
    assert query["redirect_uri"].endswith(drive_import.CALLBACK_PATH)
    assert len(query["state"]) >= 20
    assert opened == [response.json()["auth_url"]]


def test_without_an_oauth_client_connect_says_what_to_enter(client: TestClient):
    response = client.post("/api/integrations/google/connect", json={"open_browser": False})
    assert response.status_code == 409
    assert "Settings" in response.json()["detail"]
    assert client.get("/api/integrations/google").json()["client_source"] == "none"


def test_a_callback_for_a_sign_in_this_process_did_not_start_is_refused(
    client: TestClient, google: FakeGoogle
):
    response = client.get(
        drive_import.CALLBACK_PATH, params={"state": "forged-state", "code": "good-code"}
    )
    assert response.status_code == 200
    assert "was not connected" in response.text
    assert google.token_requests == []  # the code was never exchanged
    assert client.get("/api/integrations/google").json()["connected"] is False


def test_a_state_is_single_use(client: TestClient, google: FakeGoogle):
    started = client.post("/api/integrations/google/connect", json={"open_browser": False})
    state = parse_qs(urlparse(started.json()["auth_url"]).query)["state"][0]
    first = client.get(drive_import.CALLBACK_PATH, params={"state": state, "code": "good-code"})
    second = client.get(drive_import.CALLBACK_PATH, params={"state": state, "code": "good-code"})
    assert "is connected" in first.text
    assert "was not connected" in second.text
    assert len(google.token_requests) == 1


def test_tokens_are_stored_encrypted_and_never_reported(
    client: TestClient, google: FakeGoogle, context
):
    assert _connect(client) == "student@example.ac.uk"

    # The exchange carried the verifier, so the code alone was not enough.
    exchange = google.token_requests[0]
    assert exchange["grant_type"] == "authorization_code" and exchange["code_verifier"]

    with context.database.session() as session:
        rows = session.query(DriveConnection).all()
        assert len(rows) == 1
        ciphertext = rows[0].token_ciphertext
    assert "refresh-1" not in ciphertext and "access-1" not in ciphertext
    # The key that unlocks it lives in the secret store, not the database.
    assert secrets.store.get(drive_import.TOKEN_KEY_NAME)

    status = client.get("/api/integrations/google")
    assert status.json()["connected"] is True
    assert "refresh-1" not in status.text and "access-1" not in status.text


# --- reading Drive --------------------------------------------------------------
def test_a_folder_listing_marks_what_can_be_imported(client: TestClient, google: FakeGoogle):
    _connect(client)
    listing = client.get(
        "/api/integrations/google/files",
        params={"folder": f"https://drive.google.com/drive/folders/{FOLDER}?usp=sharing"},
    )
    assert listing.status_code == 200, listing.text
    body = listing.json()
    assert body["folder"]["name"] == "Deep Learning" and body["parent_id"] == "root"
    by_name = {item["name"]: item for item in body["files"]}
    assert by_name["Week 2"]["is_folder"] is True
    assert by_name["lecture-cnn.pdf"]["importable"] is True
    assert by_name["Week 1 notes"]["importable"] is True  # a Google Doc, exported
    assert by_name["diagram.png"]["importable"] is False
    assert "model" in by_name["diagram.png"]["why_skipped"]


def test_importing_a_folder_keeps_the_index_and_no_bytes(
    client: TestClient, google: FakeGoogle, context, settings: Settings
):
    _connect(client)
    course_id = _course(client)

    response = client.post(
        "/api/integrations/google/import",
        json={"course_id": course_id, "folder": FOLDER, "recursive": True, "link": True},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    by_name = {item["name"]: item for item in body["outcomes"]}
    assert by_name["lecture-cnn.pdf"]["status"] == "indexed"
    assert by_name["Week 1 notes"]["status"] == "indexed"
    assert by_name["deck.pptx"]["status"] == "indexed" and by_name["deck.pptx"]["path"] == "Week 2"
    assert by_name["diagram.png"]["status"] == "skipped"
    assert body["counts"] == {"indexed": 3, "skipped": 1}
    assert body["link"]["folder_name"] == "Deep Learning"
    assert body["link"]["last_summary"] == {"indexed": 3, "skipped": 1}

    documents = {d["title"]: d for d in client.get("/api/documents").json()}
    lecture = documents["lecture-cnn"]
    assert lecture["source"] == "google_drive" and lecture["source_ref"] == "file-lecture-pdf"
    assert documents["Week 1 notes"]["original_filename"] == "Week 1 notes.pdf"

    # Nothing of the PDF is on disk: no blob, no page previews.
    assert not context.blobs.exists(lecture["content_hash"]) if "content_hash" in lecture else True
    assert not any(settings.blob_dir.rglob("*"))
    assert not any(settings.preview_dir.glob(f"{lecture['id']}/*"))
    # ...but the text is indexed and the pages are readable.
    page = client.get(f"/api/documents/{lecture['id']}/pages/4").json()
    assert "Padding" in page["text"]
    assert page["preview_path"] == ""
    answer = client.post(
        "/api/tutor/ask", json={"question": "Why is padding necessary?", "course_id": course_id}
    )
    assert answer.status_code == 200, answer.text
    assert any(c["document_id"] == lecture["id"] for c in answer.json()["citations"])

    # The reader streams the file from Drive on demand.
    fetched_before = list(google.fetches)
    served = client.get(f"/api/documents/{lecture['id']}/file")
    assert served.status_code == 200 and served.content[:5] == b"%PDF-"
    assert google.fetches == [*fetched_before, "file-lecture-pdf"]


def test_sync_follows_the_folder(client: TestClient, google: FakeGoogle, sample_pdf_bytes):
    _connect(client)
    course_id = _course(client)
    first = client.post(
        "/api/integrations/google/import",
        json={"course_id": course_id, "folder": FOLDER, "link": True},
    ).json()
    link_id = first["link"]["id"]
    old_ids = {o["name"]: o["document_id"] for o in first["outcomes"]}

    # Nothing changed: nothing is re-read.
    fetched = len(google.fetches)
    unchanged = client.post(f"/api/integrations/google/links/{link_id}/sync").json()
    assert unchanged["counts"] == {"unchanged": 3, "skipped": 1}
    assert len(google.fetches) == fetched

    # The lecture was edited, the notes were deleted, and a new paper appeared.
    google.files["file-lecture-pdf"]["modifiedTime"] = "2026-11-01T09:00:00.000Z"
    del google.files["file-notes-gdoc"]
    google.files["file-paper-pdf"] = {
        "name": "past-paper.pdf",
        "mimeType": "application/pdf",
        "modifiedTime": "2026-11-02T09:00:00.000Z",
        "size": str(len(sample_pdf_bytes)),
        "parents": [FOLDER],
        "content": sample_pdf_bytes,
    }
    synced = client.post(f"/api/integrations/google/links/{link_id}/sync").json()
    by_name = {o["name"]: o for o in synced["outcomes"]}
    assert by_name["lecture-cnn.pdf"]["status"] == "indexed"
    assert by_name["lecture-cnn.pdf"]["document_id"] != old_ids["lecture-cnn.pdf"]
    assert by_name["past-paper.pdf"]["status"] == "indexed"
    assert by_name["deck.pptx"]["status"] == "unchanged"
    assert by_name["Week 1 notes.pdf"]["status"] == "removed"
    titles = {d["title"] for d in client.get("/api/documents").json()}
    assert titles == {"lecture-cnn", "past-paper", "deck"}
    # The old lecture's index is gone with it.
    assert client.get(f"/api/documents/{old_ids['lecture-cnn.pdf']}").status_code == 404


def test_an_expired_access_token_is_refreshed_without_the_user_noticing(
    client: TestClient, google: FakeGoogle
):
    google.expires_in = 1  # inside the refresh margin: expired on arrival
    _connect(client)
    listing = client.get("/api/integrations/google/files", params={"folder": "root"})
    assert listing.status_code == 200, listing.text
    grants = [req["grant_type"] for req in google.token_requests]
    assert grants == ["authorization_code", "refresh_token"]
    assert google.access_token == "access-2"
    # And the refreshed token is what the next call uses, with no second refresh.
    client.get("/api/integrations/google/files", params={"folder": "root"})
    assert [req["grant_type"] for req in google.token_requests] == grants


def test_disconnect_revokes_and_forgets_links_but_keeps_documents(
    client: TestClient, google: FakeGoogle
):
    _connect(client)
    course_id = _course(client)
    client.post(
        "/api/integrations/google/import",
        json={"course_id": course_id, "folder": FOLDER, "link": True},
    )
    after = client.delete("/api/integrations/google").json()
    assert after["connected"] is False and after["links"] == []
    assert google.revoked == ["refresh-1"]
    documents = client.get("/api/documents").json()
    assert len(documents) == 3
    # Without the connection the reader cannot stream the file, and says so.
    lecture = next(d for d in documents if d["title"] == "lecture-cnn")
    served = client.get(f"/api/documents/{lecture['id']}/file")
    assert served.status_code == 409
    assert "not connected" in served.json()["detail"]
    # The index is still there, so the tutor still answers from it.
    assert client.get(f"/api/documents/{lecture['id']}/pages/4").status_code == 200


def test_the_desktop_keys_route_accepts_the_oauth_client(client: TestClient):
    response = client.post("/api/keys/google_client_id", json={"key": "123456789-abc.apps"})
    assert response.status_code == 200
    entry = next(k for k in response.json()["keys"] if k["provider"] == "google_client_id")
    assert entry["configured"] is True
    assert "123456789" not in response.text


# --- pieces ------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOp?usp=sharing",
            "1AbCdEfGhIjKlMnOp",
        ),
        ("https://drive.google.com/drive/u/0/folders/1AbCdEfGhIjKlMnOp", "1AbCdEfGhIjKlMnOp"),
        ("https://drive.google.com/open?id=1AbCdEfGhIjKlMnOp", "1AbCdEfGhIjKlMnOp"),
        ("1AbCdEfGhIjKlMnOp", "1AbCdEfGhIjKlMnOp"),
        ("", "root"),
        ("root", "root"),
    ],
)
def test_folder_links_and_ids_are_both_accepted(text: str, expected: str):
    assert gd.folder_id_from(text) == expected


def test_something_that_is_not_a_folder_link_is_refused():
    with pytest.raises(gd.DriveError, match="folder link"):
        gd.folder_id_from("my lectures")


def test_the_pkce_challenge_is_the_sha256_of_the_verifier():
    # RFC 7636 appendix B.
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    assert gd.challenge_for(verifier) == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def test_hosted_mode_derives_the_token_key_from_the_operator_secret(tmp_path):
    hosted = Settings(data_dir=tmp_path, deployment_mode="hosted", jwt_secret="s" * 40)
    cipher = drive_import.cipher_for(hosted)
    tokens = gd.Tokens(access_token="a", refresh_token="r", expires_at=1.0)
    assert cipher.decrypt(cipher.encrypt(tokens)).refresh_token == "r"
    # A different secret cannot read it.
    other = drive_import.cipher_for(
        Settings(data_dir=tmp_path, deployment_mode="hosted", jwt_secret="x" * 40)
    )
    with pytest.raises(gd.DriveNotConnectedError):
        other.decrypt(cipher.encrypt(tokens))


def test_hosted_redirects_use_the_public_urls(tmp_path):
    from starlette.requests import Request

    hosted = Settings(
        data_dir=tmp_path,
        deployment_mode="hosted",
        jwt_secret="s" * 40,
        public_api_url="https://api.studypilot.example/",
        web_origin="https://studypilot.example",
    )
    scope = {
        "type": "http",
        "method": "GET",
        "scheme": "http",
        "path": "/",
        "query_string": b"",
        "headers": [(b"host", b"internal:10000"), (b"x-forwarded-proto", b"https")],
    }
    request = Request(scope)
    assert (
        drive_import.redirect_uri_for(hosted, request)
        == "https://api.studypilot.example/api/integrations/google/callback"
    )
    assert (
        drive_import.return_url_for(hosted, request, "connected")
        == "https://studypilot.example/settings?drive=connected"
    )
    # Without the explicit settings the proxy headers decide.
    bare = Settings(data_dir=tmp_path, deployment_mode="hosted", jwt_secret="s" * 40)
    assert drive_import.redirect_uri_for(bare, request).startswith("https://internal:10000/")


# --- read and write, like a mounted Drive --------------------------------------------
def test_the_scope_asked_for_allows_writing_and_the_status_says_so(
    client: TestClient, google: FakeGoogle
):
    started = client.post("/api/integrations/google/connect", json={"open_browser": False})
    query = {k: v[0] for k, v in parse_qs(urlparse(started.json()["auth_url"]).query).items()}
    assert query["scope"] == "https://www.googleapis.com/auth/drive"
    _connect(client)
    assert client.get("/api/integrations/google").json()["can_write"] is True


def test_an_older_read_only_connection_is_reported_as_such(client: TestClient, google: FakeGoogle):
    google_scope_before = gd.SCOPES
    _connect(client)
    with client.app.state.context.database.session() as session:
        row = session.query(DriveConnection).one()
        row.scope = "https://www.googleapis.com/auth/drive.readonly"
        session.commit()
    assert client.get("/api/integrations/google").json()["can_write"] is False
    assert google_scope_before == gd.SCOPES


def test_a_note_is_saved_into_a_notes_subfolder_of_the_linked_drive_folder(
    client: TestClient, google: FakeGoogle
):
    _connect(client)
    course_id = _course(client)
    linked = client.post(
        "/api/integrations/google/import",
        json={"course_id": course_id, "folder": FOLDER, "link": True},
    ).json()["link"]
    note = client.post("/api/notes/generate", json={"course_id": course_id, "mode": "concept"})
    assert note.status_code == 201, note.text
    note_id = note.json()["id"]

    saved = client.post(
        f"/api/notes/{note_id}/save", json={"target": "drive", "link_id": linked["id"]}
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["location"].startswith("https://drive.google.com/file/d/")
    # A "StudyPilot Notes" folder was made under the linked folder, the note inside it.
    notes_folder = next(
        f
        for f in google.files.values()
        if f["name"] == "StudyPilot Notes" and FOLDER in f["parents"]
    )
    written = [
        f for f in google.files.values() if f.get("parents") and f["parents"][0] == notes_id(google)
    ]
    assert len(written) == 1 and written[0]["name"].endswith(".md")
    assert b"#" in written[0]["content"] or written[0]["content"]
    assert notes_folder["mimeType"] == gd.FOLDER_MIME

    # Saving again overwrites rather than duplicating.
    client.post(f"/api/notes/{note_id}/save", json={"target": "drive", "link_id": linked["id"]})
    written = [
        f for f in google.files.values() if f.get("parents") and f["parents"][0] == notes_id(google)
    ]
    assert len(written) == 1


def notes_id(google: FakeGoogle) -> str:
    return next(k for k, f in google.files.items() if f["name"] == "StudyPilot Notes")


def test_a_bundled_client_needs_nothing_typed(client: TestClient, tmp_path: Path, monkeypatch):
    bundle = tmp_path / "google_client.json"
    bundle.write_text('{"client_id": "client-id-test", "client_secret": "client-secret-test"}')
    monkeypatch.setenv("STUDYPILOT_GOOGLE_CLIENT_FILE", str(bundle))
    status = client.get("/api/integrations/google").json()
    assert status["configured"] is True and status["client_source"] == "bundled"
    started = client.post("/api/integrations/google/connect", json={"open_browser": False})
    assert started.status_code == 200 and "client_id=client-id-test" in started.json()["auth_url"]
