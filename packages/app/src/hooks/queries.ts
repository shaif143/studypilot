/**
 * Data hooks.
 *
 * Every server interaction in the application goes through one of these, so
 * cache invalidation is defined once per concept rather than per screen. The
 * key factory keeps keys consistent and makes targeted invalidation possible
 * (uploading a document invalidates that course, not the whole cache).
 */
import { ApiError } from '@studypilot/api-client';
import type {
  ArchiveUploadResult,
  Assignment,
  AttemptReview,
  ConceptMap,
  DriveImportRequest,
  DriveImportResult,
  DriveListing,
  GoogleDriveStatus,
  LocalFolderLink,
  LocalFolderLinkInput,
  LocalFolders,
  ProviderKeys,
  AssignmentPlan,
  AuthStatus,
  AnswerResponse,
  AskRequest,
  Assessment,
  Course,
  CourseInput,
  Dashboard,
  DocumentSummary,
  Flashcard,
  LearningSession,
  MasteryEstimate,
  Message,
  Note,
  NoteMode,
  NoteSaveRequest,
  NoteSaved,
  PageDetail,
  PageSummary,
  ProvidersResponse,
  QuizGenerateRequest,
  Recommendation,
  ResponseInput,
  Topic,
  UploadResult,
  UserSettings,
} from '@studypilot/shared';
import {
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
  type UseQueryOptions,
} from '@tanstack/react-query';

import { useClient } from '../context';

export const keys = {
  auth: ['auth'] as const,
  health: ['health'] as const,
  providers: ['providers'] as const,
  settings: ['settings'] as const,
  dashboard: ['dashboard'] as const,
  courses: ['courses'] as const,
  course: (id: string) => ['courses', id] as const,
  courseTopics: (id: string) => ['courses', id, 'topics'] as const,
  conceptMap: (id: string) => ['courses', id, 'concept-map'] as const,
  attemptReviews: (planId: string) => ['assignments', 'plans', planId, 'attempts'] as const,
  documents: (courseId?: string) => ['documents', courseId ?? 'all'] as const,
  document: (id: string) => ['documents', id] as const,
  pages: (id: string) => ['documents', id, 'pages'] as const,
  page: (id: string, page: number) => ['documents', id, 'pages', page] as const,
  documentTopics: (id: string) => ['documents', id, 'topics'] as const,
  sessions: ['sessions'] as const,
  session: (id: string) => ['sessions', id] as const,
  messages: (id: string) => ['sessions', id, 'messages'] as const,
  assessments: (courseId?: string, kind?: string) =>
    ['assessments', courseId ?? 'all', kind ?? 'all'] as const,
  assessment: (id: string) => ['assessments', id] as const,
  flashcards: (courseId?: string, dueOnly?: boolean) =>
    ['flashcards', courseId ?? 'all', dueOnly ? 'due' : 'all'] as const,
  notes: (courseId?: string) => ['notes', courseId ?? 'all'] as const,
  note: (id: string) => ['notes', id] as const,
  mastery: (courseId?: string) => ['mastery', courseId ?? 'all'] as const,
  recommendations: (courseId?: string) => ['recommendations', courseId ?? 'all'] as const,
  attention: (documentId: string) => ['attention', documentId] as const,
  providerKeys: ['providerKeys'] as const,
  googleDrive: ['googleDrive'] as const,
  localFolders: ['localFolders'] as const,
  driveFiles: (folder: string) => ['googleDrive', 'files', folder] as const,
  assignments: (courseId: string) => ['assignments', courseId] as const,
  assignmentPlan: (documentId: string) => ['assignments', 'plan', documentId] as const,
};

/** Anything that changes as a side effect of studying. */
const PROGRESS_KEYS = [
  keys.dashboard,
  ['mastery'],
  ['recommendations'],
  ['flashcards'],
] as const;

