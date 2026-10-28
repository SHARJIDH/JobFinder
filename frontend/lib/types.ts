// Shared API types (mirror backend/app/api/serializers.py)

export type Tier = "A" | "B" | "C" | "D" | "X" | "";

export interface Salary {
  min: number | null;
  max: number | null;
  currency: string;
  period: string;
  kind: "base" | "total" | "estimated" | "unknown" | string;
  source: string;
  text: string;
  annual_lpa_min: number | null;
  annual_lpa_max: number | null;
}

export interface JobSummary {
  id: number;
  title: string;
  company: string;
  location: string;
  work_mode: string;
  is_remote: boolean;
  provider: string;
  score: number | null;
  deterministic_score: number | null;
  ai_score: number | null;
  tier: Tier;
  hard_excluded: boolean;
  exclusion_reasons: string[];
  flags: string[];
  salary: Salary;
  posted_at: string | null;
  posted_at_evidence: string;
  first_seen_at: string | null;
  last_verified_at: string | null;
  listing_status: "open" | "closed" | "unknown" | "unverified";
  pipeline_status: string;
  pipeline_label: string;
  apply_url: string;
  source_url: string;
  duplicate_of_id: number | null;
  min_years_required: number | null;
}

export interface ScoreComponent {
  key: string;
  label: string;
  value: number;
  weight: number;
  detail: string;
  points: number;
}

export interface Breakdown {
  version?: string;
  deterministic?: number;
  ai_score?: number | null;
  final?: number;
  tier?: Tier;
  components?: ScoreComponent[];
  flags?: string[];
  skills?: {
    matched_required: string[];
    missing_required: string[];
    matched_preferred: string[];
    missing_preferred: string[];
  };
  annual_lpa?: [number | null, number | null];
}

export interface AIAnalysis {
  fit_score: number;
  seniority_assessment: string;
  role_alignment: string;
  strengths: string[];
  gaps: string[];
  missing_mandatory_qualifications: string[];
  eligibility_concerns: string[];
  summary: string;
  model?: string;
  at?: string;
}

export interface DocumentInfo {
  id: number;
  job_id: number;
  kind: "resume" | "cover_letter";
  generation_id: string;
  generator: string;
  model: string;
  prompt_version: string;
  source_resume_id: number | null;
  source_resume_sha256: string;
  profile_hash: string;
  job_content_hash: string;
  created_at: string;
  validation: {
    rejected_bullets?: { source_id: string; text: string; reason: string }[];
    dropped_skills?: string[];
    dropped_cover_paragraphs?: { text: string; reason: string }[];
    summary_fallback?: string;
    warnings?: string[];
    injection_flags?: string[];
    sources?: { id: number; label: string; sha256: string }[];
    latex_notes?: string[];
    latex_unverified?: { numbers?: string[]; skills?: string[] };
  };
  has_pdf: boolean;
  has_docx: boolean;
  has_tex?: boolean;
  tex_origin?: string;
  page_count?: number | null;
  content?: ResumeContent & { paragraphs?: string[]; company?: string; title?: string };
}

export interface ResumeBullet {
  source_id: string;
  text: string;
  rewritten: boolean;
}

export interface ResumeContent {
  contact: Record<string, string>;
  headline?: string;
  summary: string;
  skills: string[];
  experience: { id: string; title: string; company: string; location: string; start: string; end: string; bullets: ResumeBullet[] }[];
  projects: { id: string; name: string; url: string; technologies: string[]; bullets: ResumeBullet[] }[];
  education: { degree: string; institution: string; start: string; end: string; grade: string }[];
  cover_letter_paragraphs?: string[];
  change_log?: string[];
  missing_qualifications?: string[];
  questions_for_candidate?: string[];
}

export interface Answer {
  key: string;
  question: string;
  answer: string;
  source: string;
  needs_input: boolean;
  verified: boolean;
  include: boolean;
}

export interface ApplicationInfo {
  id: number;
  job_id: number;
  resume_document_id: number | null;
  cover_document_id: number | null;
  answers: Answer[];
  checklist: Record<string, boolean>;
  concerns: string[];
  approved_at: string | null;
  submitted_at: string | null;
  submission_method: string;
  submission_evidence: string;
  deadline: string | null;
  notes: string;
  history: { at: string; from: string; to: string; note: string }[];
  updated_at: string;
}

export interface JobDetail extends JobSummary {
  description: string;
  department: string;
  employment_type: string;
  experience_text: string;
  locations: string[];
  board: string;
  source_job_id: string;
  required_skills: string[];
  preferred_skills: string[];
  breakdown: Breakdown;
  ai_analysis: AIAnalysis | null;
  closed_reason: string;
  content_hash: string;
  scored_at: string | null;
  duplicates: JobSummary[];
  documents: DocumentInfo[];
  application: ApplicationInfo | null;
}

export interface RunInfo {
  id: number;
  kind: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  stats: Record<string, unknown>;
}

export interface BudgetStatus {
  calls_today: number;
  call_limit: number;
  est_cost_today_usd: number;
  budget_usd: number;
  provider: string;
}
