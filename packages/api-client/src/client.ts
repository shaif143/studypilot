import type {
  Account,
  AnswerResponse,
  Assessment,
  AskRequest,
  AuthStatus,
  AttemptResult,
  AttentionReport,
  Course,
  CourseInput,
  Dashboard,
  DocumentSummary,
  Flashcard,
  HealthResponse,
  LearningSession,
  MasteryEstimate,
  Message,
  Note,
  NoteMode,
  PageDetail,
  PageSummary,
  Platform,
  ProvidersResponse,
  QuizGenerateRequest,
  Recommendation,
  ResponseInput,
  Topic,
  UserSettings,
} from '@studypilot/shared';

import { ApiError, NetworkError } from './errors';

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PATCH' | 'DELETE';
  body?: unknown;
  query?: Record<string, string | number | boolean | undefined | null>;
  signal?: AbortSignal;
  /** Multipart payload; `body` is ignored when this is set. */
  form?: FormData;
}

/**
 * The single HTTP boundary for the whole application.
 *
 * Both hosts construct one of these with their own {@link Platform}. The token
 * is fetched lazily and cached, so the web host (which has none) pays nothing
 * and the desktop host asks the Rust shell once.
 */
export class StudyPilotClient {
  #tokenPromise: Promise<string | null> | null = null;

  constructor(readonly platform: Platform) {}

  get baseUrl(): string {
    return this.platform.apiBaseUrl;
  }

  /** Absolute URL for a binary resource rendered directly by the browser. */
  url(path: string): string {
    return `${this.platform.apiBaseUrl}${path}`;
  }