/**
 * Drop everything the previous session cached and re-read the auth status.
 *
 * Called after every sign-in, sign-up and sign-out, by both providers.
 *
 * `queryClient.clear()` cannot be used here, and using it was a real bug: it
 * removes the auth query as well, and the gate's observer is then left holding
 * the removed query's last value with nothing to re-subscribe to. Nothing
 * re-renders, `refetchQueries` finds no matching query, and the cache empties
 * while the screen stays exactly as it was — a sign-out that appeared to fail,
 * and a sign-in that never arrived.
 *
 * So the auth query is *reset* (kept, re-fetched, observers notified) while
 * everything else is removed outright — removed rather than refetched, so no
 * request goes out for a session that may no longer exist.
 */
export async function resetSessionCache(queryClient: QueryClient): Promise<void> {
  queryClient.removeQueries({ predicate: (query) => query.queryKey[0] !== keys.auth[0] });
  await queryClient.resetQueries({ queryKey: keys.auth });
}

export function useAuthStatus() {
  const client = useClient();
  return useQuery<AuthStatus>({
    queryKey: keys.auth,
    queryFn: () => client.authStatus(),
    // The gate renders off this; never serve it stale after a sign-in or out.
    staleTime: 0,
    retry: 1,
  });
}

export function useLogin() {
  const client = useClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ email, password }: { email: string; password: string }) =>
      client.login(email, password),
    // Everything cached belonged to the previous (signed-out) state, and the
    // gate must see the new status before this resolves.
    onSuccess: () => resetSessionCache(queryClient),
  });
}

export function useRegister() {
  const client = useClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      email,
      password,
      displayName,
    }: {
      email: string;
      password: string;
      displayName?: string;
    }) => client.register(email, password, displayName ?? ''),
    onSuccess: () => resetSessionCache(queryClient),
  });
}

export function useLogout() {
  const client = useClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => client.logout(),
    // On settle, not just success: if the call fails the local cache must
    // still be dropped rather than left showing another account's data.
    onSettled: () => resetSessionCache(queryClient),
  });
}

export function useHealth() {
  const client = useClient();
  return useQuery({
    queryKey: keys.health,
    queryFn: () => client.health(),
    // The backend may still be starting when the window opens.
    retry: 6,
    retryDelay: (attempt) => Math.min(1500, 250 * 2 ** attempt),
    staleTime: 30_000,
  });
}

export function useProviders() {
  const client = useClient();
  return useQuery<ProvidersResponse>({
    queryKey: keys.providers,
    queryFn: () => client.providers(),
    staleTime: 60_000,
  });
}

export function useSettings() {
  const client = useClient();
  return useQuery<UserSettings>({
    queryKey: keys.settings,
    queryFn: () => client.settings(),
    staleTime: 30_000,
  });
}

export function useUpdateSettings() {
  const client = useClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (patch: Partial<UserSettings>) => client.updateSettings(patch),
    onSuccess: (settings) => {
      queryClient.setQueryData(keys.settings, settings);
      // Provider availability depends on the cloud-processing consent flag.
      void queryClient.invalidateQueries({ queryKey: keys.providers });
    },
  });
}

export function useDashboard() {
  const client = useClient();
  return useQuery<Dashboard>({ queryKey: keys.dashboard, queryFn: () => client.dashboard() });
}

export function useCourses() {
  const client = useClient();
  return useQuery<Course[]>({ queryKey: keys.courses, queryFn: () => client.courses() });
}

export function useCourse(id: string | undefined) {
  const client = useClient();
  return useQuery<Course>({
    queryKey: keys.course(id ?? ''),
    queryFn: () => client.course(id as string),
    enabled: Boolean(id),
  });
}

export function useCreateCourse() {
  const client = useClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: CourseInput) => client.createCourse(input),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: keys.courses });
      void queryClient.invalidateQueries({ queryKey: keys.dashboard });
    },
  });
}

export function useUpdateCourse() {
  const client = useClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      patch,
    }: {
      id: string;
      patch: Partial<CourseInput> & { archived?: boolean };
    }) => client.updateCourse(id, patch),
    onSuccess: (course) => {
      queryClient.setQueryData(keys.course(course.id), course);
      void queryClient.invalidateQueries({ queryKey: keys.courses });
    },
  });
}

