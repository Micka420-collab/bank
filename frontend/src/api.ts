// Client HTTP : cookies same-origin, jeton CSRF en en-tête, erreurs typées, jamais de cache.

import { useCallback, useEffect, useRef, useState } from "react";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

export function readCookie(name: string, source = document.cookie): string {
  for (const part of source.split(";")) {
    const [k, ...v] = part.trim().split("=");
    if (k === name) return decodeURIComponent(v.join("="));
  }
  return "";
}

export function errorMessage(detail: unknown, fallback: string): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d: { loc?: unknown[]; msg?: string }) => {
        const field = Array.isArray(d.loc) ? d.loc[d.loc.length - 1] : "";
        return field ? `${String(field)} : ${d.msg ?? ""}` : (d.msg ?? "");
      })
      .join(" ; ");
  }
  return fallback;
}

// Réponse sans détail JSON (nginx, passerelle coupée) : dire quoi faire plutôt qu'afficher « Erreur 429 ».
export function statusMessage(status: number): string {
  if (status === 429) return "Trop de demandes en peu de temps : patiente une minute, puis réessaie.";
  if (status === 413) return "Fichier trop lourd pour être envoyé.";
  if (status === 502 || status === 503 || status === 504) return "Le serveur Bank ne répond pas pour l'instant : réessaie dans un instant.";
  return `Erreur ${status}`;
}

type Options = { method?: string; body?: unknown; form?: FormData; signal?: AbortSignal };

export async function api<T>(path: string, { method = "GET", body, form, signal }: Options = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (method !== "GET") headers["X-CSRF-Token"] = readCookie("bank_csrf");
  let payload: BodyInit | undefined;
  if (form) payload = form;
  else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  const res = await fetch(path, { method, headers, body: payload, credentials: "same-origin", cache: "no-store", signal });
  if (res.status === 401 && !path.startsWith("/api/auth/")) {
    window.dispatchEvent(new Event("bank:unauthorized"));
  }
  if (!res.ok) {
    let detail: unknown = null;
    try {
      detail = (await res.json()).detail;
    } catch {
      /* corps non JSON */
    }
    throw new ApiError(res.status, errorMessage(detail, statusMessage(res.status)));
  }
  // Une écriture a réussi : ce qui en dépend ailleurs (pastilles de navigation) peut se relire.
  if (method !== "GET") window.dispatchEvent(new Event("bank:changed"));
  if (res.status === 204) return undefined as T;
  if ((res.headers.get("content-type") ?? "").includes("application/json")) return (await res.json()) as T;
  return (await res.blob()) as T;
}

export function useApi<T>(path: string | null): {
  data: T | null;
  error: string | null;
  loading: boolean;
  reload: () => void;
} {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(path !== null);
  const [tick, setTick] = useState(0);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    if (path === null) return;
    const ctrl = new AbortController();
    setLoading(true);
    api<T>(path, { signal: ctrl.signal })
      .then((d) => {
        setData(d);
        setError(null);
      })
      .catch((e: unknown) => {
        if ((e as Error).name !== "AbortError") setError((e as Error).message);
      })
      .finally(() => {
        if (!ctrl.signal.aborted) setLoading(false);
      });
    return () => {
      mounted.current = false;
      ctrl.abort();
    };
  }, [path, tick]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data, error, loading, reload };
}

// ------------------------------------------------------------------ types (miroir du backend)
export type Point = { date: string; balance_cents: number };

export type UpcomingEvent = {
  date: string;
  label: string;
  amount_cents: number;
  category: string;
  kind: "income" | "fixed";
  confidence: number;
  late: boolean;
};

export type BudgetLine = {
  slug: string;
  name: string;
  budget_cents: number;
  spent_cents: number;
  remaining_cents: number;
  pace: number;
  status: "ok" | "warning" | "over";
};

export type MonthForecast = {
  month: string;
  today: string;
  days_in_month: number;
  days_left: number;
  current_balance_cents: number;
  income_received_cents: number;
  spent_fixed_cents: number;
  spent_variable_cents: number;
  saved_cents: number;
  expected_income_cents: number;
  expected_fixed_cents: number;
  expected_variable_cents: number;
  projected_end_balance_cents: number;
  safe_to_spend_cents: number;
  safe_to_spend_per_day_cents: number;
  daily_variable_rate_cents: number;
  lowest_balance_cents: number;
  lowest_balance_date: string;
  overdraft_date: string | null;
  status: "ok" | "tight" | "risk";
  upcoming: UpcomingEvent[];
  projection: Point[];
  history: Point[];
  budgets: BudgetLine[];
  allowance: { start: string; end: string; per_day_cents: number; reason: string | null }[];
};

export type BudgetResponse = {
  forecast: MonthForecast;
  suggested_budgets: Record<string, number>;
  settings: { buffer_cents: number; savings_goal_cents: number };
};

export type Category = {
  slug: string;
  name: string;
  kind: string;
  color: string;
  monthly_budget_cents: number | null;
};

export type AccountKind =
  | "checking" | "savings" | "livret_a" | "ldds" | "lep" | "cel" | "pel" | "pea"
  | "assurance_vie" | "per" | "crypto" | "credit" | "other";

