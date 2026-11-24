export type User = { id: string, role: 'student' | 'teacher' | 'admin', display_name: string, csrf: string }
export type Page<T> = { items: T[], total: number, page: number, page_size: number }
export type Job = {
  id: string, status: string, kind: string, progress_total: number, progress_done: number,
  succeeded_count: number, failed_count: number, skipped_count: number, error: { message: string } | null,
  result: { source_id?: string, export_id?: string, batch_id?: string } | null,
  analysis?: { model: string, provider_call_limit: number, provider_calls_started: number },
  runs?: { id: string, session_id: string, status: string, error: string | null, provider?: string, degraded?: number,
    stage?: string | null, report_id?: string | null, task_title?: string, process_score?: number | null,
    process_score_five?: number | null,
    scored_dimensions?: number | null, actual_model_calls?: number | null, provider_response_valid?: number | null,
    segment_count?: number | null }[],
}
export type EventSummary = {
  id: string, sequence_no: number, type: string, occurred_at: string | null, timestamp_precision: string, excerpt: string,
}
export type EventDetail = EventSummary & { payload: Record<string, unknown> }
export type Dimension = {
  dimension: string, state: 'scored' | 'not_applicable' | 'insufficient_evidence',
  level: number | null, reason: string | null, rationale: string, event_ids: string[],
}
export type Annotation = {
  id: string, dimension: string, type: string, text: string, source: string,
  evidence_refs: { event_id: string, excerpt: string }[],
}
export type EvidenceNote = { text: string, event_ids: string[] }
export type DimensionNote = EvidenceNote & { dimension: string }
export type OutcomeFields = {
  source_outcome_score: number | null, source_outcome_percent: number | null, system_outcome_score: number | null,
  outcome_basis: 'source_grade' | 'system_rubric' | 'local_review' | null,
}
export type TeacherRecommendation = { dimension: string, observation: string, judgment: string, action: string, follow_up: string, event_ids: string[] }
export type StudyChatQuality = {
  eligibility_status: string, observed_turns: number, declared_turns: number | null, completeness_ratio: number | null,
  task_relevance: string, relevance_reason: string, relevance_evidence_event_ids: string[],
  richness_signals: { signal: string, event_ids: string[] }[], grade_matched: boolean, grade_link_valid: boolean,
  submission_link_valid: boolean, exclusion_reasons: string[], excluded_event_ids: string[],
  scoped_turns: number,
  transcripts: { conversation: number, observed_turns: number, declared_turns: number | null, incomplete: boolean, in_scope: boolean }[],
}
export type StudentRecommendation = { dimension: string, action: string, example: string, event_ids: string[] }
export type TransferTask = { dimension: string, title: string, prompt: string, success_criteria: string, event_ids: string[] }
export type DialogueMessage = {
  id: string, sequence_no: number, turn: number, conversation: number | null, source_turn: number | null,
  role: 'student' | 'ai', text: string,
}
type Outcome = { score: number | null, basis?: 'local_review', rubric_version?: string, items: {
  item_id: string, title: string, max_score: number, score: number | null, reason: string, weight?: number, artifact_ids?: string[],
}[] }
export type Report = OutcomeFields & {
  id: string, run_id: string, session_id: string, version: number, kind: string, source_type: string,
  method: string, purpose: string, status: string, degraded: boolean, demo_only: boolean,
  dimensions: Dimension[], process_score: number | null, process_score_five?: number | null, diagnosis: { label: string | null },
  outcome: Outcome, local_outcome?: Outcome,
  teaching_suggestions: string[], annotations: Annotation[], warnings: string[], conflicts: Conflict[],
  legacy_rubric: boolean, process_coverage?: { scored_count: number, total: number, minimum: number },
  teaching_diagnosis?: EvidenceNote | null, teaching_recommendations?: DimensionNote[], limitations?: string[],
  teacher_recommendations?: TeacherRecommendation[], student_recommendations?: StudentRecommendation[], transfer_tasks?: TransferTask[],
  guidance_basis?: string,
  evidence_turns?: Record<string, { turn: number, sequence: number }>,
  display_outcome_score: number | null, outcome_display_projection?: boolean,
  ai_behaviors?: (DimensionNote & { behavior: string })[],
  out_of_task_event_ids?: string[],
  system_quality_review?: { reason: string, system_parent_id: string, changes: { event_ids: string[], target: string, reason: string }[] },
  task_version: string, rubric_version: string, threshold_version: string, prompt_version: string,
  ruleset_version: string, provider: string, model: string, effective: boolean, released_to_student: boolean, can_review: boolean,
  provider_call?: { actual_model_calls?: number, provider_response_valid?: boolean, segments?: number, stages?: { id: string }[] } | null,
  review?: { decision: string, reason: string, reviewer: string, created_at: string },
  parent_id: string | null, parent_run_id?: string | null,
}
export type Conflict = { state: string, dimension: string, rule_event_ids: string[], candidate: { text: string, event_ids: string[] } }
export type Session = {
  id: string, task_id: string, task_title: string, source_type: string, participant: string | null,
  status: string, coverage: string | null, fixture_id: string | null, created_at: string,
}
export type SessionDetail = Session & { reports: (OutcomeFields & {
  id: string, run_id: string, method: string, status: string, version: number, process_score: number | null,
  process_score_five: number | null,
  outcome_score: number | null, diagnosis: { label: string | null }, conflicts: Conflict[],
  provider: string, model: string, rubric_version: string, legacy_rubric: boolean, kind: string, effective: boolean,
})[], jobs: Job[], task_instructions: string, external_scores: Record<string, number>, studychat?: (OutcomeFields & {
  term: string, assignment: string, assignment_title: string, turn_count: number, message_count: number,
  grade_matched: boolean, submission_count: number, quality: StudyChatQuality,
}) | null }

let csrf = ''
export function setUser(user: User | null) { csrf = user?.csrf ?? '' }

export class ApiError extends Error {
  constructor(message: string, public status: number, public code: string, public details: Record<string, unknown> = {}) {
    super(message)
  }
}

export async function api<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const form = body instanceof FormData
  const headers = new Headers()
  if (body !== undefined && !form) headers.set('Content-Type', 'application/json')
  if (method !== 'GET' && csrf) headers.set('X-CSRF-Token', csrf)
  const res = await fetch('/api' + path, {
    method, headers, credentials: 'same-origin',
    body: body === undefined ? undefined : form ? body : JSON.stringify(body),
  })
  const data = await res.json().catch(() => null)
  if (!res.ok) throw new ApiError(data?.message || '服务暂不可用。请检查启动状态后重试。', res.status, data?.code || 'UNAVAILABLE', data?.details)
  return data as T
}

export const terminal = (status: string) => ['completed', 'partial', 'failed', 'cancelled'].includes(status)
export const message = (error: unknown) => error instanceof Error ? error.message : '操作未完成，请重试。'
