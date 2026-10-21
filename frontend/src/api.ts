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
    throw new ApiError(res.status, errorMessage(detail, `Erreur ${res.status}`));
  }
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

export type Me = { username: string; totp_enabled: boolean; recovery_codes_left: number; session_expires_at: string };

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
  | { kind: "category"; options: { value: string; label: string }[] };
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
