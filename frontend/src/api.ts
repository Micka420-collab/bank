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

/** Lecteur de flux SSE (« event: » / « data: », cadres séparés par une ligne vide). */
export function parseSseChunk(buffer: string): { frames: { event: string; data: string }[]; rest: string } {
  const frames: { event: string; data: string }[] = [];
  let rest = buffer;
  for (;;) {
    const cut = rest.indexOf("\n\n");
    if (cut < 0) break;
    const raw = rest.slice(0, cut);
    rest = rest.slice(cut + 2);
    let event = "message";
    const data: string[] = [];
    for (const line of raw.split("\n")) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
    }
    if (data.length) frames.push({ event, data: data.join("\n") });
  }
  return { frames, rest };
}

export type StreamHandlers = {
  onStatus?: (text: string, tool?: string) => void;
  onDelta?: (text: string) => void;
  onReset?: () => void;
};

/** Une question à l'assistant, répondue en direct : les statuts et les morceaux de texte arrivent au fil de
 * l'eau, la réponse complète (avec ses blocs) à la fin. Un serveur qui ne sait pas diffuser déclenche le repli
 * vers la réponse en un bloc. */
export async function askStream(
  body: { question: string; conversation_id: number | null },
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<AssistantAnswer> {
  const headers: Record<string, string> = { Accept: "text/event-stream", "Content-Type": "application/json", "X-CSRF-Token": readCookie("bank_csrf") };
  const res = await fetch("/api/assistant/ask/stream", { method: "POST", headers, body: JSON.stringify(body), credentials: "same-origin", cache: "no-store", signal });
  if (res.status === 401) window.dispatchEvent(new Event("bank:unauthorized"));
  if (res.status === 404 || res.status === 405) return api<AssistantAnswer>("/api/assistant/ask", { method: "POST", body, signal });
  if (!res.ok) {
    let detail: unknown = null;
    try {
      detail = (await res.json()).detail;
    } catch {
      /* corps non JSON */
    }
    throw new ApiError(res.status, errorMessage(detail, statusMessage(res.status)));
  }
  if (!res.body || !(res.headers.get("content-type") ?? "").includes("text/event-stream")) {
    return (await res.json()) as AssistantAnswer;
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let result: AssistantAnswer | null = null;
  for (;;) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done });
    const parsed = parseSseChunk(done ? buffer + "\n\n" : buffer);
    buffer = parsed.rest;
    for (const f of parsed.frames) {
      let data: unknown = null;
      try {
        data = JSON.parse(f.data);
      } catch {
        continue;
      }
      const d = (data ?? {}) as Record<string, unknown>;
      if (f.event === "status") handlers.onStatus?.(String(d.text ?? ""), typeof d.tool === "string" ? d.tool : undefined);
      else if (f.event === "delta") handlers.onDelta?.(String(d.text ?? ""));
      else if (f.event === "reset") handlers.onReset?.();
      else if (f.event === "done") result = data as AssistantAnswer;
      else if (f.event === "error") throw new ApiError(Number(d.status ?? 500), errorMessage(d.detail, statusMessage(Number(d.status ?? 500))));
    }
    if (done) break;
  }
  if (!result) throw new ApiError(502, "La réponse s'est interrompue : réessaie.");
  window.dispatchEvent(new Event("bank:changed"));
  return result;
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

// ------------------------------------------------------------------ boîte mail (lecture seule)
export type MailProposal = {
  id: number;
  sender: string;
  sender_domain: string;
  subject: string;
  filename: string;
  size: number;
  kind_guess: string;
  kind_label: string;
  confidence: number;
  received_at: string | null;
  status: "proposed" | "imported" | "dismissed";
  document_id: number | null;
};
export type MailStatus = {
  configured: boolean;
  consent: boolean;
  asked: boolean;
  account: {
    host: string;
    port: number;
    security: "ssl" | "starttls";
    username_hint: string;
    folder: string;
    senders: string[];
    status: "active" | "error";
    last_sync_at: string | null;
    last_error: string | null;
    last_uid: number;
  } | null;
  proposals: MailProposal[];
  recent: MailProposal[];
  limits: { messages_per_scan: number; attachment_bytes: number; every_minutes: number };
  document_ids?: number[];
};

