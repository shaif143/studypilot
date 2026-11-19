/**
 * The API contract, mirroring `services/api/app/api/schemas.py`.
 *
 * Kept hand-written rather than generated so the types can carry the meaning
 * the UI depends on (for example that `generative: false` means the text was
 * quoted, not written). `npm run api:schema` dumps the backend's OpenAPI
 * document and `packages/api-client` has a test that every endpoint this client
 * calls still exists in it, so a backend change cannot drift silently.
 */

export type Theme = 'system' | 'light' | 'dark';
export type ExplanationLevel = 'simple' | 'intermediate' | 'advanced';
export type AssignmentPolicy = 'socratic' | 'explain' | 'off';
export type TutorMode = 'question' | 'explain' | 'assignment';
export type QuestionKind = 'mcq' | 'short_answer' | 'numeric' | 'explanation';
export type AssessmentKind = 'quiz' | 'mock_exam';
export type NoteMode = 'concept' | 'exam' | 'weakness';
export type MasteryLabel = 'unknown' | 'weak' | 'developing' | 'strong';
export type DocumentStatus = 'pending' | 'processing' | 'indexed' | 'partial' | 'failed';

/**
 * Where a question or card actually came from. Never invented: `past_paper`
 * only appears for material the student uploaded and labelled as a past paper.
 */
export type Provenance =
  | 'source_derived'
  | 'ai_generated'
  | 'past_paper'
  | 'past_paper_variation'
  | 'user';

export interface ProviderInfo {
  key: string;
  label: string;
  /** True when using this provider transmits course material to a third party. */
  sends_data_off_device: boolean;
  available: boolean;
  /** False for the offline provider, which quotes sources instead of writing. */
  generative: boolean;
  requires_api_key: boolean;
  detail: string;
  model: string;
  /** Which non-text briefs this provider can read. */
  accepts_images: boolean;
  accepts_audio: boolean;
  /** Models the user can pick; the first is the default. Empty when fixed. */
  models: ModelChoice[];
}

export interface ModelChoice {
  id: string;
  label: string;
}

export interface ProvidersResponse {
  llm: ProviderInfo[];
  embedding: ProviderInfo[];
  active_llm: string;
  /** The model the active provider will use for this user. */
  active_model: string;
  active_embedding: string;
  active_is_generative: boolean;
}

export interface UserSettings {
  theme: Theme;
  learning_style: string;
  explanation_level: ExplanationLevel;
  assignment_policy: AssignmentPolicy;
  /** The user's chosen generation provider; empty means the installation default. */
  preferred_llm_provider: string;
  /** Model id per provider key; a provider absent here uses its default. */
  preferred_llm_models: Record<string, string>;
  quiz_checkpoint_slides: number;
  allow_cloud_processing: boolean;
  allow_cloud_vision: boolean;
  allow_external_search: boolean;
  allow_telemetry: boolean;
  allow_attention_tracking: boolean;
  allow_federated_participation: boolean;
  retention_days: number | null;
}

export interface Course {
  id: string;
  title: string;
  code: string;
  description: string;
  institution: string;
  integrity_policy: string;
  archived: boolean;
  created_at: string;
  document_count: number;
  page_count: number;
  topic_count: number;
}

export interface CourseInput {
  title: string;
  code?: string;
  description?: string;
  institution?: string;
  integrity_policy?: string;
}

export interface ExtractionMeta {
  text_pages?: number;
  pages_with_equations?: number;
  pages_with_figures?: number;
  pages_with_tables?: number;
  extractor?: string;
  /** Folder path below the linked Drive folder, for a document read from Drive. */
  drive_path?: string;
  drive_mime_type?: string;
  /** Path below the linked folder on this Mac, for a document indexed in place. */
  folder_path?: string;
}