export type Account = {
  id: number;
  name: string;
  kind: AccountKind;
  institution: string | null;
  currency: string;
  iban_masked: string | null;
  provider: "manual" | "file" | "enablebanking";
  balance_cents: number;
  balance_at: string | null;
  include_in_budget: boolean;
  archived: boolean;
  connection_status: string | null;
};

export type Transaction = {
  id: number;
  account_id: number;
  booking_date: string;
  amount_cents: number;
  currency: string;
  label: string;
  merchant: string;
  category: string | null;
  category_source: string;
  is_transfer: boolean;
  aid_kind: string | null;
  note: string | null;
};

export type ImportResult = {
  dry_run: boolean;
  format: string;
  detected: Record<string, string>;
  parsed: number;
  skipped: { line: number; reason: string }[];
  preview: { booking_date: string; amount_cents: number; label: string }[];
  inserted: number;
  duplicates: number;
  reconciled: number;
  transfers_detected: number;
  aids_detected: number;
  already_imported: boolean;
};

export type AidStream = {
  kind: string;
  name: string;
  organism: string;
  organism_name: string;
  portal: string;
  monthly_totals: Record<string, number>;
  average_3m_cents: number;
  last_date: string;
  last_amount_cents: number;
  next_expected: string | null;
  status: "ok" | "late" | "changed" | "ended";
  message: string | null;
};

export type AidGap = {
  kind: string;
  name: string;
  estimated_cents: number;
  received_cents: number;
  gap_cents: number;
  message: string;
};

export type Simulation = {
  period: string;
  engine_version: string;
  estimates: { variable: string; label: string; monthly_cents: number }[];
  css: "gratuite" | "participation" | "non_eligible";
  css_participation_cents: number;
  lep_eligible: boolean;
  created_at?: string;
  inputs?: { months_of_salary_history: number; used_declared_taxable_income: boolean };
};

export type AidsResponse = {
  summary: { streams: AidStream[]; total_last_12m_cents: number; monthly_average_cents: number; alerts: string[] };
  simulation: Simulation | null;
  gaps: AidGap[];
  disclaimer: string;
};

// ------------------------------------------------------------------ déclarations récurrentes
export type ObligationState = "upcoming" | "open" | "soon" | "late" | "done" | "missed";
export type ObligationPeriod = {
  key: string;
  label: string;
  opens: string;
  deadline: string;
  reference_months: string[];
  state: ObligationState;
  done_on: string | null;
  how: string | null;
  proof: { day: string; amount_cents: number; kind: string } | null;
  awaiting_payment: boolean;
  estimated: boolean;
  note: string | null;
  declared: Record<string, number> | null;
};
export type Obligation = {
  id: number;
  kind: string;
  label: string;
  organism: string;
  organism_name: string;
  active: boolean;
  anchor: string | null;
  anchor_kind: "month" | "date" | null;
  anchor_label: string | null;
  needs_anchor: boolean;
  cadence_months: number;
  window_days: number;
  what: string;
  consequence: string;
  url: string;
  source: string;
  automatic: boolean;
  current: ObligationPeriod | null;
  periods: ObligationPeriod[];
};
export type ObligationSpec = {
  kind: string;
  label: string;
  organism: string;
  organism_name: string;
  cadence_months: number;
  what: string;
  consequence: string;
  url: string;
  source: string;
  anchor: "month" | "date" | null;
  anchor_label: string | null;
};
export type ObligationsView = {
  obligations: Obligation[];
  suggestions: ObligationSpec[];
  catalog: ObligationSpec[];
  checked_on: string;
  disclaimer: string;
};
export type PrefillCell = {
  amount_cents: number;
  items: { id: number; day: string; label: string; amount_cents: number }[];
  source: "releve" | "bulletin";
  net_paid_cents?: number;
  documents?: number[];
};
export type Prefill = {
  period: string;
  months: string[];
  month_labels: string[];
  kinds: { key: string; label: string }[];
  cells: Record<string, Record<string, PrefillCell>>;
  totals: Record<string, number>;
  excluded: { id: number; day: string; label: string; amount_cents: number; why: string }[];
  to_check: { id: number; day: string; label: string; amount_cents: number; why: string }[];
  notes: string[];
  summary: string;
};

// ------------------------------------------------------------------ l'année qui vient
export type YearSpike = {
  key: string;
  label: string;
  amount_cents: number;
  day: string;
  cadence: "quarterly" | "yearly" | "custom" | "planned";
  source: "detected" | "custom" | "planned";
  category: string | null;
  occurrences: number;
  confidence: number;
};
export type YearPoint = {
  month: string;
  label: string;
  income_cents: number;
  fixed_cents: number;
  variable_cents: number;
  spikes_cents: number;
  spikes: string[];
  end_balance_cents: number;
};
export type YearAhead = {
  start_month: string;
  start_balance_cents: number;
  points: YearPoint[];
  spikes: YearSpike[];
  ignored: YearSpike[];
  income_monthly_cents: number;
  fixed_monthly_cents: number;
  variable_monthly_cents: number;
  yearly_spikes_cents: number;
  provision_monthly_cents: number;
  lowest_month: string;
  lowest_label: string;
  lowest_cents: number;
  status: "ok" | "tight" | "risk";
  notes: string[];
  savings_goal_cents: number;
  buffer_cents: number;
  custom: { key: string; label: string; amount_cents: number; next_date: string; every_months: number }[];
  disclaimer: string;
};