export function useDeleteCourse() {
  const client = useClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => client.deleteCourse(id),
    onSuccess: () => {
      void queryClient.invalidateQueries();
    },
  });
}

export function useDocuments(courseId?: string) {
  const client = useClient();
  return useQuery<DocumentSummary[]>({
    queryKey: keys.documents(courseId),
    queryFn: () => client.documents(courseId),
  });
}

export function useDocument(id: string | undefined) {
  const client = useClient();
  return useQuery<DocumentSummary>({
    queryKey: keys.document(id ?? ''),
    queryFn: () => client.document(id as string),
    enabled: Boolean(id),
  });
}

export function useUploadDocument() {
  const client = useClient();
  const queryClient = useQueryClient();
  return useMutation<
    UploadResult,
    Error,
    { courseId: string; file: File; title?: string; kind?: string; allowCloud?: boolean }
  >({
    mutationFn: ({ courseId, file, title, kind, allowCloud }) =>
      client.uploadDocument(courseId, file, { title, kind, allowCloud }),
    onSuccess: (_result, variables) => {
      void queryClient.invalidateQueries({ queryKey: keys.documents(variables.courseId) });
      void queryClient.invalidateQueries({ queryKey: keys.documents(undefined) });
      // A brief appears in the assignments list and a lecture in the concept
      // map; both read from their own queries. Found by the end-to-end test.
      void queryClient.invalidateQueries({ queryKey: keys.assignments(variables.courseId) });
      void queryClient.invalidateQueries({ queryKey: keys.conceptMap(variables.courseId) });
      void queryClient.invalidateQueries({ queryKey: keys.courseTopics(variables.courseId) });
      void queryClient.invalidateQueries({ queryKey: keys.courses });
      void queryClient.invalidateQueries({ queryKey: keys.dashboard });
    },
  });
}

export function useUploadArchive() {
  const client = useClient();
  const queryClient = useQueryClient();
  return useMutation<
    ArchiveUploadResult,
    Error,
    { courseId: string; file: File; kind?: string; allowCloud?: boolean }
  >({
    mutationFn: ({ courseId, file, kind, allowCloud }) =>
      client.uploadArchive(courseId, file, { kind, allowCloud }),
    onSuccess: (_result, variables) => {
      void queryClient.invalidateQueries({ queryKey: keys.documents(variables.courseId) });
      void queryClient.invalidateQueries({ queryKey: keys.documents(undefined) });
      void queryClient.invalidateQueries({ queryKey: keys.assignments(variables.courseId) });
      void queryClient.invalidateQueries({ queryKey: keys.conceptMap(variables.courseId) });
      void queryClient.invalidateQueries({ queryKey: keys.courseTopics(variables.courseId) });
      void queryClient.invalidateQueries({ queryKey: keys.courses });
      void queryClient.invalidateQueries({ queryKey: keys.dashboard });
    },
  });
}

export function useDeleteDocument() {
  const client = useClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => client.deleteDocument(id),
    onSuccess: () => {
      void queryClient.invalidateQueries();
    },
  });
}

export function usePages(documentId: string | undefined) {
  const client = useClient();
  return useQuery<PageSummary[]>({
    queryKey: keys.pages(documentId ?? ''),
    queryFn: () => client.pages(documentId as string),
    enabled: Boolean(documentId),
    staleTime: 5 * 60_000,
  });
}

export function usePage(documentId: string | undefined, pageNumber: number | undefined) {
  const client = useClient();
  return useQuery<PageDetail>({
    queryKey: keys.page(documentId ?? '', pageNumber ?? 0),
    queryFn: () => client.page(documentId as string, pageNumber as number),
    enabled: Boolean(documentId && pageNumber),
    staleTime: 5 * 60_000,
  });
}