// ------------------------------------------------------------------ sauvegardes
export type BackupRun = {
  id: number;
  kind: "backup" | "verify";
  status: "ok" | "failed";
  trigger: "auto" | "manual";
  started_at: string;
  finished_at: string | null;
  details: {
    file?: string;
    cipher_bytes?: number;
    plain_bytes?: number;
    tables?: Record<string, number>;
    copies?: string[];
    pruned?: number;
    seconds?: number;
    warnings?: string[];
    problems?: string[];
    error?: string;
  };
};
export type BackupStatus = {
  enabled: boolean;
  level: "ok" | "unverified" | "stale" | "none" | "failed";
  directory: string;
  remote_directory: string | null;
  remote_reachable: boolean;
  hour: number;
  keep: number;
  verify_weekday: number;
  next_backup: string;
  last_backup: BackupRun | null;
  last_ok_backup: BackupRun | null;
  last_verify: BackupRun | null;
  runs: BackupRun[];
  files: { name: string; bytes: number; modified_at: string; manifest: boolean }[];
  remote_files: { name: string; bytes: number; modified_at: string; manifest: boolean }[];
  restore_hint: string;
  run?: BackupRun;
};

// ------------------------------------------------------------------ crédits
export type LoanRule = { title: string; url: string | null; checked: string };
export type LoanView = {
  id: number;
  name: string;
  lender: string | null;
  kind: "immobilier" | "consommation" | "auto" | "autre";
  kind_label: string;
  principal_cents: number;
  annual_rate_bp: number;
  months: number;
  first_payment: string;
  insurance_monthly_cents: number;
  account_id: number | null;
  bank_remaining_cents: number | null;
  payment_cents: number;
  monthly_with_insurance_cents: number;
  total_interest_cents: number;
  total_insurance_cents: number;
  total_cost_cents: number;
  end: string;
  paid_rows: number;
  remaining_cents: number;
  paid_principal_cents: number;
  paid_interest_cents: number;
  paid_insurance_cents: number;
  next_payment: string | null;
  next_payment_cents: number;
  months_left: number;
  interest_left_cents: number;
  progress: number;
  rules_recheck: boolean;
  notes: string[];
  matched: { merchant: string; amount_cents: number; last_date: string; next_date: string; late: boolean; occurrences: number } | null;
};
export type LoansResponse = {
  loans: LoanView[];
  total_remaining_cents: number;
  total_monthly_cents: number;
  credit_accounts: { id: number; name: string; balance_cents: number }[];
  kinds: { key: LoanView["kind"]; label: string }[];
  rules: Record<string, LoanRule>;
  disclaimer: string;
  created_id?: number;
};
export type LoanSchedule = {
  payment_cents: number;
  rows: { index: number; day: string; payment_cents: number; interest_cents: number; principal_cents: number; insurance_cents: number; remaining_cents: number }[];
  yearly: { year: number; payments_cents: number; interest_cents: number; principal_cents: number; insurance_cents: number; remaining_cents: number; months: number }[];
  total_interest_cents: number;
  total_insurance_cents: number;
};
export type EarlyRepayment = {
  on: string;
  amount_cents: number;
  mode: "shorter" | "lower" | "full";
  indemnity_cents: number;
  indemnity_rule: LoanRule | null;
  indemnity_explanation: string;
  remaining_before_cents: number;
  remaining_after_cents: number;
  old_payment_cents: number;
  new_payment_cents: number;
  old_months_left: number;
  new_months_left: number;
  interest_saved_cents: number;
  insurance_saved_cents: number;
  net_gain_cents: number;
  new_end: string | null;
};
export type Renegotiation = {
  on: string;
  old_rate_bp: number;
  new_rate_bp: number;
  remaining_cents: number;
  months_left: number;
  old_payment_cents: number;
  new_payment_cents: number;
  old_interest_left_cents: number;
  new_interest_cents: number;
  fees_cents: number;
  indemnity_cents: number;
  net_gain_cents: number;
  worth_it: boolean;
};