// ------------------------------------------------------------------ corrélations
export type Correlation = {
  key: string;
  kind: string;
  level: "danger" | "warning" | "info";
  topic: string;
  topic_label: string;
  title: string;
  body: string;
  evidence: { label: string; value: string; ref: string | null }[];
  yearly_cents: number | null;
  action: { template?: string; label?: string; merchant?: string } | null;
  confidence: number;
  statistical: boolean;
  status: "new" | "dismissed" | string;
  procedure_id: number | null;
};
export type CorrelationsView = { findings: Correlation[]; hidden: number; irl_source: string; disclaimer: string };

export type Profile = {
  birth_date: string;
  postal_insee_code: string;
  housing: string;
  rent_cents: number;
  activity: string;
  couple: boolean;
  partner_birth_date: string | null;
  partner_monthly_net_cents: number;
  children_birth_dates: string[];
  annual_taxable_income_n2_cents: number | null;
};

export type WealthResponse = {
  net_worth: { assets_cents: number; liabilities_cents: number; net_cents: number; by_bucket: Record<string, number> };
  accounts: { id: number; name: string; kind: string; balance_cents: number }[];
  history: { day: string; net_cents: number }[];
  emergency_fund: {
    available_cents: number;
    monthly_essentials_cents: number;
    months_covered: number;
    reference_min_months: number;
    reference_max_months: number;
    level: string;
  };
  regulated: {
    kind: string;
    name: string;
    rate_percent: string;
    ceiling_cents: number;
    balance_cents: number;
    room_cents: number;
    yearly_interest_cents: number;
    at_ceiling: boolean;
  }[];
  rates_as_of: string | null;
  rates_source: string | null;
  goals: {
    id: number;
    name: string;
    target_cents: number;
    current_cents: number;
    target_date: string | null;
    monthly_contribution_cents: number;
    progress: number;
    months_to_goal: number | null;
    monthly_needed_cents: number | null;
  }[];
  disclaimer: string;
};

export type ProjectionYear = {
  year: number;
  nominal_cents: number;
  real_cents: number;
  contributed_cents: number;
  interest_cents: number;
};

export type Me = { username: string; totp_enabled: boolean; recovery_codes_left: number; session_expires_at: string; passkeys?: number };

export type Connection = {
  id: number;
  aspsp_name: string;
  aspsp_country: string;
  status: string;
  valid_until: string | null;
  last_sync_at: string | null;
  last_error: string | null;
  accounts: string[];
};

// ------------------------------------------------------------------ agent IA (HERMÈS) et « Ma situation »
export type QuestionKind = "choice" | "multi" | "money" | "int" | "text" | "list";

export type ItemField = {
  name: string;
  label: string;
  kind: "money" | "choice" | "decimal" | "date" | "int" | "text";
  options: string[];
  required: boolean;
};

export type Question = {
  id: string;
  section: string;
  question: string;
  why: string;
  kind: QuestionKind;
  weight: number;
  options: { value: string; label: string }[];
  max_items: number;
  minimum: number | null;
  maximum: number | null;
  item_fields: ItemField[] | null;
};

export type StoredAnswer = { value: unknown; source: string; at: string };

export type Suggestion = { question_id: string; value: unknown; evidence: string };

export type RiskProfile = {
  label: "prudent" | "equilibre" | "dynamique" | "inconnu";
  score: number | null;
  capacity_limited: boolean;
  reason: string;
};

export type OnboardingResponse = {
  percent: number;
  answered: number;
  total: number;
  sections: { id: string; title: string }[];
  questions: Question[];
  answers: Record<string, StoredAnswer>;
  suggestions: Suggestion[];
  missing: string[];
  risk_profile: RiskProfile;
};

export type Proposal = {
  id: number;
  agent: string;
  kind: string;
  title: string;
  params: Record<string, unknown>;
  rationale: string;
  status: "pending" | "applied" | "rejected" | "failed" | "expired";
  result: string | null;
  question?: string; // set_answer : intitulé de la question visée
  impact?: number; // create_rule : opérations qui seraient reclassées
  created_at: string;
  expires_at: string;
  decided_at: string | null;
};

export type PrivacyMode = "redacted" | "aggregates" | "full";

export type AgentToken = {
  id: number;
  name: string;
  prefix: string;
  scopes: string[];
  privacy: PrivacyMode;
  allowed_ips: string[];
  created_at: string;
  expires_at: string;
  last_used_at: string | null;
  calls: number;
  state: "actif" | "expire" | "revoque";
};

export type AgentTokenCreated = AgentToken & {
  token: string;
  mcp_url: string;
  hermes_config: string;
  hermes_env: string;
};