export function useDocumentTopics(documentId: string | undefined) {
  const client = useClient();
  return useQuery<Topic[]>({
    queryKey: keys.documentTopics(documentId ?? ''),
    queryFn: () => client.documentTopics(documentId as string),
    enabled: Boolean(documentId),
    staleTime: 5 * 60_000,
  });
}

export function useConceptMap(courseId: string | undefined) {
  const client = useClient();
  return useQuery<ConceptMap>({
    queryKey: keys.conceptMap(courseId ?? ''),
    queryFn: () => client.conceptMap(courseId as string),
    enabled: Boolean(courseId),
  });
}

export function useCourseTopics(courseId: string | undefined) {
  const client = useClient();
  return useQuery<Topic[]>({
    queryKey: keys.courseTopics(courseId ?? ''),
    queryFn: () => client.courseTopics(courseId as string),
    enabled: Boolean(courseId),
  });
}

export function useSessions(limit = 20) {
  const client = useClient();
  return useQuery<LearningSession[]>({
    queryKey: keys.sessions,
    queryFn: () => client.sessions(limit),
  });
}

export function useResumeSession(documentId: string | undefined) {
  const client = useClient();
  return useQuery<LearningSession>({
    queryKey: ['sessions', 'resume', documentId ?? ''],
    queryFn: () => client.resumeSession(documentId as string),
    enabled: Boolean(documentId),
    // Resuming is a write; never serve it from a stale cache.
    gcTime: 0,
    staleTime: 0,
  });
}

export function useMessages(sessionId: string | undefined) {
  const client = useClient();
  return useQuery<Message[]>({
    queryKey: keys.messages(sessionId ?? ''),
    queryFn: () => client.messages(sessionId as string),
    enabled: Boolean(sessionId),
  });
}

export function useUpdateSession() {
  const client = useClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      patch,
    }: {
      id: string;
      patch: Parameters<typeof client.updateSession>[1];
    }) => client.updateSession(id, patch),
    onSuccess: (session) => {
      queryClient.setQueryData(keys.session(session.id), session);
      void queryClient.invalidateQueries({ queryKey: keys.sessions });
    },
  });
}

export function useAsk() {
  const client = useClient();
  const queryClient = useQueryClient();
  return useMutation<AnswerResponse, Error, AskRequest>({
    mutationFn: (request) => client.ask(request),
    onSuccess: (answer) => {
      void queryClient.invalidateQueries({ queryKey: keys.messages(answer.session_id) });
      void queryClient.invalidateQueries({ queryKey: keys.sessions });
    },
  });
}

export function useAssessments(courseId?: string, kind?: string) {
  const client = useClient();
  return useQuery<Assessment[]>({
    queryKey: keys.assessments(courseId, kind),
    queryFn: () => client.assessments({ course_id: courseId, kind }),
  });
}

export function useAssessment(id: string | undefined) {
  const client = useClient();
  return useQuery<Assessment>({
    queryKey: keys.assessment(id ?? ''),
    queryFn: () => client.assessment(id as string),
    enabled: Boolean(id),
  });
}

export function useGenerateAssessment() {
  const client = useClient();
  const queryClient = useQueryClient();
  return useMutation<Assessment, Error, QuizGenerateRequest>({
    mutationFn: (request) => client.generateAssessment(request),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['assessments'] });
    },
  });
}

export function useSubmitAssessment() {
  const client = useClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      responses,
      allowCloud,
    }: {
      id: string;
      responses: ResponseInput[];
      allowCloud?: boolean;
    }) => client.submitAssessment(id, responses, allowCloud ?? false),
    onSuccess: () => {
      for (const key of PROGRESS_KEYS) {
        void queryClient.invalidateQueries({ queryKey: key });
      }
    },
  });
}

export function useFlashcards(courseId?: string, dueOnly?: boolean) {
  const client = useClient();
  return useQuery<Flashcard[]>({
    queryKey: keys.flashcards(courseId, dueOnly),
    queryFn: () => client.flashcards({ course_id: courseId, due_only: dueOnly }),
  });
}