export interface DocumentSummary {
  id: string;
  course_id: string;
  title: string;
  original_filename: string;
  media_type: string;
  byte_size: number;
  page_count: number;
  status: DocumentStatus;
  status_detail: string;
  kind: string;
  embedding_model: string;
  extraction_meta: ExtractionMeta;
  /** "upload"; "google_drive" or "local_folder" when only the index is kept here. */
  source: 'upload' | 'google_drive' | 'local_folder';
  /** The file's id at its source; empty for an upload. */
  source_ref: string;
  /** The folder it is filed in within its course, if any. */
  folder_id?: string | null;
  created_at: string;
}

export interface UploadResult {
  document: DocumentSummary;
  status: DocumentStatus;
  page_count: number;
  chunk_count: number;
  topic_count: number;
  warnings: string[];
  duration_ms: number;
  deduplicated: boolean;
}

export interface ArchiveMemberResult {
  /** The member's path inside the archive. */
  path: string;
  status: 'indexed' | 'partial' | 'failed' | 'skipped';
  detail: string;
  result: UploadResult | null;
}

export interface ArchiveUploadResult {
  archive: string;
  members: ArchiveMemberResult[];
}

export type ElementKind = 'heading' | 'text' | 'table' | 'equation' | 'figure';

export interface PageElement {
  index: number;
  kind: ElementKind;
  /** [x0, y0, x1, y1] in PDF points, top-left origin. */
  bbox: [number, number, number, number] | number[];
  text: string;
  font_size: number;
  bold: boolean;
}

export interface PageDetail {
  page_number: number;
  heading: string;
  text: string;
  width: number;
  height: number;
  preview_path: string;
  has_equations: boolean;
  has_figures: boolean;
  has_tables: boolean;
  elements: PageElement[];
}

export interface PageSummary {
  page_number: number;
  heading: string;
  preview_path: string;
  has_equations: boolean;
  has_figures: boolean;
  has_tables: boolean;
}

export interface Topic {
  id: string;
  name: string;
  slug: string;
  summary: string;
  document_id: string | null;
  first_page: number;
  last_page: number;
}

export interface ConceptMapNode {
  id: string;
  topic_id: string;
  slug: string;
  name: string;
  document_id: string | null;
  first_page: number;
  mastery_label: 'weak' | 'developing' | 'strong' | 'unknown';
  mastery_probability: number | null;
  observations: number;
}

export interface ConceptMapEdge {
  source: string;
  target: string;
  kind: 'prerequisite' | 'next';
}

/** A course's topics as a graph, with a Mermaid rendering built server-side by code. */
export interface ConceptMap {
  nodes: ConceptMapNode[];
  edges: ConceptMapEdge[];
  mermaid: string;
  revisit_first: string[];
}

export interface Citation {
  marker: number;
  chunk_id: string;
  document_id: string;
  document_title?: string;
  page_number: number;
  section?: string;
  bbox: number[] | null;
  score: number;
  quote: string;
  cited_inline?: boolean;
}

export interface RetrievedSource {
  chunk_id: string;
  text: string;
  page_number: number;
  document_id: string;
  document_title: string;
  section: string;
  topic_slug: string;
  element_type: string;
  bbox: number[] | null;
  score: number;
  retrievers: string[];
}

export interface AskRequest {
  question: string;
  course_id?: string | null;
  document_id?: string | null;
  session_id?: string | null;
  page_number?: number | null;
  mode?: TutorMode;
  explanation_level?: ExplanationLevel | null;
  hint_level?: number;
  allow_cloud?: boolean;
  provider?: string | null;
}

export interface AnswerResponse {
  session_id: string;
  message_id: string;
  answer: string;
  citations: Citation[];
  sources: RetrievedSource[];
  /** Supported by retrieved material with at least one verified citation. */
  grounded: boolean;
  /** False when the text was quoted from sources rather than written. */
  generative: boolean;
  /** True when the tutor declined to answer rather than guessing. */
  abstained: boolean;
  provider: string;
  model: string;
  query_class: string;
  evidence_sufficient: boolean;
  evidence_note: string;
  recommended_action: string;
  recommendation_reason: string;
  input_tokens: number | null;
  output_tokens: number | null;
  warnings: string[];
}