export type AgentCatalog = {
  scopes: Record<string, string>;
  presets: Record<string, string[]>;
  privacy: Record<PrivacyMode, string>;
  max_ttl_days: number;
};

export type AgentActivity = { at: string; agent: string; action: string; details: Record<string, unknown> };

export type Alert = { severity: "critique" | "attention" | "opportunite" | "info"; code: string; title: string; suggested_action: string };

// ------------------------------------------------------------------ décisions : achat et placements
export type PurchaseSnapshot = {
  per_day_cents: number;
  lowest_cents: number;
  lowest_date: string;
  overdraft_date: string | null;
  end_balance_cents: number;
};

export type PurchaseVerdict = {
  verdict: "ok" | "serre" | "attendre" | "deconseille";
  amount_cents: number;
  on: string;
  before: PurchaseSnapshot;
  after: PurchaseSnapshot;
  best_date: string | null;
  envelope: { slug: string; budget_cents: number; spent_cents: number; spent_after_cents: number; over_after: boolean } | null;
  savings_months_equivalent: number | null;
  explanation: string;
};

export type SavingPath = { label: string; months: number | null; affordable_on: string | null; monthly_cents: number; note: string };

export type PurchasePlan = {
  answer: "oui" | "oui_mais" | "attendre" | "plus_tard" | "hors_de_portee";
  headline: string;
  amount_cents: number;
  this_month: PurchaseVerdict;
  from_savings_cents: number;
  paths: SavingPath[];
  explanation: string;
};

export type PlanStep = {
  order: number;
  kind: "urgence" | "matelas" | "dette" | "projet" | "long_terme" | "attente";
  title: string;
  monthly_cents: number;
  where: string;
  why: string;
  months: number | null;
  target_cents: number | null;
};

export type Allocation = { vehicle: string; label: string; share_percent: number; monthly_cents: number; note: string };

export type InvestmentPlan = {
  monthly_capacity_cents: number;
  allocated_cents: number;
  steps: PlanStep[];
  long_term: Allocation[];
  long_term_monthly_cents: number;
  equity_share_percent: number;
  horizon_years: number | null;
  tax: { marginal_rate_percent: number; source: "declared" | "document" | "estimated" | "unknown"; taxable_income_cents: number | null; parts: string | null; detail: string };
  excluded: string[];
  cautions: string[];
  summary: string;
  references: string;
};

// ------------------------------------------------------------------ énergie (Linky)
export type EnergyMonth = { month: string; wh: number; days: number; cost_cents: number };

export type EnergyInsights = {
  data_from: string | null;
  data_through: string | null;
  days_of_data: number;
  stale: boolean;
  last_30_days_wh: number;
  last_30_days_cost_cents: number;
  this_month_wh: number;
  this_month_cost_cents: number;
  months: EnergyMonth[];
  same_month_last_year_wh: number | null;
  change_vs_last_year_percent: number | null;
  yearly_estimate_cents: number;
  yearly_estimate_wh: number;
  yearly_is_extrapolated: boolean;
  recommended_monthly_cents: number;
  monthly_payment_cents: number | null;
  regularisation_cents: number | null;
  spike_ratio: number | null;
  spike: boolean;
  daily_average_wh: number;
};

export type TariffOption = { id: string; label: string; cents_per_100kwh: number; yearly_subscription_cents: number; as_of: string; source: string };

export type EnergyStatus = {
  connected: boolean;
  tariff: { cents_per_100kwh: number; yearly_subscription_cents: number; label: string; options: TariffOption[] };
  connection: { id: number; label: string; prm_masked: string; status: string; last_sync_at: string | null; last_error: string | null; created_at: string } | null;
  insights: EnergyInsights | null;
  daily?: { day: string; wh: number }[];
  imported?: number;
};

// ------------------------------------------------------------------ documents officiels
export type Portal = { id: string; name: string; what: string; url: string; how: string };

export type ParsedDocument = {
  kind: "avis_impot" | "caf" | "france_travail" | "inconnu";
  title: string;
  fields: Record<string, unknown>;
  warnings: string[];
  discarded: string[];
};

export type OfficialFact = { id: number; kind: string; period: string; title: string; fields: Record<string, unknown>; imported_at: string };

// ------------------------------------------------------------------ initiatives (« Bank te demande »)
export type CardAction = {
  id: string;
  label: string;
  style: "primary" | "secondary" | "link";
  effect: { type: string; [k: string]: unknown };
};
export type CardInput =
  | { kind: "money"; placeholder?: string }
  | { kind: "int"; minimum: number | null; maximum: number | null }
  | { kind: "choice"; options: { value: string; label: string }[] }
  | { kind: "multi"; options: { value: string; label: string }[]; max_items: number }
  | { kind: "category"; options: { value: string; label: string }[]; default?: string | null };
export type Card = {
  key: string;
  kind: "question" | "notice" | "celebrate" | "tip";
  priority: number;
  title: string;
  body: string;
  why: string | null;
  evidence: string | null;
  topic: string;
  input: CardInput | null;
  actions: CardAction[];
};
export type Initiatives = { cards: Card[]; total: number; by_topic: Record<string, number> };
export type ActResult = Initiatives & { result: string };

