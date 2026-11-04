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
}

export interface ProvidersResponse {
  llm: ProviderInfo[];
  embedding: ProviderInfo[];
  active_llm: string;
  active_embedding: string;
  active_is_generative: boolean;
}

export interface UserSettings {
  theme: Theme;
  learning_style: string;
  explanation_level: ExplanationLevel;
  assignment_policy: AssignmentPolicy;
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
    vector_count: number;
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