export interface MessageMeta {
  provider?: string;
  model?: string;
  grounded?: boolean;
  generative?: boolean;
  abstained?: boolean;
  mode?: string;
  query_class?: string;
  evidence_sufficient?: boolean;
  warnings?: string[];
  /** The slide the question was asked on. */
  page?: number;
}

export interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  created_at: string;
  meta: MessageMeta;
  citations: Citation[];
}

export interface LearningSession {
  id: string;
  course_id: string | null;
  document_id: string | null;
  current_page: number;
  current_topic: string;
  learning_mode: string;
  recommended_action: string;
  slides_since_checkpoint: number;
  last_active_at: string;
  document_title: string;
  course_title: string;
}

export interface Question {
  id: string;
  ordinal: number;
  kind: QuestionKind;
  prompt: string;
  options: string[];
  marks: number;
  difficulty: string;
  topic_slug: string;
  provenance: Provenance;
  source_page: number | null;
}

export interface Assessment {
  id: string;
  course_id: string;
  document_id: string | null;
  kind: AssessmentKind;
  title: string;
  total_marks: number;
  time_limit_seconds: number | null;
  generator: string;
  created_at: string;
  questions: Question[];
  blueprint: Record<string, unknown>;
  warnings: string[];
}

export interface QuizGenerateRequest {
  course_id: string;
  document_id?: string | null;
  topic_slug?: string | null;
  page_from?: number | null;
  page_to?: number | null;
  question_count?: number;
  kinds?: QuestionKind[];
  difficulty?: 'easy' | 'balanced' | 'hard';
  kind?: AssessmentKind;
  time_limit_seconds?: number | null;
  allow_cloud?: boolean;
  /** Follow the course's past papers. Omitted means "if they have been analysed". */
  exam_style?: boolean | null;
}

export interface ResponseInput {
  question_id: string;
  selected_option?: number | null;
  answer_text?: string;
  numeric_value?: number | null;
}

export interface GradedResponse {
  question_id: string;
  awarded_marks: number;
  max_marks: number;
  correct: boolean | null;
  marking_method: 'deterministic' | 'rubric' | 'llm_rubric' | 'unmarked';
  feedback: string;
  expected_answer: string;
  correct_option: number | null;
  explanation: string;
  source_page: number | null;
}

export interface MasteryChange {
  topic_slug: string;
  topic_name: string;
  probability: number;
  previous_probability: number;
  label: MasteryLabel;
  confident: boolean;
  observations: number;
}

export interface AttemptResult {
  attempt_id: string;
  assessment_id: string;
  score: number;
  max_score: number;
  percentage: number;
  responses: GradedResponse[];
  weak_topics: string[];
  mastery_changes: MasteryChange[];
  recommended_action: string;
  recommendation_reason: string;
}

export interface Flashcard {
  id: string;
  course_id: string;
  document_id: string | null;
  topic_slug: string;
  front: string;
  back: string;
  difficulty: string;
  provenance: Provenance;
  source_page: number | null;
  ease: number;
  interval_days: number;
  repetitions: number;
  due_at: string;
}

export interface Note {
  id: string;
  course_id: string;
  document_id: string | null;
  title: string;
  mode: NoteMode;
  body_markdown: string;
  generated_by: string;
  edited_by_user: boolean;
  citations: Citation[];
  updated_at: string;
}

export interface MasteryEstimate {
  topic_slug: string;
  topic_name: string;
  probability: number;
  observations: number;
  correct_observations: number;
  uncertainty: number;
  label: MasteryLabel;
  confident: boolean;
}

export interface Recommendation {
  action: string;
  topic_slug: string;
  topic_name: string;
  reason: string;
  priority: number;
}

export interface AttemptSummary {
  attempt_id: string;
  assessment_id: string;
  score: number;
  max_score: number;
  percentage: number;
  submitted_at: string | null;
  weak_topics: string[];
}

export interface Dashboard {
  course_count: number;
  document_count: number;
  page_count: number;
  indexed_chunk_count: number;
  due_flashcards: number;
  upcoming_reviews: number;
  recent_sessions: LearningSession[];
  recent_documents: DocumentSummary[];
  mastery: MasteryEstimate[];
  weak_topics: MasteryEstimate[];
  recommendations: Recommendation[];
  recent_attempts: AttemptSummary[];
}