// ------------------------------------------------------------------ planning de travail
export type PayRule = { pattern: string; unit: "per_event" | "per_day" | "per_hour"; amount_cents: number; learned?: boolean };
export type PayModel = { base_cents: number; base_learned: boolean; offset_months: 0 | 1; rules: PayRule[] };
export type PayExtra = { pattern: string; unit: string; amount_cents: number; count: number; hours: number; total_cents: number; learned: boolean };
export type PayPrediction = {
  pay_date: string;
  period_start: string;
  period_end: string;
  amount_cents: number;
  base_cents: number;
  extras: PayExtra[];
  worked_days: number;
  worked_hours: number;
  shifts_in_period: number;
};
export type PlanningStatus = {
  connected: boolean;
  employer: string | null;
  pay_day: number | null;
  usual_pay_cents: number | null;
  next_pay_date: string | null;
  plan: {
    source: "url" | "file";
    label: string;
    status: string;
    last_sync_at: string | null;
    last_error: string | null;
    shifts: number;
    coverage_start: string | null;
    coverage_end: string | null;
  } | null;
  model: PayModel | null;
  learned: (PayModel & { periods: number; residual_cents: number; note: string }) | null;
  predictions: PayPrediction[];
  upcoming: { day: string; title: string; hours: number; all_day: boolean }[];
  imported?: number;
};

// ------------------------------------------------------------------ historique (tableau de bord)
export type HistoryRange = "7d" | "30d" | "3m" | "1y" | "all";
export type HistoryTotals = { income_cents: number; spent_cents: number; net_cents: number };
export type History = {
  range: HistoryRange;
  start: string;
  end: string;
  current_balance_cents: number;
  balances: { date: string; balance_cents: number }[];
  now: HistoryTotals;
  previous: HistoryTotals;
  categories: { slug: string; name: string; color: string; cents: number; share: number }[];
  periods: { key: string; start: string; label: string; income_cents: number; spent_cents: number }[];
  anomalies: { slug: string; name: string; cents: number; previous_cents: number; ratio: number }[];
  latest: { id: number; date: string; merchant: string; label: string; amount_cents: number; category: string | null; category_name: string | null; is_transfer: boolean }[];
};

// ------------------------------------------------------------------ assistant (OpenRouter)
export type AssistantPrivacy = "full" | "redacted" | "aggregates";
export type MemoryStatus = { consent: boolean; asked: boolean; available: boolean };
export type AssistantStatus = {
  configured: boolean;
  privacy_modes: Record<AssistantPrivacy, string>;
  name: string;
  memory: MemoryStatus;
  provider?: string;
  key_prefix?: string;
  model?: string;
  privacy?: AssistantPrivacy;
  monthly_cap_usd_cents?: number;
  daily_calls_cap?: number;
  month_cost_usd_cents?: number;
  month_cost_microusd?: number;
  calls_today?: number;
  calls_month?: number;
  last_used_at?: string | null;
  last_error?: string | null;
  created_at?: string;
};
export type AssistantModel = {
  id: string;
  name: string;
  context_length: number;
  prompt_usd_per_million: number;
  completion_usd_per_million: number;
  free: boolean;
};
export type MemoryStore = "user" | "notes";
export type MemoryNotice = { action: "add" | "patch" | "remove"; store: MemoryStore; id: number; content: string; before?: string };
export type AssistantAnswer = {
  answer: string;
  conversation_id: number;
  message_id: number;
  model: string;
  name: string;
  privacy: AssistantPrivacy;
  memory_on: boolean;
  memory: MemoryNotice[];
  tools: string[];
  cost_usd: number;
  prompt_tokens: number;
  completion_tokens: number;
  truncated: boolean;
  review_due: boolean;
  calls_today: number;
  month_cost_usd_cents: number;
};
export type MessageMeta = {
  model?: string;
  cost_usd?: number;
  privacy?: AssistantPrivacy;
  truncated?: boolean;
  tools?: string[];
  memory?: { action: string; store: MemoryStore; id: number }[];
};
export type ConversationSummary = {
  id: number;
  title: string;
  summary: string | null;
  started_at: string;
  last_at: string;
  turns: number;
  memory_on: boolean;
  closed: boolean;
};
export type ConversationDetail = {
  id: number;
  title: string;
  summary: string | null;
  memory_on: boolean;
  closed: boolean;
  messages: { id: number; role: "user" | "assistant"; content: string; ts: string; meta: MessageMeta; feedback: "up" | "down" | null }[];
};
export type MemoryEntryView = {
  id: number;
  content: string;
  pinned: boolean;
  origin: "chat" | "review" | "user";
  blocked: boolean;
  created_at: string;
  updated_at: string;
};
export type MemoryProposalView = {
  id: number;
  store: MemoryStore;
  action: "add" | "patch" | "remove";
  old_text: string | null;
  new_text: string | null;
  reason: string | null;
  created_at: string;
};
export type MemoryView = {
  stores: Record<MemoryStore, { limit: number; used: number; usage: string; entries: MemoryEntryView[] }>;
  proposals: MemoryProposalView[];
  name: string;
  retention_days: number;
  retention_choices: number[];
  conversations: number;
  consent: boolean;
  available: boolean;
  titles: Record<MemoryStore, string>;
  forgotten?: { entries: number; conversations: number };
};
export type ConsentView = {
  purpose: string;
  title: string;
  text: string;
  granted: boolean;
  explicit: boolean;
  decided: boolean;
  updated_at: string | null;
  default: boolean;
};
export type AssistantHints = { suggested: number; merchants: number; hints: Record<string, string> };