export function useGenerateFlashcards() {
  const client = useClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (request: Parameters<typeof client.generateFlashcards>[0]) =>
      client.generateFlashcards(request),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['flashcards'] });
      void queryClient.invalidateQueries({ queryKey: keys.dashboard });
    },
  });
}

export function useReviewFlashcard() {
  const client = useClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, rating }: { id: string; rating: number }) =>
      client.reviewFlashcard(id, rating),
    onSuccess: () => {
      for (const key of PROGRESS_KEYS) {
        void queryClient.invalidateQueries({ queryKey: key });
      }
    },
  });
}

export function useNotes(courseId?: string) {
  const client = useClient();
  return useQuery<Note[]>({
    queryKey: keys.notes(courseId),
    queryFn: () => client.notes(courseId),
  });
}

export function useGenerateNote() {
  const client = useClient();
  const queryClient = useQueryClient();
  return useMutation<
    Note,
    Error,
    {
      course_id: string;
      document_id?: string | null;
      mode: NoteMode;
      topic_slug?: string | null;
      allow_cloud?: boolean;
    }
  >({
    mutationFn: (request) => client.generateNote(request),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['notes'] });
    },
  });
}

export function useUpdateNote() {
  const client = useClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      patch,
    }: {
      id: string;
      patch: { title?: string; body_markdown?: string };
    }) => client.updateNote(id, patch),
    onSuccess: (note) => {
      queryClient.setQueryData(keys.note(note.id), note);
      void queryClient.invalidateQueries({ queryKey: ['notes'] });
    },
  });
}

export function useDeleteNote() {
  const client = useClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => client.deleteNote(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['notes'] });
    },
  });
}

export function useMastery(courseId?: string) {
  const client = useClient();
  return useQuery<MasteryEstimate[]>({
    queryKey: keys.mastery(courseId),
    queryFn: () => client.mastery(courseId),
  });
}

export function useRecommendations(courseId?: string) {
  const client = useClient();
  return useQuery<Recommendation[]>({
    queryKey: keys.recommendations(courseId),
    queryFn: () => client.recommendations(courseId),
  });
}

export function useDeleteAllData() {
  const client = useClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => client.deleteAllData(),
    onSuccess: () => {
      queryClient.clear();
    },
  });
}

/** Turn any thrown value into a sentence worth showing a student. */
export function describeError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.isUnauthorised) {
      // Deliberately neutral: this is reached both when a hosted session has
      // ended and when the desktop shell's token is stale, and the previous
      // wording ("Restart StudyPilot") was wrong on the web.
      return 'You are not signed in, or your session has ended.';
    }
    return error.message;
  }
  if (error instanceof Error) return error.message;
  return 'Something went wrong.';
}

export type { UseQueryOptions };


// --- assignment guides ------------------------------------------------------
export function useAssignments(courseId: string | undefined) {
  const client = useClient();
  return useQuery<Assignment[]>({
    queryKey: keys.assignments(courseId ?? ''),
    queryFn: () => client.assignments(courseId ?? ''),
    enabled: Boolean(courseId),
  });
}

export function useAssignmentPlan(documentId: string | undefined) {
  const client = useClient();
  return useQuery<AssignmentPlan>({
    queryKey: keys.assignmentPlan(documentId ?? ''),
    queryFn: () => client.assignmentPlan(documentId ?? ''),
    enabled: Boolean(documentId),
    // A 404 means "not drafted yet", which the screen handles; retrying it
    // would just delay that answer.
    retry: false,
  });
}

export function useDraftAssignmentPlan(courseId: string) {
  const client = useClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      documentId,
      allowCloud,
      provider,
    }: {
      documentId: string;
      allowCloud: boolean;
      provider?: string | null;
    }) => client.draftAssignmentPlan(documentId, { allowCloud, provider }),
    onSuccess: (plan) => {
      queryClient.setQueryData(keys.assignmentPlan(plan.document_id), plan);
      void queryClient.invalidateQueries({ queryKey: keys.assignments(courseId) });
    },
  });
}

