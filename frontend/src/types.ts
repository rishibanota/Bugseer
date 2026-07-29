export type Band = "low" | "medium" | "high" | "critical";
export type Phase = "static" | "git" | "ml" | "graph";

export interface RuleHit {
  rule_id: string;
  title: string;
  score: number;
  detail: string;
  phase: Phase;
  severity: "low" | "medium" | "high";
  locations: { line?: number; end_line?: number; name?: string; note?: string }[];
  evidence: Record<string, unknown>;
}

export interface MlContribution {
  feature: string;
  label: string;
  value: number;
  z_score: number;
  contribution: number;
  direction: "increases" | "decreases";
}

export interface FileRisk {
  path: string;
  language: string;
  score: number;
  raw_score: number;
  band: Band;
  emoji: string;
  static_score: number;
  git_score: number;
  graph_score: number;
  ml_probability: number | null;
  ml_contributions: MlContribution[];
  hits: RuleHit[];
  coverage: number | null;
  dependents: string[];
  dependencies: string[];
  centrality: number;
  metrics?: Record<string, unknown> | null;
  git?: {
    commit_count: number;
    bugfix_commit_count: number;
    revert_count: number;
    author_count: number;
    authors: string[];
    churn: number;
    days_since_last_change: number;
    bugfix_ratio: number;
    fix_follow_rate: number;
    co_change_partners: { path: string; count: number; strength: number }[];
    last_commit_subject: string;
  } | null;
  source?: string;
}

export interface GitSummary {
  available: boolean;
  reason?: string;
  commits_analyzed?: number;
  bugfix_commits?: number;
  bugfix_ratio?: number;
  reverts?: number;
  contributors?: number;
  degraded?: boolean;
  degraded_reason?: string;
}

export interface MlSummary {
  trained: boolean;
  reason: string;
  samples: number;
  positives: number;
  estimator: string;
  auc: number | null;
  precision: number | null;
  recall: number | null;
  baseline_rate: number | null;
  label_window_days: number;
  top_features: { feature: string; label: string; importance: number }[];
}

export interface Summary {
  files_scanned: number;
  total_loc: number;
  bands: Record<Band, number>;
  languages: Record<string, number>;
  average_score: number;
  median_score: number;
  top_rules: { rule_id: string; files: number }[];
  git: GitSummary;
  coverage_source: string | null;
  parsers: Record<string, number>;
  ml: MlSummary | null;
  graph: { nodes: number; import_edges: number; cochange_edges: number };
  coupled_developers: { authors: string[]; shared_files: number }[];
}

export interface ReportSummary {
  root: string;
  generated_at: string;
  summary: Summary;
  hotspots: { path: string; score: number; band: Band; reasons: string[] }[];
  git_available: boolean;
  ml_used: boolean;
  duration_seconds: number;
  version: string;
}

export interface ImpactItem {
  path: string;
  impact_score: number;
  hops: number;
  own_risk: number;
  reasons: string[];
}

export interface ImpactResult {
  seeds: string[];
  affected: ImpactItem[];
  explanation: string[];
}

export interface GraphPayload {
  nodes: { id: string; risk: number; in: number; out: number }[];
  edges: { source: string; target: string; kind: string }[];
  truncated?: boolean;
}