  async #token(): Promise<string | null> {
    this.#tokenPromise ??= this.platform.getApiToken().catch(() => null);
    return this.#tokenPromise;
  }

  /** Drop the cached token, e.g. after a 401 when the sidecar restarted. */
  resetToken(): void {
    this.#tokenPromise = null;
  }

  async request<T>(path: string, options: RequestOptions = {}): Promise<T> {
    const { method = 'GET', body, query, signal, form } = options;

    let url = `${this.platform.apiBaseUrl}${path}`;
    if (query) {
      const params = new URLSearchParams();
      for (const [key, value] of Object.entries(query)) {
        if (value !== undefined && value !== null) params.set(key, String(value));
      }
      const encoded = params.toString();
      if (encoded) url += `?${encoded}`;
    }

    const headers: Record<string, string> = {};
    const token = await this.#token();
    if (token) headers['x-studypilot-token'] = token;

    // A delegated identity (Neon Auth) supplies a short-lived bearer token.
    // Resolved per request, never cached here: these expire in 15 minutes and
    // the auth client refreshes them behind this call.
    if (this.platform.getBearerToken) {
      try {
        const bearer = await this.platform.getBearerToken();
        if (bearer) headers.authorization = `Bearer ${bearer}`;
      } catch {
        // No token means not signed in; the backend answers 401 and the app
        // shows its sign-in screen, which is the right outcome either way.
      }
    }

    if (body !== undefined && !form) headers['content-type'] = 'application/json';

    let response: Response;
    try {
      response = await fetch(url, {
        method,
        headers,
        body: form ?? (body === undefined ? undefined : JSON.stringify(body)),
        signal,
        // Hosted deployments authenticate with a session cookie and are served
        // same-origin (the frontend host rewrites /api/* to the backend), so
        // same-origin is both sufficient and the tighter of the two options.
        credentials: 'same-origin',
      });
    } catch (cause) {
      if (cause instanceof DOMException && cause.name === 'AbortError') throw cause;
      throw new NetworkError(
        'Could not reach the StudyPilot backend. Check that it is running.',
      );
    }

    if (response.status === 204) return undefined as T;

    if (!response.ok) {
      let detail = `Request failed with status ${response.status}`;
      let code: string | undefined;
      try {
        const payload = (await response.json()) as { detail?: unknown; code?: string };
        if (typeof payload.detail === 'string') {
          detail = payload.detail;
        } else if (Array.isArray(payload.detail)) {
          // FastAPI validation errors arrive as a list of per-field problems.
          detail = payload.detail
            .map((item) => {
              const entry = item as { loc?: unknown[]; msg?: string };
              const field = Array.isArray(entry.loc) ? entry.loc.slice(1).join('.') : '';
              return field ? `${field}: ${entry.msg ?? ''}` : (entry.msg ?? '');
            })
            .filter(Boolean)
            .join('; ');
        }
        code = payload.code;
      } catch {
        // Non-JSON error body; keep the status-based message.
      }
      if (response.status === 401) this.resetToken();
      throw new ApiError(detail, response.status, code);
    }

    const text = await response.text();
    return (text ? JSON.parse(text) : undefined) as T;
  }

  // --- health and providers -------------------------------------------
  health = () => this.request<HealthResponse>('/api/health');
  /** Unauthenticated liveness probe; used while the sidecar is starting. */
  liveness = () => this.request<{ status: string; version: string }>('/healthz');
  providers = () => this.request<ProvidersResponse>('/api/providers');

  // --- accounts (hosted deployments only) ------------------------------
  /**
   * Ask whether this deployment needs a sign-in, and whether we have one.
   *
   * The desktop backend does not mount these routes at all, so a 404 is the
   * correct, expected answer there and means "no accounts, carry on".
   */
  /**
   * Whether this deployment needs a sign-in, and who handles it.
   *
   * The desktop backend does not mount the account routes at all, so a 404
   * there genuinely means "this installation has no accounts" and the local
   * profile is the only user. That shortcut is restricted to the desktop
   * host on purpose: on a website a 404 means the API is missing or
   * misrouted, and answering "nobody needs to sign in" would open the whole
   * interface as though it were an authenticated session. A hosted
   * deployment fails closed — the error reaches the gate, which says the
   * backend is unreachable.
   */
  authStatus = async (): Promise<AuthStatus> => {
    try {
      return await this.request<AuthStatus>('/api/auth/status');
    } catch (error) {
      if (
        this.platform.kind === 'desktop' &&
        error instanceof ApiError &&
        error.isNotFound
      ) {
        return {
          authenticated: true,
          requires_authentication: false,
          registration_open: false,
          provider: 'builtin',
          account: null,
        };
      }
      throw error;
    }
  };

  register = (email: string, password: string, displayName = '') =>
    this.request<Account>('/api/auth/register', {
      method: 'POST',
      body: { email, password, display_name: displayName },
    });

  login = (email: string, password: string) =>
    this.request<Account>('/api/auth/login', {
      method: 'POST',
      body: { email, password },
    });

  logout = () => this.request<void>('/api/auth/logout', { method: 'POST' });

  logoutEverywhere = () =>
    this.request<void>('/api/auth/logout-everywhere', { method: 'POST' });

  changePassword = (currentPassword: string, newPassword: string) =>
    this.request<void>('/api/auth/change-password', {
      method: 'POST',
      body: { current_password: currentPassword, new_password: newPassword },
    });

  // --- settings --------------------------------------------------------
  settings = () => this.request<UserSettings>('/api/settings');
  updateSettings = (patch: Partial<UserSettings>) =>
    this.request<UserSettings>('/api/settings', { method: 'PATCH', body: patch });

  // --- courses ---------------------------------------------------------
  courses = () => this.request<Course[]>('/api/courses');
  course = (id: string) => this.request<Course>(`/api/courses/${id}`);
  createCourse = (input: CourseInput) =>
    this.request<Course>('/api/courses', { method: 'POST', body: input });
  updateCourse = (id: string, patch: Partial<CourseInput> & { archived?: boolean }) =>
    this.request<Course>(`/api/courses/${id}`, { method: 'PATCH', body: patch });
  deleteCourse = (id: string) =>
    this.request<void>(`/api/courses/${id}`, { method: 'DELETE' });
  courseTopics = (id: string) => this.request<Topic[]>(`/api/courses/${id}/topics`);

  // --- documents -------------------------------------------------------
  documents = (courseId?: string) =>
    this.request<DocumentSummary[]>('/api/documents', { query: { course_id: courseId } });
  document = (id: string) => this.request<DocumentSummary>(`/api/documents/${id}`);

  uploadDocument = (
    courseId: string,
    file: File,
    options: { title?: string; kind?: string } = {},
  ) => {
    const form = new FormData();
    form.append('file', file);
    if (options.title) form.append('title', options.title);
    if (options.kind) form.append('kind', options.kind);
    return this.request<import('@studypilot/shared').UploadResult>(
      `/api/courses/${courseId}/documents`,
      { method: 'POST', form },
    );
  };

  deleteDocument = (id: string) =>
    this.request<void>(`/api/documents/${id}`, { method: 'DELETE' });
  pages = (documentId: string) =>
    this.request<PageSummary[]>(`/api/documents/${documentId}/pages`);
  page = (documentId: string, pageNumber: number) =>
    this.request<PageDetail>(`/api/documents/${documentId}/pages/${pageNumber}`);
  documentTopics = (documentId: string) =>
    this.request<Topic[]>(`/api/documents/${documentId}/topics`);

  /** URL of the original PDF, for client-side rendering. */
  documentFileUrl = (documentId: string) => this.url(`/api/documents/${documentId}/file`);
  /** URL of a server-rendered page image, used as the reader's fallback. */
  pagePreviewUrl = (documentId: string, pageNumber: number) =>
    this.url(`/api/documents/${documentId}/pages/${pageNumber}/preview`);

  /**
   * Fetch a protected binary as a blob URL.
   *
   * `<img src>` and pdf.js cannot attach the token header, so authenticated
   * binaries are fetched here and handed to them as object URLs.
   */
  async fetchBlobUrl(path: string, signal?: AbortSignal): Promise<string> {
    const headers: Record<string, string> = {};
    const token = await this.#token();
    if (token) headers['x-studypilot-token'] = token;
    const response = await fetch(this.url(path), { headers, signal });
    if (!response.ok) throw new ApiError(`Could not load ${path}`, response.status);
    return URL.createObjectURL(await response.blob());
  }

  // --- tutor -----------------------------------------------------------
  ask = (request: AskRequest, signal?: AbortSignal) =>
    this.request<AnswerResponse>('/api/tutor/ask', {
      method: 'POST',
      body: request,
      signal,
    });

  sessions = (limit = 20) =>
    this.request<LearningSession[]>('/api/sessions', { query: { limit } });
  session = (id: string) => this.request<LearningSession>(`/api/sessions/${id}`);
  resumeSession = (documentId: string) =>
    this.request<LearningSession>('/api/sessions/resume', {
      method: 'POST',
      query: { document_id: documentId },
    });
  updateSession = (
    id: string,
    patch: {
      current_page?: number;
      current_topic?: string;
      learning_mode?: string;
      slides_since_checkpoint?: number;
    },
  ) => this.request<LearningSession>(`/api/sessions/${id}`, { method: 'PATCH', body: patch });
  messages = (sessionId: string) =>
    this.request<Message[]>(`/api/sessions/${sessionId}/messages`);
  deleteSession = (id: string) =>
    this.request<void>(`/api/sessions/${id}`, { method: 'DELETE' });

  // --- assessment ------------------------------------------------------
  generateAssessment = (request: QuizGenerateRequest) =>
    this.request<Assessment>('/api/assessments/generate', { method: 'POST', body: request });
  assessments = (query: { course_id?: string; kind?: string } = {}) =>
    this.request<Assessment[]>('/api/assessments', { query });
  assessment = (id: string) => this.request<Assessment>(`/api/assessments/${id}`);
  submitAssessment = (id: string, responses: ResponseInput[], allowCloud = false) =>
    this.request<AttemptResult>(`/api/assessments/${id}/submit`, {
      method: 'POST',
      body: { responses, allow_cloud: allowCloud },
    });
  attempt = (assessmentId: string, attemptId: string) =>
    this.request<AttemptResult>(`/api/assessments/${assessmentId}/attempts/${attemptId}`);

  // --- flashcards ------------------------------------------------------
  generateFlashcards = (request: {
    course_id: string;
    document_id?: string | null;
    topic_slug?: string | null;
    count?: number;
    allow_cloud?: boolean;
  }) => this.request<Flashcard[]>('/api/flashcards/generate', { method: 'POST', body: request });
  flashcards = (
    query: {
      course_id?: string;
      document_id?: string;
      due_only?: boolean;
      limit?: number;
    } = {},
  ) => this.request<Flashcard[]>('/api/flashcards', { query });
  reviewFlashcard = (id: string, rating: number) =>
    this.request<Flashcard>(`/api/flashcards/${id}/review`, {
      method: 'POST',
      body: { rating },
    });
  suspendFlashcard = (id: string) =>
    this.request<Flashcard>(`/api/flashcards/${id}/suspend`, { method: 'POST' });

  // --- notes -----------------------------------------------------------
  generateNote = (request: {
    course_id: string;
    document_id?: string | null;
    mode: NoteMode;
    topic_slug?: string | null;
    title?: string | null;
    allow_cloud?: boolean;
  }) => this.request<Note>('/api/notes/generate', { method: 'POST', body: request });
  notes = (courseId?: string) =>
    this.request<Note[]>('/api/notes', { query: { course_id: courseId } });
  note = (id: string) => this.request<Note>(`/api/notes/${id}`);
  updateNote = (id: string, patch: { title?: string; body_markdown?: string }) =>
    this.request<Note>(`/api/notes/${id}`, { method: 'PATCH', body: patch });
  deleteNote = (id: string) => this.request<void>(`/api/notes/${id}`, { method: 'DELETE' });

  // --- analytics -------------------------------------------------------
  dashboard = () => this.request<Dashboard>('/api/dashboard');
  mastery = (courseId?: string) =>
    this.request<MasteryEstimate[]>('/api/analytics/mastery', {
      query: { course_id: courseId },
    });
  recommendations = (courseId?: string) =>
    this.request<Recommendation[]>('/api/analytics/recommendations', {
      query: { course_id: courseId },
    });

  // --- attention (experimental) ----------------------------------------
  attentionReport = (documentId: string) =>
    this.request<AttentionReport>(`/api/attention/${documentId}`);
  recordAttention = (
    documentId: string,
    samples: Array<{
      page_number: number;
      element_index: number;
      dwell_ms: number;
      source?: 'simulated' | 'webcam';
    }>,
  ) =>
    this.request<AttentionReport>('/api/attention/samples', {
      method: 'POST',
      body: { document_id: documentId, samples },
    });
  deleteAttention = (documentId: string) =>
    this.request<{ deleted: boolean; detail: string }>(`/api/attention/${documentId}`, {
      method: 'DELETE',
    });

  // --- privacy ---------------------------------------------------------
  exportData = () => this.request<Record<string, unknown>>('/api/privacy/export');
  deleteAllData = () =>
    this.request<{ deleted: boolean; detail: string }>('/api/privacy/data', {
      method: 'DELETE',
    });
}