// ------------------------------------------------------------------ coffre-fort de documents
export type VaultStatus = "processing" | "to_review" | "verified" | "failed" | "refused";
export type VaultFieldType = "amount" | "date" | "month" | "percent" | "number" | "text";
export type VaultKind = { key: string; label: string; sensitive: boolean };
export type VaultSummary = {
  id: number;
  has_thumb: boolean;
  kind: string;
  kind_label: string;
  title: string;
  status: VaultStatus;
  error: string | null;
  sensitive: boolean;
  mime: string;
  size: number;
  pages: number;
  source: "upload" | "camera";
  issuer: string | null;
  document_date: string | null;
  expires_on: string | null;
  retention_until: string | null;
  created_at: string;
  verified_at: string | null;
  fields_count: number;
  missing_count: number;
  warnings_count: number;
};
export type VaultField = {
  key: string;
  label: string;
  type: VaultFieldType;
  value: number | string;
  original: number | string | null;
  evidence: string | null;
  confidence: number;
  source: "extraction" | "assist" | "user";
};
export type VaultDetail = VaultSummary & {
  filename: string;
  method: "pdf_text" | "ocr" | "none";
  confidence: number;
  fields: VaultField[];
  missing: { key: string; label: string; type: VaultFieldType }[];
  warnings: string[];
  retention: { label: string; title: string; url: string; verified_on: string };
  official: boolean;
  kinds: VaultKind[];
  text_available: boolean;
  assist?: { added: string[]; rejected: number; cost_usd: number };
};
export type VaultListing = {
  documents: VaultSummary[];
  counts: Record<string, number>;
  kinds: VaultKind[];
  processing: boolean;
  ocr_available: boolean;
  total_bytes: number;
};
export type VaultUpload = { documents: VaultSummary[]; skipped: { filename: string; reason: string; document_id?: number }[] };

// ------------------------------------------------------------------ opportunités
export type OpportunityCategory = "recuperer" | "reduire" | "revenus" | "epargne" | "anticiper";
export type Period = "once" | "month" | "year";
export type SourceRef = { title: string; url: string | null; verified_on: string | null; kind?: string };
export type OpportunityAction = { template?: string; initial?: Record<string, unknown>; link?: string; label?: string };
export type Opportunity = {
  key: string;
  category: OpportunityCategory;
  category_label: string;
  title: string;
  summary: string;
  amount_cents: number;
  period: Period;
  yearly_cents: number;
  probability: number;
  effort_minutes: number;
  cost_cents: number;
  expected_net_cents: number;
  confidence: "élevée" | "moyenne" | "faible";
  facts: string[];
  estimates: string[];
  hypotheses: string[];
  sources: SourceRef[];
  action: OpportunityAction;
  deadline: string | null;
  status: "new" | "started" | "snoozed" | "dismissed" | "done";
  procedure: { id: number; stage: string; stage_label: string } | null;
};
export type ValueEvent = {
  key: string;
  amount_cents: number;
  period: Period;
  on: string;
  label: string;
  evidence: string | null;
  source: "user" | "auto";
};
export type ValueSummary = {
  confirmed: Record<Period, number>;
  confirmed_yearly_cents: number;
  estimated: Record<Period, number>;
  estimated_yearly_cents: number;
  history: ValueEvent[];
  pending: ValueEvent[];
};
export type Confirmation = { key: string; procedure_id: number; title: string; label: string; amount_cents: number; period: Period };
export type OpportunityCenter = {
  opportunities: Opportunity[];
  hidden: number;
  categories: Record<OpportunityCategory, string>;
  priorities: OpportunityCategory[];
  time_value_cents_per_hour: number;
  value: ValueSummary;
  confirmations: Confirmation[];
  disclaimer: string;
};