// ------------------------------------------------------------------ plan B « perte de revenu »
export type PlanBIncomes = {
  salary_cents: number;
  unemployment_cents: number;
  pension_cents: number;
  aids_cents: number;
  other_cents: number;
  total_cents: number;
};
export type PlanBRule = { key: string; title: string; url: string | null; checked: string; detail: string };
export type PlanBAction = {
  key: string;
  title: string;
  why: string;
  when: string;
  kind: "procedure" | "link" | "check";
  amount_cents: number;
  template?: string;
  url?: string;
  rule?: PlanBRule;
};
export type PlanBScenario = {
  key: "stop" | "drop" | "end_of_rights";
  title: string;
  lead: string;
  incomes_after: PlanBIncomes;
  income_before_cents: number;
  income_after_cents: number;
  lost_cents: number;
  needs_cents: number;
  gap_cents: number;
  cash_cents: number;
  months: number | null;
  runway_month: string | null;
  runway_label: string | null;
  status: "ok" | "tight" | "risk";
  are: {
    low_cents: number;
    high_cents: number;
    formula_cents: number;
    sjr_day_cents: number;
    floor_applied: boolean;
    rule: PlanBRule;
    simulator: PlanBRule;
    note: string;
  } | null;
  aids: { variable: string; label: string; before_cents: number; after_cents: number; delta_cents: number }[];
  aids_total_before_cents: number;
  aids_total_after_cents: number;
  aids_note: string;
  css: string;
  actions: PlanBAction[];
  notes: string[];
};
export type PlanB = {
  today: string;
  drop_percent: number;
  incomes: PlanBIncomes;
  needs: { fixed_cents: number; variable_cents: number; provision_cents: number; total_cents: number };
  cash_cents: number;
  main_income: string;
  scenarios: PlanBScenario[];
  sources: ({ key: string; title: string; kind: string } & Partial<PlanBRule>)[];
  rules_checked: string;
  rules_recheck: boolean;
  notes: string[];
  disclaimer: string;
};