export interface AttentionReport {
  document_id: string;
  low_attention: Array<{
    page_number: number;
    element_index: number;
    dwell_ms: number;
    page_average_ms: number;
  }>;
  totals: Array<{
    page_number: number;
    element_index: number;
    dwell_ms: number;
    source: string;
  }>;
  enabled: boolean;
  note: string;
}

export interface Account {
  id: string;
  email: string;
  display_name: string;
}

export type AuthProvider = 'builtin' | 'neon';

export interface AuthStatus {
  authenticated: boolean;
  /** False on the desktop, where there is nothing to sign in to. */
  requires_authentication: boolean;
  registration_open: boolean;
  /** Which sign-in flow the frontend should present. */
  provider: AuthProvider;
  account: Account | null;
}

export interface HealthResponse {
  status: 'ok';
  version: string;
  runtime: {
    vector_backend: string;
    /** Null when the database could not be reached; `database_error` says why. */
    vector_count: number | null;
    database_error?: string | null;
    sparse_backend: string;
    storage_backend: string;
    database: string;
    deployment_mode: 'desktop' | 'hosted';
    embedding_model: string;
    embedding_dimensions: number;
    llm_provider: string;
    data_dir: string;
  };
}

// --- assignment guides ------------------------------------------------------

export type StepGrounding = 'slides' | 'beyond';
export type StepApproval = 'not_required' | 'pending' | 'approved' | 'declined';
export type RequirementKind = 'deliverable' | 'constraint' | 'criterion' | 'tool';
export type PlanStatus = 'ready' | 'needs_approval' | 'failed';

export interface PlanCitation {
  marker: number;
  chunk_id: string;
  document_id: string;
  document_title: string;
  page_number: number;
  section: string;
  quote: string;
}

export interface PlanStep {
  index: number;
  title: string;
  instruction: string;
  why: string;
  requirement_ids: string[];
  /** `slides` when grounded in this course's lecture material, else `beyond`. */
  grounding: StepGrounding;
  citations: PlanCitation[];
  approval: StepApproval;
  /** Shown on a step that goes beyond the slides; empty when grounded. */
  disclosure: string;
  coverage: number;
  model_flagged_beyond: boolean;
  beyond_reason: string;
}

export type AttemptVerdict = 'on_track' | 'needs_work' | 'off_track';

export interface AttemptIssue {
  what: string;
  why: string;
  /** Markers into the review's citations. */
  citations: number[];
}

/** A review of the student's attempt at one plan step: what is wrong, why, and one hint. */
export interface AttemptReview {
  id: string;
  plan_id: string;
  step_index: number;
  attempt_text: string;
  verdict: AttemptVerdict;
  summary: string;
  issues: AttemptIssue[];
  next_hint: string;
  /** 1 (the concept) to 5 (line by line); set by code from how often the step was asked about. */
  hint_level: number;
  citations: PlanCitation[];
  provider: string;
  model: string;
  warnings: string[];
  created_at: string;
}

export interface Requirement {
  id: string;
  text: string;
  kind: RequirementKind;
  quote: string;
}

export interface AssignmentPlan {
  id: string;
  course_id: string;
  document_id: string;
  status: PlanStatus;
  summary: string;
  requirements: Requirement[];
  steps: PlanStep[];
  provider: string;
  model: string;
  /** False: no language model drafted this — requirements with matching passages only. */
  generative: boolean;
  warnings: string[];
  input_tokens: number;
  output_tokens: number;
  created_at: string;
}

export interface Assignment {
  document: DocumentSummary;
  plan: AssignmentPlan | null;
}

// --- provider keys (desktop) --------------------------------------------------

export interface ProviderKeyStatus {
  provider: string;
  configured: boolean;
  /** Set outside the app; cannot be removed from inside it. */
  from_environment: boolean;
}