// ------------------------------------------------------------------ démarches
export type ProcedureStage = "verify" | "documents" | "ready" | "sent" | "waiting" | "done" | "abandoned";
export type ProcedureFieldType = VaultFieldType | "choice" | "bool" | "longtext";
export type ProcedureTemplate = {
  key: string;
  title: string;
  summary: string;
  category: string;
  organisme: string;
  channel: "online_official" | "letter" | "counter" | "libre";
  recipient: string;
  official_url: string | null;
  steps: string[];
  sources: SourceRef[];
  confidence: string;
  declarations: { key: string; text: string; kind: "honneur" | "engagement" | "consequence" }[];
  followup_label: string;
  has_letter: boolean;
};
export type Provenance = {
  source: string;
  label: string;
  document_id?: number;
  document_title?: string;
  evidence?: string;
  period?: string;
};
export type ProcedureConflict = { key: string; label: string; value: string; expected: string; document_id: number | null; evidence: string | null };
export type ProcedureField = {
  key: string;
  label: string;
  type: ProcedureFieldType;
  required: boolean;
  active: boolean;
  help: string;
  choices: { value: string; label: string }[];
  value: number | string | boolean | null;
  display: string;
  provenance: Provenance | null;
  missing: boolean;
  conflict: ProcedureConflict | null;
};
export type ProcedureDoc = { id: number; title: string; status: VaultStatus; document_date: string | null };
export type ProcedurePiece = {
  key: string;
  label: string;
  required: boolean;
  count: number;
  help: string;
  missing: boolean;
  attached: ProcedureDoc[];
  candidates: ProcedureDoc[];
};
export type ProcedureEvent = { ts: string; kind: string; detail: Record<string, unknown> };
export type ProcedureView = {
  id: number;
  title: string;
  template: ProcedureTemplate;
  stage: ProcedureStage;
  stage_label: string;
  fields: ProcedureField[];
  pieces: ProcedurePiece[];
  evaluation: { missing_fields: string[]; conflicts: ProcedureConflict[]; missing_pieces: string[]; unverified: number[]; warnings: string[] };
  letter: string | null;
  hash: string;
  approved: boolean;
  approved_stale: boolean;
  reference: string | null;
  notes: string;
  sent_on: string | null;
  next_check: string | null;
  due: boolean;
  deadline: string | null;
  escalation: { label: string; from: string; until: string } | null;
  outcome: { result: string; amount_cents: number | null; period: Period; note: string } | null;
  opportunity_key: string | null;
  created_at: string;
  updated_at: string;
  events: ProcedureEvent[];
};
export type ProcedureRow = {
  id: number;
  title: string;
  template: string;
  stage: ProcedureStage;
  stage_label: string;
  next_check: string | null;
  due: boolean;
  deadline: string | null;
  updated_at: string;
  opportunity_key: string | null;
};
export type ProcedureListing = { procedures: ProcedureRow[]; stages: { key: ProcedureStage; label: string }[]; templates: ProcedureTemplate[] };

// ------------------------------------------------------------------ veille
export type WatchItemView = {
  id: number;
  title: string;
  summary: string;
  url: string | null;
  published_at: string | null;
  first_seen_at: string | null;
  last_verified_at: string | null;
  changed_at: string | null;
  version: number;
  topics: string[];
  topic_labels: string[];
  relevant: boolean;
  status: "new" | "seen" | "dismissed";
  source: { name: string; trust: string } | null;
};
export type WatchSourceView = {
  id: number;
  name: string;
  url: string;
  trust: "officiel" | "technique" | "secondaire" | "perso";
  builtin: boolean;
  enabled: boolean;
  interval_hours: number;
  last_checked_at: string | null;
  last_ok_at: string | null;
  last_error: string | null;
};
export type ReferenceCheck = { key: string; title: string; as_of: string | null; next_check: string; status: "ok" | "stale"; detail: string; source: string | null };
export type WatchListing = {
  enabled: boolean;
  interests: string[];
  interest_labels: Record<string, string>;
  items: WatchItemView[];
  counts: { new_relevant: number; new: number };
  engine: { used: string | null; latest: string; latest_published_at: string | null; outdated: boolean; advice: string | null } | null;
  references: ReferenceCheck[];
  sources: WatchSourceView[];
  report?: { checked: number; new: number; changed: number; errors: number; skipped: number };
};

// ------------------------------------------------------------------ copilote (écran Aujourd'hui, navigation)
export type CopilotSummary = {
  opportunities: Opportunity[];
  value: { confirmed_yearly_cents: number; estimated_yearly_cents: number };
  confirmations: number;
  procedures: { open: number; ready: number; waiting: number; due: { id: number; title: string; next_check: string | null; stage_label: string }[] };
  watch: { enabled: boolean; new_relevant: number; stale_references: number; engine_outdated: boolean };
};

// ------------------------------------------------------------------ messageries
export type ChannelStatus = "off" | "connecting" | "online" | "error";
export type ChannelField = {
  key: string;
  label: string;
  secret: boolean;
  required: boolean;
  example: string;
  set: boolean;
  hint: string | null;
  value: string | null;
};
export type ChannelLinkView = {
  id: number;
  label: string;
  notify: boolean;
  linked_at: string;
  last_seen_at: string | null;
  last_notified_at: string | null;
  failures: number;
  last_error: string | null;
};
export type ChannelPlatform = {
  key: string;
  label: string;
  inbound: boolean;
  e2e: boolean;
  fields: ChannelField[];
  configured: boolean;
  enabled: boolean;
  status: ChannelStatus;
  bot_name: string | null;
  privacy: PrivacyMode;
  last_error: string | null;
  last_ok_at: string | null;
  ignored: number;
  links: ChannelLinkView[];
  extra: Record<string, string | null | undefined>;
};
export type ChannelsView = {
  server_enabled: boolean;
  consent: boolean;
  pairing: { active: boolean; expires_at: string | null };
  quiet_hours: [number, number];
  weekly: boolean;
  platforms: ChannelPlatform[];
};
export type PairingCode = { code: string; raw: string; expires_at: string };