export function useAttemptReviews(planId: string | undefined) {
  const client = useClient();
  return useQuery<AttemptReview[]>({
    queryKey: keys.attemptReviews(planId ?? ''),
    queryFn: () => client.attemptReviews(planId as string),
    enabled: Boolean(planId),
  });
}

export function useCheckAttempt(planId: string) {
  const client = useClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      stepIndex,
      text,
      allowCloud,
      provider,
    }: {
      stepIndex: number;
      text: string;
      allowCloud: boolean;
      provider?: string | null;
    }) => client.checkAttempt(planId, stepIndex, { text, allowCloud, provider }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: keys.attemptReviews(planId) });
    },
  });
}

export function useSetStepApproval(courseId: string) {
  const client = useClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      planId,
      stepIndex,
      approval,
    }: {
      planId: string;
      stepIndex: number;
      approval: 'approved' | 'declined';
    }) => client.setStepApproval(planId, stepIndex, approval),
    onSuccess: (plan) => {
      queryClient.setQueryData(keys.assignmentPlan(plan.document_id), plan);
      void queryClient.invalidateQueries({ queryKey: keys.assignments(courseId) });
    },
  });
}


// --- provider keys (desktop) --------------------------------------------------
export function useProviderKeys() {
  const client = useClient();
  return useQuery<ProviderKeys>({ queryKey: keys.providerKeys, queryFn: () => client.providerKeys() });
}

export function useSetProviderKey() {
  const client = useClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ provider, key }: { provider: string; key: string }) =>
      client.setProviderKey(provider, key),
    onSuccess: (result) => {
      queryClient.setQueryData(keys.providerKeys, result);
      // The provider's availability changed, so the catalogue must be re-read.
      void queryClient.invalidateQueries({ queryKey: keys.providers });
    },
  });
}

export function useDeleteProviderKey() {
  const client = useClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (provider: string) => client.deleteProviderKey(provider),
    onSuccess: (result) => {
      queryClient.setQueryData(keys.providerKeys, result);
      void queryClient.invalidateQueries({ queryKey: keys.providers });
    },
  });
}


// --- Google Drive --------------------------------------------------------------
export function useGoogleDrive(options: { refetchInterval?: number | false } = {}) {
  const client = useClient();
  return useQuery<GoogleDriveStatus>({
    queryKey: keys.googleDrive,
    queryFn: () => client.googleDrive(),
    refetchInterval: options.refetchInterval ?? false,
  });
}

export function useConnectGoogleDrive() {
  const client = useClient();
  return useMutation({
    mutationFn: (openBrowser: boolean) => client.connectGoogleDrive(openBrowser),
  });
}

export function useDisconnectGoogleDrive() {
  const client = useClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => client.disconnectGoogleDrive(),
    onSuccess: (status) => {
      queryClient.setQueryData(keys.googleDrive, status);
      void queryClient.invalidateQueries({ queryKey: ['googleDrive', 'files'] });
    },
  });
}

export function useDriveFiles(folder: string, enabled: boolean) {
  const client = useClient();
  return useQuery<DriveListing>({
    queryKey: keys.driveFiles(folder),
    queryFn: ({ signal }) => client.driveFiles(folder, signal),
    enabled,
    staleTime: 30_000,
    retry: false,
  });
}

/** After an import or sync: everything a new document touches, plus the links. */
function afterDriveImport(queryClient: QueryClient, result: DriveImportResult) {
  void queryClient.invalidateQueries({ queryKey: keys.googleDrive });
  void queryClient.invalidateQueries({ queryKey: keys.documents(result.course_id) });
  void queryClient.invalidateQueries({ queryKey: keys.documents(undefined) });
  void queryClient.invalidateQueries({ queryKey: keys.assignments(result.course_id) });
  void queryClient.invalidateQueries({ queryKey: keys.conceptMap(result.course_id) });
  void queryClient.invalidateQueries({ queryKey: keys.courseTopics(result.course_id) });
  void queryClient.invalidateQueries({ queryKey: keys.courses });
  void queryClient.invalidateQueries({ queryKey: keys.dashboard });
}