export interface ProviderKeys {
  /** False on a hosted deployment, where keys belong to the operator. */
  editable: boolean;
  keys: ProviderKeyStatus[];
  /** Where a key entered in the app is kept: "keychain" on macOS, or "file" as a fallback. */
  storage: string;
}

// --- Google Drive --------------------------------------------------------------

export interface DriveLink {
  id: string;
  course_id: string;
  folder_id: string;
  folder_name: string;
  recursive: boolean;
  last_synced_at: string | null;
  /** Counts from the last sync, keyed by outcome status. */
  last_summary: Record<string, number>;
}

export interface GoogleDriveStatus {
  /** An OAuth client is set up on this installation. */
  configured: boolean;
  /** Where that client came from: "bundled" means the app ships its own and nothing is typed. */
  client_source: 'bundled' | 'settings' | 'environment' | 'none';
  /** The desktop lets the user enter the client; a hosted site does not. */
  editable: boolean;
  connected: boolean;
  /** The connection may write; an older read-only one must be made again to save notes. */
  can_write: boolean;
  account_email: string;
  links: DriveLink[];
  /** What is kept here for a Drive file: only ever the search index. */
  keeps: 'index_only';
}

export interface DriveConnectResult {
  auth_url: string;
  redirect_uri: string;
}

export interface DriveFile {
  id: string;
  name: string;
  mime_type: string;
  modified_time: string;
  size: number;
  is_folder: boolean;
  importable: boolean;
  why_skipped: string;
  path: string;
}

export interface DriveListing {
  folder: DriveFile;
  parent_id: string;
  files: DriveFile[];
}

export interface DriveImportRequest {
  course_id: string;
  /** A folder id or link; empty means "just these files". */
  folder?: string;
  file_ids?: string[];
  recursive?: boolean;
  /** Keep the folder linked to the course for later syncs. */
  link?: boolean;
}

export type DriveImportStatus =
  | 'indexed'
  | 'partial'
  | 'failed'
  | 'skipped'
  | 'unchanged'
  | 'removed';

export interface DriveImportOutcome {
  file_id: string;
  name: string;
  path: string;
  status: DriveImportStatus;
  detail: string;
  document_id: string | null;
}

export interface DriveImportResult {
  course_id: string;
  outcomes: DriveImportOutcome[];
  counts: Partial<Record<DriveImportStatus, number>>;
  link: DriveLink | null;
  duration_ms: number;
}

// --- folders on this Mac ----------------------------------------------------

export interface LocalFolderScan {
  state: 'queued' | 'running' | 'done' | 'failed' | 'cancelled';
  /** Asked to stop; it finishes the file it is on first. */
  stopping?: boolean;
  /** Documents are read first; images and recordings, the slow part, last. */
  phase: 'documents' | 'media';
  done: number;
  total: number;
  current: string;
  counts: Record<string, number>;
  error: string;
}

export interface LocalFolderLink {
  id: string;
  course_id: string;
  path: string;
  recursive: boolean;
  /** Images and recordings go to the consent-gated model on each scan. */
  read_media: boolean;
  last_scanned_at: string | null;
  last_summary: Record<string, number>;
  /** Present while a scan runs, and for a few minutes after. */
  scan: LocalFolderScan | null;
}

export interface LocalFolders {
  /** False on a hosted deployment, which has no file system of the user's. */
  available: boolean;
  links: LocalFolderLink[];
}

export interface LocalFolderLinkInput {
  course_id: string;
  path: string;
  recursive?: boolean;
  read_media?: boolean;
}

export interface NoteSaveRequest {
  target: 'local' | 'drive';
  link_id: string;
}

export interface NoteSaved {
  target: string;
  /** The file's path on this Mac, or its Drive link. */
  location: string;
}

// --- slide guides -----------------------------------------------------------
export type GuideCardKind =
  | 'terms'
  | 'overview'
  | 'explanation'
  | 'equation'
  | 'visual'
  | 'example'
  | 'background'
  | 'in_class'
  | 'check';