// ------------------------------------------------------------------ crypto (lecture seule)
export type CryptoAsset = {
  asset: string;
  name: string;
  quantity: string;
  value_cents: number;
  share_percent: number;
  price_eur: string | null;
  change_24h: string | null;
  price_at: string | null;
  stale: boolean;
  stable: string | null;
  sources: { where: string; quantity: string; watched: boolean }[];
};
export type CryptoWalletView = {
  id: number;
  label: string;
  chain: string;
  chain_label: string;
  address: string;
  masked: string;
  synced_at: string | null;
  error: string | null;
};
export type CryptoManual = {
  id: number;
  asset: string;
  quantity: string;
  platform: string | null;
  foreign: boolean;
  coingecko_id: string | null;
  updated_at: string;
};
export type CryptoResource = { label: string; url: string };
export type CryptoTaxEventView = {
  id: number;
  day: string;
  kind: "acquisition" | "cession";
  amount_cents: number;
  fees_cents: number;
  portfolio_value_cents: number | null;
  asset: string | null;
  note: string | null;
};
export type CryptoTax = {
  year: number;
  error: string | null;
  total_price_cents: number;
  threshold_cents: number;
  exempt: boolean;
  net_gain_cents: number;
  taxable_cents: number;
  estimated_tax_cents: number | null;
  rate: { total_percent: string | null; social_percent: string | null; status: string; note: string; checked: string } | null;
  cessions: { id: number; day: string; price_cents: number; fees_cents: number; portfolio_value_cents: number; acquisition_share_cents: number; gain_cents: number }[];
  acquisition_basis_cents: number;
  current_value_cents: number;
  unrealized_cents: number | null;
  foreign_accounts: string[];
  reminders: string[];
  events: CryptoTaxEventView[];
  sources: CryptoResource[];
  disclaimer: string;
};
export type AutopilotSummary = { active: boolean; trial: boolean; agent: string | null; expires_at: string | null };
export type CryptoView = {
  total_cents: number;
  change_24h_cents: number;
  assets: CryptoAsset[];
  wallets: CryptoWalletView[];
  manual: CryptoManual[];
  history: { day: string; value_cents: number }[];
  sentiment: { value: number; label: string; at?: string } | null;
  tax: CryptoTax;
  guards: { level: "info" | "warning"; text: string }[];
  network_consent: boolean;
  endpoints: Record<string, { custom: boolean; shown: string }>;
  chains: { key: string; label: string; native: string; tokens: string[] }[];
  catalog: { symbol: string; name: string }[];
  in_wealth: boolean;
  autopilot: AutopilotSummary;
  resources: CryptoResource[];
  disclaimer: string;
};
export type ScamCheck = {
  level: "ok" | "warning" | "danger";
  verdict: string;
  signals: { key: string; label: string }[];
  advice: string[];
  resources: CryptoResource[];
};

// ------------------------------------------------------------------ pilote automatique
export type PilotKind = "buy" | "sell" | "swap" | "transfer";
export type PilotRule = {
  id?: string;
  label?: string;
  kind: PilotKind;
  asset: string;
  to_asset: string | null;
  destination: string | null;
  amount_cents: number;
  every: "day" | "week" | "month";
  done_this_period?: boolean;
};
export type PilotDestination = { label: string; chain: string; address: string };
export type PilotMove = {
  id: number;
  agent: string;
  kind: string;
  asset: string;
  to_asset: string | null;
  chain: string | null;
  amount_cents: number;
  executed_cents: number | null;
  pnl_cents: number | null;
  destination: string | null;
  rule: string | null;
  reason: string;
  status: "reserved" | "executed" | "failed" | "unreported" | "revoked" | "simulated" | "refused";
  refusal: string | null;
  tx_ref: string | null;
  text: string;
  created_at: string;
  reported_at: string | null;
};
export type PilotLimits = {
  per_op_cents: number;
  per_day_cents: number;
  per_month_cents: number;
  max_moves_per_day: number;
  max_loss_cents: number;
};
export type PilotView = {
  active: boolean;
  enabled: boolean;
  trial: boolean;
  agent_token_id: number | null;
  agent_name: string | null;
  mode: "rules" | "free";
  scopes: ("crypto_spot" | "transfer")[];
  assets: string[];
  rules: PilotRule[];
  destinations: PilotDestination[];
  limits: PilotLimits;
  enabled_at: string | null;
  expires_at: string | null;
  disabled_at: string | null;
  disabled_reason: string | null;
  usage: { day_cents: number; month_cents: number; moves_day: number; loss_cents: number };
  agents: { id: number; name: string; ip_locked: boolean }[];
  no_crypto: boolean;
  scope_labels: Record<string, string>;
  every_labels: Record<string, string>;
  chains: string[];
  hard_rules: string[];
  hard_max: { per_op_cents: number; per_day_cents: number; per_month_cents: number; days: number };
  journal: PilotMove[];
};