// ------------------------------------------------------------------ retraite
export type RetirementStatus = "before_legal" | "decote" | "full" | "surcote";
export type RetirementDeparture = {
  at: string;
  age_years: number;
  age_months: number;
  quarters_total: number;
  quarters_required: number;
  missing_quarters: number;
  extra_quarters: number;
  rate_percent: string;
  status: RetirementStatus;
  base_monthly_low_cents: number;
  base_monthly_high_cents: number;
  complementary_monthly_low_cents: number;
  complementary_monthly_high_cents: number;
  total_low_cents: number;
  total_high_cents: number;
  points: string;
  replacement_percent: number | null;
  notes: string[];
};
export type RetirementCareer = {
  birth_date: string;
  age_years: number;
  quarters_acquired: number;
  as_of: string;
  quarters_required: number;
  legal_age_years: number;
  legal_age_months: number;
  gross_annual_cents: number;
  salary_source: string;
  best_years_average_cents: number | null;
  agirc_arrco_points: string | null;
  points_per_year: string;
  working: boolean;
  quarter_salary_cents: number;
};
export type RetirementPer = {
  payment_cents: number;
  ceiling_cents: number;
  deductible_cents: number;
  marginal_rate_percent: number;
  tax_saved_cents: number;
  effort_cents: number;
  notes: string[];
  tax_source: string;
  tax_detail: string;
};
export type RetirementProfile = {
  quarters_acquired?: number;
  as_of?: string;
  best_years_average_cents?: number | null;
  agirc_arrco_points?: string | null;
  career_start_year?: number | null;
  working?: boolean;
  birth_date?: string | null;
};
export type Retirement = {
  profile: RetirementProfile;
  notes: string[];
  rules: Record<string, LoanRule>;
  rules_checked: string;
  rules_recheck: boolean;
  disclaimer: string;
  career: RetirementCareer | null;
  departures: Partial<Record<"legal" | "full_rate" | "age_67" | "custom", RetirementDeparture>>;
  per: RetirementPer | null;
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
/** Blocs interactifs d'une réponse : construits et validés par le serveur, jamais par le modèle seul. */
export type ChatActionItem =
  | { key: string; label: string; kind: "navigate"; page: string }
  | { key: string; label: string; kind: "ask"; prompt: string }
  | { key: string; label: string; kind: "act"; card: string; action: string };
export type ChartItem = { label: string; value?: number; a?: number; b?: number; color?: string };
export type ChatBlock =
  | { type: "actions"; items: ChatActionItem[] }
  | { type: "chart"; kind: "donut" | "bars" | "pairs" | "line"; title: string; unit: "eur" | "pct" | "kwh" | "count"; items: ChartItem[]; source: string; dataset: string }
  | { type: "diagram"; kind: "flow" | "steps"; title: string; nodes: { id: string; label: string; note?: string }[]; edges: { from: string; to: string; label?: string }[] }
  | { type: "sources"; query: string; items: { n: number; title: string; url: string; host: string; snippet: string; date: string }[] }
  | { type: "note"; tone: "info" | "warning"; text: string };
export type AssistantOpening = {
  text: string;
  blocks: ChatBlock[];
  score: number;
  next: { key: string; title: string; state: string; detail: string; unlocks: string; page: string; label: string }[];
};
export type SearchStatus = {
  configured: boolean;
  consent: boolean;
  asked: boolean;
  kinds: Record<"searxng" | "brave", string>;
  kind?: "searxng" | "brave";
  base_url?: string | null;
  key_prefix?: string | null;
  daily_cap?: number;
  used_today?: number;
  last_used_at?: string | null;
  last_error?: string | null;
};
export type AssistantStatus = {
  configured: boolean;
  privacy_modes: Record<AssistantPrivacy, string>;
  name: string;
  memory: MemoryStatus;
  search?: { configured: boolean; consent: boolean; asked: boolean };
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
  blocks: ChatBlock[];
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
  messages: { id: number; role: "user" | "assistant"; content: string; ts: string; meta: MessageMeta; feedback: "up" | "down" | null; blocks?: ChatBlock[] }[];
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
export type VaultShareState = "active" | "used" | "revoked" | "expired";
export type VaultShare = {
  id: number;
  recipient: string;
  created_at: string;
  expires_at: string;
  used_at: string | null;
  revoked_at: string | null;
  state: VaultShareState;
};

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
// ------------------------------------------------------------------ clés d'API de plateforme (lecture seule)
export type ExchangePlatformKey = "kraken" | "binance" | "coinbase";
export type ExchangePlatform = {
  key: ExchangePlatformKey;
  label: string;
  help: string;
  url: string;
  key_label: string;
  secret_label: string;
};
export type ExchangeRights = {
  read?: boolean;
  trade?: boolean;
  withdraw?: boolean;
  source?: "permissions" | "probes";
  ip_restricted?: boolean | null;
};
export type ExchangeKeyView = {
  id: number;
  platform: ExchangePlatformKey;
  platform_label: string;
  label: string;
  key_hint: string;
  foreign: boolean;
  rights: ExchangeRights;
  verified_at: string;
  synced_at: string | null;
  error: string | null;
  assets: { asset: string; quantity: string }[];
};
export type ExchangeKeysView = {
  keys: ExchangeKeyView[];
  platforms: ExchangePlatform[];
  network_consent: boolean;
  max_keys: number;
  created_id?: number;
  view?: CryptoView;
};
export type ScamCheck = {
  level: "ok" | "warning" | "danger";
  verdict: string;
  signals: { key: string; label: string }[];
  advice: string[];
  resources: CryptoResource[];
};
export type CryptoImportEvent = {
  key: string;
  line: number;
  day: string;
  kind: "acquisition" | "cession";
  asset: string | null;
  quantity: string | null;
  amount_cents: number;
  fees_cents: number;
  portfolio_value_cents: number | null;
  value_source: "suivi" | "saisie" | null;
  value_day: string | null; // jour du relevé du suivi proposé (la veille, sinon le jour même)
  status: "new" | "duplicate" | "to_complete" | "manual_match" | "excluded";
  issue: string | null;
};
export type CryptoImportPreview = {
  platform: "kraken" | "binance" | "coinbase";
  platform_label: string;
  format: string;
  rows: number;
  counts: {
    acquisitions: number; cessions: number; to_complete: number; duplicates: number; ignored: number;
    manual_matches: number; excluded: number;
  };
  events: CryptoImportEvent[];
  truncated: boolean;
  reasons: { reason: string; count: number }[];
  ignored: { line: number; reason: string }[];
  years: number[];
  created?: number;
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
  on?: number | null; // jour prévu : 1 = lundi … 7 = dimanche, ou jour du mois (1 … 31)
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

// ------------------------------------------------------------------ alertes de cours
export type PriceAlertKind = "above" | "below" | "change";
export type PriceAlert = {
  id: number;
  asset: string;
  kind: PriceAlertKind;
  threshold: string;
  threshold_text: string;
  label: string | null;
  state: "armed" | "triggered";
  fires: number;
  triggered_at: string | null;
  triggered_price: string | null;
  price_eur: string | null;
  change_24h: string | null;
  created_at: string;
};
export type PriceAlertsView = {
  alerts: PriceAlert[];
  assets: string[];
  kinds: Record<PriceAlertKind, string>;
  max: number;
  bounds: { change_min: string; change_max: string; margin_percent: string };
};

// ------------------------------------------------------------------ pilote vs consigne
export type PilotSlotStatus = "on_time" | "late" | "missed" | "waiting" | "in_progress" | "off";
export type PilotSlot = {
  period: string;
  start: string;
  end: string;
  due: string;
  status: PilotSlotStatus;
  move_id: number | null;
  done_on: string | null;
  delay_days: number;
  amount_cents: number | null;
  gap_cents: number;
  gap_percent: number;
  amount_differs: boolean;
  trial: boolean;
  note: string | null;
  refusals: string[];
};
export type PilotRuleTrack = {
  id: string;
  label: string;
  every: "day" | "week" | "month";
  on: number | null;
  amount_cents: number;
  slots: PilotSlot[];
  totals: { due: number; on_time: number; late: number; missed: number; different: number; expected_cents: number; done_cents: number; gap_cents: number };
};
export type PilotTrack = {
  rules: PilotRuleTrack[];
  outside: { move_id: number; text: string; status: PilotMove["status"]; amount_cents: number; created_at: string; why: string }[];
  since: string;
};
// Ton logement : estimation par les ventes voisines (DVF), fourchette, extraction trimestrielle, prêt en face.
export type HomeKind = "appartement" | "maison";
export type HomeProfile = {
  kind: HomeKind;
  surface: number;
  code: string;
  commune: string | null;
  section: string | null;
  rooms: number | null;
  in_wealth: boolean;
  declared_as?: string | null; // libellé sous lequel il est déjà déclaré à l'agent (absent : description d'avant)
};
export type HomeEstimate = {
  ok: boolean;
  kind: HomeKind;
  surface: number;
  count: number;
  window_start: string | null;
  window_end: string | null;
  message: string;
  median_cents: number;
  low_cents: number;
  high_cents: number;
  median_m2_cents: number;
  low_m2_cents: number;
  high_m2_cents: number;
  first_sale: string | null;
  last_sale: string | null;
  tolerance_percent: number;
  scope: "section" | "commune";
  rooms_used: boolean;
  outliers: number;
  notes: string[];
  code: string;
};
export type HomeView = {
  consent: boolean;
  profile: HomeProfile | null;
  estimate: HomeEstimate | null;
  data: {
    matches: boolean;
    fetched_at: string | null;
    latest_sale: string | null;
    sales: number;
    years: number[];
    fresh: boolean;
    next_refresh: string;
    error: string | null;
  };
  in_wealth: boolean;
  declared_property: { label: string; value_cents: number }[];
  declared_match: string | null;
  loans: { id: number; name: string; remaining_cents: number }[];
  loans_remaining_cents: number;
  net_cents: number | null;
  kinds: { key: HomeKind; label: string }[];
  bounds: { surface: [number, number]; rooms: [number, number] };
  source: string;
  source_url: string;
  disclaimer: string;
};
export type HomeRefresh = { status: "refreshed" | "fresh" | "failed" | "capped"; message: string };
export type CommuneHit = { code: string; name: string; postcodes: string[] };
