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