/** One teaching slide written for a page of a lecture. */
export interface GuideCard {
  kind: GuideCardKind | string;
  title: string;
  markdown: string;
  /** True when the content is the model's own rather than the slide's. */
  beyond_slide: boolean;
}

/** A search link built by the backend; never an address a model wrote. */
export interface VideoLink {
  title: string;
  url: string;
}

export interface SlideGuide {
  document_id: string;
  page_number: number;
  cards: GuideCard[];
  videos: VideoLink[];
  /** "llm:<provider>" when a model wrote it, "source_outline" for the slide's own text. */
  generated_by: string;
  model: string;
  level: ExplanationLevel | string;
  saw_image: boolean;
  /** True when the class transcript was in front of the model too. */
  used_transcript?: boolean;
  /** False for the slide's own text, which is not kept. */
  saved: boolean;
  warnings: string[];
  updated_at: string | null;
}

export interface SlideGuideStatus {
  guide: SlideGuide | null;
}

export interface SlideGuideRequest {
  allow_cloud?: boolean;
  regenerate?: boolean;
  level?: ExplanationLevel | null;
}

/** A whole lecture being explained in the background. */
export interface GuideJob {
  state: 'queued' | 'running' | 'done' | 'failed' | 'cancelled';
  total: number;
  done: number;
  written: number;
  failed: number;
  error: string;
  /** Asked to stop; slides already with the model are still coming back. */
  stopping: boolean;
}

export interface GuidedPages {
  pages: number[];
  page_count?: number;
  job?: GuideJob | null;
  has_transcript?: boolean;
}

export interface ExplainAllRequest {
  allow_cloud?: boolean;
  level?: ExplanationLevel | null;
  /** Also rewrite the slides already explained. */
  regenerate?: boolean;
}

/** What was said in the class a lecture's slides were shown in. Never its text. */
export interface Transcript {
  document_id: string;
  filename: string;
  word_count: number;
  updated_at: string | null;
}

export interface TranscriptStatus {
  transcript: Transcript | null;
}

/** The student's own note on one slide. */
export interface SlideNote {
  document_id: string;
  page_number: number;
  text: string;
  updated_at: string | null;
}

export interface SlideNoteListItem extends SlideNote {
  document_title: string;
  course_id: string;
}

// --- past papers --------------------------------------------------------------
export interface ExamPaper {
  document_id: string;
  title: string;
  page_count: number;
  status: string;
}

export interface ExamSection {
  name: string;
  instructions: string;
  questions: number;
  marks_each: number;
  form: string;
}

export interface ExamTopicForecast {
  topic: string;
  /** 0 to 1. Never 1: a forecast, not a promise. */
  likelihood: number;
  pattern: string;
  /** Question references it was drawn from, like "2022 Q3(b)". */
  appearances: string[];
  typical_marks: number;
  form: string;
  rationale: string;
  next_variation: string;
}

export interface ExamProfileBody {
  papers_read: Array<{ title: string; year: string; questions: number }>;
  format: {
    style: 'mcq' | 'written' | 'mixed';
    summary: string;
    duration_minutes: number;
    total_marks: number;
    sections: ExamSection[];
  };
  question_mix: Record<string, number>;
  command_words: string[];
  topics: ExamTopicForecast[];
  patterns: string[];
  marking_habits: string[];
  advice: string[];
  confidence: 'low' | 'medium' | 'high';
  caveat: string;
}

export interface ExamProfile {
  course_id: string;
  papers: ExamPaper[];
  /** Null until the papers have been analysed. */
  profile: ExamProfileBody | null;
  /** "llm:<provider>" when a model analysed the papers, "counted" for the format only. */
  generated_by: string;
  model: string;
  /** True when papers were added, removed or changed since the analysis. */
  stale: boolean;
  warnings: string[];
  updated_at: string | null;
}

// --- folders ----------------------------------------------------------------
/** A folder the student made to organise a course's lectures or its assignments. */
export interface CourseFolder {
  id: string;
  course_id: string;
  name: string;
  section: 'course' | 'assignments';
  document_count: number;
}