export function useImportFromDrive() {
  const client = useClient();
  const queryClient = useQueryClient();
  return useMutation<DriveImportResult, Error, DriveImportRequest>({
    mutationFn: (body) => client.importFromDrive(body),
    onSuccess: (result) => afterDriveImport(queryClient, result),
  });
}

export function useSyncDriveLink() {
  const client = useClient();
  const queryClient = useQueryClient();
  return useMutation<DriveImportResult, Error, string>({
    mutationFn: (linkId) => client.syncDriveLink(linkId),
    onSuccess: (result) => afterDriveImport(queryClient, result),
  });
}

export function useUnlinkDriveFolder() {
  const client = useClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (linkId: string) => client.unlinkDriveFolder(linkId),
    onSuccess: (status) => queryClient.setQueryData(keys.googleDrive, status),
  });
}


// --- folders on this Mac ------------------------------------------------------------
/** Everything a finished scan changes: documents, topics, maps, the counts. */
function afterFolderScan(queryClient: QueryClient) {
  void queryClient.invalidateQueries({ queryKey: ['documents'] });
  void queryClient.invalidateQueries({ queryKey: ['assignments'] });
  void queryClient.invalidateQueries({ queryKey: ['courses'] });
  void queryClient.invalidateQueries({ queryKey: keys.dashboard });
}

export function useLocalFolders() {
  const client = useClient();
  const queryClient = useQueryClient();
  return useQuery<LocalFolders>({
    queryKey: keys.localFolders,
    queryFn: async () => {
      const result = await client.localFolders();
      const previous = queryClient.getQueryData<LocalFolders>(keys.localFolders);
      const running = result.links.some(
        (link) => link.scan && ['queued', 'running'].includes(link.scan.state),
      );
      // A scan that just finished changed the library; re-read it once.
      const finishedNow = result.links.some((link) => {
        const was = previous?.links.find((p) => p.id === link.id)?.scan?.state;
        return link.scan?.state === 'done' && was !== undefined && was !== 'done';
      });
      // While it runs, rows go from queued to indexed one by one: keep the list live.
      if (finishedNow) afterFolderScan(queryClient);
      else if (running) void queryClient.invalidateQueries({ queryKey: ['documents'] });
      return result;
    },
    // Poll while any scan is running; otherwise sit still.
    refetchInterval: (query) =>
      query.state.data?.links.some((l) => l.scan && ['queued', 'running'].includes(l.scan.state))
        ? 1500
        : false,
  });
}

export function useLinkLocalFolder() {
  const client = useClient();
  const queryClient = useQueryClient();
  return useMutation<LocalFolderLink, Error, LocalFolderLinkInput>({
    mutationFn: (body) => client.linkLocalFolder(body),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: keys.localFolders }),
  });
}

export function useRescanLocalFolder() {
  const client = useClient();
  const queryClient = useQueryClient();
  return useMutation<LocalFolderLink, Error, string>({
    mutationFn: (linkId) => client.rescanLocalFolder(linkId),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: keys.localFolders }),
  });
}

export function useUnlinkLocalFolder() {
  const client = useClient();
  const queryClient = useQueryClient();
  return useMutation<LocalFolders, Error, string>({
    mutationFn: (linkId) => client.unlinkLocalFolder(linkId),
    onSuccess: (result) => {
      queryClient.setQueryData(keys.localFolders, result);
      afterFolderScan(queryClient);
    },
  });
}

export function useSaveNote() {
  const client = useClient();
  return useMutation<NoteSaved, Error, { noteId: string } & NoteSaveRequest>({
    mutationFn: ({ noteId, ...body }) => client.saveNote(noteId, body),
  });
}
