// Mirror of backend/contracts.py. backend/tests/test_contracts.py parses this
// file and fails if any literal or field name drifts from the Python side.

export const STATUS_VALUES = [
  "extracting_facts",
  "generating_script",
  "revising_outline",
  "synthesizing_audio",
  "complete",
  "error",
] as const;
export type Status = (typeof STATUS_VALUES)[number];

export const ROLE_VALUES = ["intro", "body", "outro"] as const;
export type Role = (typeof ROLE_VALUES)[number];

export const HOST_VALUES = ["A", "B"] as const;
export type Host = (typeof HOST_VALUES)[number];

export const PRIORITY_VALUES = ["must_cover", "should_cover", "nice_to_have"] as const;
export type Priority = (typeof PRIORITY_VALUES)[number];

export const KIND_VALUES = [
  "thesis",
  "claim",
  "evidence",
  "framework",
  "example",
  "counterargument",
  "implication",
] as const;
export type Kind = (typeof KIND_VALUES)[number];

export const STATUS_PROGRESS: Record<Status, number> = {
  extracting_facts: 5,
  generating_script: 20,
  revising_outline: 75,
  synthesizing_audio: 90,
  complete: 100,
  error: 100,
};

export const IN_PROGRESS_STATUSES = [
  "extracting_facts",
  "generating_script",
  "revising_outline",
  "synthesizing_audio",
] as const;

export function isInProgress(status: Status | null | undefined): boolean {
  return !!status && (IN_PROGRESS_STATUSES as readonly string[]).includes(status);
}

export function isStatus(value: unknown): value is Status {
  return typeof value === "string" && (STATUS_VALUES as readonly string[]).includes(value);
}

export interface KeyPoint {
  id: string;
  text: string;
  kind: Kind;
  priority: Priority;
  source_ref: string;
}

export interface Section {
  id: string;
  order: number;
  role: Role;
  title: string;
  pedagogical_goal: string;
  key_points: KeyPoint[];
  word_budget: number;
}

export interface ProductionPlan {
  title: string;
  source_summary: string;
  target_minutes: number;
  total_word_budget: number;
  capacity_warning: string | null;
  sections: Section[];
}

export interface DialogueTurn {
  section_id: string;
  host: Host;
  text: string;
  delivery_instruction: string;
  covers: string[];
}

export interface CoverageReport {
  must_cover_total: number;
  must_cover_hit: number;
  missing_ids: string[];
  missing_points: Record<string, string>;
  per_section_words: Record<string, number>;
  total_words: number;
  estimated_minutes: number;
  warnings: string[];
}

export interface CompleteData {
  audio_url: string;
  transcript_url: string;
  coverage: CoverageReport;
  tts_provider: string;
}

export interface ErrorData {
  detail: string;
}

export interface StreamEvent {
  status: Status;
  message: string;
  progress: number;
  data: Record<string, unknown> | null;
}

export const STREAM_EVENT_FIELDS = ["status", "message", "progress", "data"] as const;