// --- models working an assignment out together ------------------------------
/** One of the four models, and whether it can take part right now. */
export interface CouncilSeat {
  key: 'anthropic' | 'openai' | 'gemini' | 'ollama';
  name: string;
  model: string;
  ready: boolean;
  local: boolean;
  /** Why it cannot take part, or what using it means. */
  detail: string;
}

export interface CouncilIssue {
  id: number;
  reviewer: string;
  author: string;
  kind: 'mistake' | 'better';
  where: string;
  problem: string;
  reason: string;
  fix: string;
  /** How it ended: accepted by its author, upheld or dismissed by a judge, or left contested. */
  status: 'open' | 'accepted' | 'defended' | 'upheld' | 'dismissed' | 'contested';
  response: string;
  judge: string;
  ruling: string;
  /** "review" for an issue with a draft; "vote" for an objection to the agreed version. */
  stage?: 'review' | 'vote';
  round?: number;
}

export interface CouncilObjection {
  member: string;
  where: string;
  problem: string;
  reason: string;
  fix: string;
  /** For an overruled objection: who ruled, and why. */
  judge?: string;
  ruling?: string;
}

/** One thing said in a discussion, in the order it was said. */
export type CouncilEvent =
  | { type: 'draft'; phase: string; member: string; markdown: string }
  | { type: 'review'; phase: string; member: string; issues: CouncilIssue[]; best: string }
  | {
      type: 'defence';
      phase: string;
      member: string;
      responses: Array<{ issue: number; stance: string; reason: string }>;
    }
  | {
      type: 'ruling';
      phase: string;
      member: string;
      rulings: Array<{ issue: number; ruling: string; reason: string }>;
    }
  | { type: 'synthesis'; phase: string; member: string; markdown: string }
  | {
      type: 'vote';
      phase: string;
      member: string;
      round: number;
      vote: 'agree' | 'object';
      objections: CouncilObjection[];
    }
  | { type: 'revision'; phase: string; member: string; round: number; markdown: string }
  | { type: 'error'; phase: string; member: string; text: string }
  | { type: 'issues'; phase: string; issues: CouncilIssue[] };

export interface CouncilConsensus {
  /** Every model voted for it. */
  unanimous?: boolean;
  /** Nothing is left open: everyone agreed, or what was objected to was ruled against. */
  settled?: boolean;
  /** Objections to the agreed version that a neutral model ruled against. */
  overruled?: CouncilObjection[];
  rounds?: number;
  lead?: string;
  agreed?: string[];
  took_part?: string[];
  dropped?: Array<{ member: string; phase: string; reason: string }>;
  objections?: CouncilObjection[];
  issues_raised?: number;
  issues_accepted?: number;
  issues_upheld?: number;
  issues_dismissed?: number;
  issues_contested?: number;
}

export interface CouncilRun {
  id: string;
  document_id: string;
  kind: 'guide' | 'solution';
  status: 'queued' | 'running' | 'done' | 'failed' | 'cancelled';
  phase: string;
  phase_detail: string;
  members: Array<{ key: string; name: string; model: string; local: boolean }>;
  reference_document_ids: string[];
  citations: Citation[];
  events: CouncilEvent[];
  result_markdown: string;
  consensus: CouncilConsensus;
  warnings: string[];
  error: string;
  created_at: string | null;
  updated_at: string | null;
}

export interface CouncilState {
  seats: CouncilSeat[];
  guide: CouncilRun | null;
  solution: CouncilRun | null;
}

export interface CouncilStartRequest {
  allow_cloud?: boolean;
  /** The files to use as references. Empty means every lecture in the course. */
  reference_document_ids?: string[];
  members?: Array<CouncilSeat['key']>;
}

// --- the Claude chat ---------------------------------------------------------
export interface AssistantModel {
  id: string;
  label: string;
  detail: string;
}

/** Whether this account has a Claude API key. The value is never sent back. */
export interface AssistantKey {
  configured: boolean;
  /** "account" (saved in the chat) or "installation" (this machine's own key). */
  source: '' | 'account' | 'installation';
  /** The last four characters of the saved key. */
  hint: string;
}

export interface AssistantStatus {
  key: AssistantKey;
  models: AssistantModel[];
  default_model: string;
  /** The operator's switch: may anything here go to a cloud model at all. */
  installation_allows_cloud: boolean;
  /** This account's own switch, in Settings. */
  account_allows_cloud: boolean;
  /** True when a message can be sent. */
  ready: boolean;
  max_upload_bytes: number;
}

export interface AssistantFile {
  id: string;
  filename: string;
  kind: 'image' | 'pdf' | 'text' | 'file';
  media_type: string;
  byte_size: number;
  created_at?: string | null;
  /** False once the stored bytes are gone (message attachments only). */
  available?: boolean;
}

/** One step Claude took: a tool call and, once it has run, what came of it. */
export interface AssistantToolBlock {
  type: 'tool';
  id: string;
  name: string;
  /** Plain text; its first line says what happened. */
  result: string;
  is_error: boolean;
  done: boolean;
}

export type AssistantBlock = { type: 'text'; text: string } | AssistantToolBlock;

export interface AssistantItem {
  role: 'user' | 'assistant';
  /** The student's own words (their messages only). */
  text: string;
  attachments: AssistantFile[];
  /** An assistant reply in order: text, and the steps it took. */
  blocks: AssistantBlock[];
}

export interface AssistantThreadSummary {
  id: string;
  title: string;
  model: string;
  project_id: string | null;
  created_at: string;
  updated_at: string;
  running: boolean;
}

export interface AssistantThread extends AssistantThreadSummary {
  items: AssistantItem[];
  /** Tokens so far, and an estimate of their cost at list prices. */
  usage: { input: number; output: number; cost_usd: number };
}

export interface AssistantProject {
  id: string;
  name: string;
  description: string;
  instructions: string;
  thread_count: number;
  file_count: number;
  created_at: string;
  updated_at: string;
}

export interface AssistantProjectDetail extends AssistantProject {
  files: AssistantFile[];
  threads: AssistantThreadSummary[];
}

export interface AssistantMessageRequest {
  text: string;
  attachment_ids: string[];
  model: string;
}

/** What arrives, a line at a time, while a reply is being written. */
export type AssistantEvent =
  | { type: 'thinking'; text: string }
  | { type: 'text'; text: string }
  | { type: 'tool_pending'; id: string; name: string }
  | { type: 'tool_start'; id: string; name: string }
  | { type: 'tool_end'; id: string; name: string; result: string; is_error: boolean }
  | { type: 'notice'; text: string }
  /** Discard what has streamed of the current reply; it is being asked for again. */
  | { type: 'reset' }
  | { type: 'error'; message: string }
  | { type: 'done'; thread: AssistantThread };

/** A remote MCP server connected to the chat. Its token is never sent back. */
export interface AssistantConnector {
  id: string;
  /** The name the API knows it by. */
  name: string;
  label: string;
  url: string;
  has_token: boolean;
  /** It was signed in to, as opposed to given a pasted token. */
  signed_in: boolean;
  enabled: boolean;
  /** The plugin it came with, if any. */
  plugin_id: string | null;
  created_at: string;
}

export interface AssistantSkill {
  /** The name Claude loads it under: "plugin:skill". */
  name: string;
  description: string;
  file_count: number;
}

export interface AssistantCommand {
  /** What is typed after "/": "plugin:command". */
  name: string;
  description: string;
  plugin: string;
}

export interface AssistantPlugin {
  id: string;
  name: string;
  version: string;
  description: string;
  enabled: boolean;
  skills: AssistantSkill[];
  commands: AssistantCommand[];
  /** What its archive held that is not used here. */
  notes: string[];
  created_at: string;
}

export interface AssistantExtensions {
  connectors: AssistantConnector[];
  plugins: AssistantPlugin[];
  /** The commands of the plugins that are switched on. */
  commands: AssistantCommand[];
  max_connectors: number;
}

export interface AssistantConnectorTest {
  ok: boolean;
  /** The tools the server offered, when it answered. */
  tools: string[];
  detail: string;
}
