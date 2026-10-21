import { useMemo, useState, type FormEvent } from "react";
import { api, useApi, type Account, type Category, type ImportResult, type Transaction } from "../api";
import { Amount, ErrorNote, Field, Loading, useAsyncAction, useToast } from "../components/ui";
import { dayNumber, money, monthLabel, plural, shortDate } from "../format";

type Page = { items: Transaction[]; total: number };
const PAGE = 100;

export function Operations() {
  const accounts = useApi<Account[]>("/api/accounts");
  const categories = useApi<Category[]>("/api/categories");
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("");
  const [account, setAccount] = useState("");
  const [limit, setLimit] = useState(PAGE);
  const [showImport, setShowImport] = useState(false);

  const params = new URLSearchParams({ limit: String(limit) });
  if (query) params.set("q", query);
  if (category) params.set("category", category);
  if (account) params.set("account_id", account);
  const list = useApi<Page>(`/api/transactions?${params.toString()}`);

  const submitSearch = (e: FormEvent) => {
    e.preventDefault();
    setLimit(PAGE);
    setQuery(q.trim());
  };

  const activeAccounts = (accounts.data ?? []).filter((a) => !a.archived);

  return (
    <>
      <div className="page-head">
        <h1>Opérations</h1>
        <button className="btn" onClick={() => setShowImport((v) => !v)} aria-expanded={showImport}>
          {showImport ? "Fermer l'import" : "Importer un relevé"}
        </button>
      </div>

      {showImport && (
        <ImportPanel accounts={activeAccounts} onImported={() => { list.reload(); accounts.reload(); }} />
      )}

      <form className="form-grid" onSubmit={submitSearch} role="search" style={{ marginTop: "1.5rem" }}>
        <Field label="Rechercher">
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="libellé, marchand, note" />
        </Field>
        <Field label="Catégorie">
          <select value={category} onChange={(e) => { setCategory(e.target.value); setLimit(PAGE); }}>
            <option value="">Toutes</option>
            {(categories.data ?? []).map((c) => (
              <option key={c.slug} value={c.slug}>{c.name}</option>
            ))}
          </select>
        </Field>
        <Field label="Compte">
          <select value={account} onChange={(e) => { setAccount(e.target.value); setLimit(PAGE); }}>
            <option value="">Tous</option>
            {activeAccounts.map((a) => (
              <option key={a.id} value={a.id}>{a.name}</option>
            ))}
          </select>
        </Field>
      </form>

      <section className="section" aria-live="polite">
        {list.error && <ErrorNote message={list.error} onRetry={list.reload} />}
        {!list.data && !list.error && <Loading />}
        {list.data && list.data.total === 0 && (
          <p className="muted">
            {query || category || account ? "Aucune opération ne correspond." : "Aucune opération pour l'instant : importe un relevé pour commencer."}
          </p>
        )}
        {list.data && list.data.total > 0 && (
          <>
            <p className="faint" style={{ marginBottom: "1rem" }}>
              {plural(list.data.total, "opération", "opérations")}
            </p>
            <TransactionLedger items={list.data.items} categories={categories.data ?? []} onChanged={list.reload} />
            {list.data.items.length < list.data.total && (
              <div className="actions">
                <button className="btn quiet" onClick={() => setLimit((l) => Math.min(500, l + PAGE))}
                        disabled={limit >= 500}>
                  Afficher plus
                </button>
              </div>
            )}
          </>
        )}
      </section>
    </>
  );
}

function TransactionLedger({ items, categories, onChanged }: { items: Transaction[]; categories: Category[]; onChanged: () => void }) {
  const toast = useToast();
  const days = useMemo(() => {
    const map = new Map<string, Transaction[]>();
    for (const t of items) map.set(t.booking_date, [...(map.get(t.booking_date) ?? []), t]);
    return [...map.entries()];
  }, [items]);

  const recategorize = async (t: Transaction, slug: string) => {
    try {
      const r = await api<{ propagated: number }>(`/api/transactions/${t.id}`, {
        method: "PATCH",
        body: { category: slug, learn: true },
      });
      const name = categories.find((c) => c.slug === slug)?.name ?? slug;
      toast(r.propagated > 0
        ? `Classé en ${name}. Appris pour ${t.merchant} : ${plural(r.propagated, "autre opération", "autres opérations")} mises à jour.`
        : `Classé en ${name}. Les prochaines opérations ${t.merchant} suivront.`);
      onChanged();
    } catch (e) {
      toast((e as Error).message);
    }
  };

  return (
    <ul className="ledger">
      {days.map(([date, txns]) => (
        <li className="ledger-day" key={date}>
          <div className="ledger-date" aria-hidden="true">
            <strong>{dayNumber(date)}</strong>
            <span>{monthLabel(date.slice(0, 7))}</span>
          </div>
          {txns.map((t) => (
            <div className="ledger-row txn" key={t.id}>
              <div className="ledger-label">
                <span className="sr-only">{shortDate(date)} : </span>
                <b>{t.merchant || t.label}</b>{" "}
                {t.aid_kind && <span className="tag ok">aide</span>}{" "}
                {t.is_transfer && <span className="tag">virement interne</span>}
                <span className="raw" title={t.label}>{t.label}</span>
              </div>
              <select
                  className="inline"
                  aria-label={`Catégorie de ${t.merchant || t.label}`}
                  value={t.category ?? "a_categoriser"}
                  onChange={(e) => void recategorize(t, e.target.value)}
                  disabled={t.is_transfer}
                >
                  {categories.map((c) => (
                    <option key={c.slug} value={c.slug}>{c.name}</option>
                  ))}
                </select>
              <Amount cents={t.amount_cents} />
            </div>
          ))}
        </li>
      ))}
    </ul>
  );
}

function ImportPanel({ accounts, onImported }: { accounts: Account[]; onImported: () => void }) {
  const [accountId, setAccountId] = useState<string>(accounts[0] ? String(accounts[0].id) : "");
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<ImportResult | null>(null);
  const [busy, error, run] = useAsyncAction();
  const toast = useToast();

  const send = (dryRun: boolean) =>
    run(async () => {
      if (!file || !accountId) return;
      const form = new FormData();
      form.append("file", file);
      form.append("account_id", accountId);
      form.append("dry_run", String(dryRun));
      const r = await api<ImportResult>("/api/imports", { method: "POST", form });
      if (dryRun) {
        setPreview(r);
        return;
      }
      setPreview(null);
      setFile(null);
      toast(
        `${plural(r.inserted, "opération importée", "opérations importées")}` +
          (r.duplicates ? `, ${plural(r.duplicates, "doublon ignoré", "doublons ignorés")}` : "") +
          (r.reconciled ? `, ${plural(r.reconciled, "rapprochée", "rapprochées")} avec la banque` : "") +
          (r.aids_detected ? `, ${plural(r.aids_detected, "aide reconnue", "aides reconnues")}` : "") +
          ".",
      );
      onImported();
    });

  if (accounts.length === 0) {
    return (
      <div className="notice">
        Crée d'abord le compte qui reçoit ce relevé, dans <a href="#/reglages">Réglages</a>.
      </div>
    );
  }

  return (
    <div className="panel">
      <div className="form-grid">
        <Field label="Compte">
          <select value={accountId} onChange={(e) => { setAccountId(e.target.value); setPreview(null); }}>
            {accounts.map((a) => (
              <option key={a.id} value={a.id}>{a.name}</option>
            ))}
          </select>
        </Field>
        <Field label="Relevé (CSV, OFX, QFX)" hint="Export de ton espace bancaire, toutes banques.">
          <input type="file" accept=".csv,.txt,.ofx,.qfx,text/csv"
                 onChange={(e) => { setFile(e.target.files?.[0] ?? null); setPreview(null); }} />
        </Field>
      </div>
      <div className="actions">
        <button className="btn quiet" disabled={!file || busy} onClick={() => void send(true)}>Vérifier le fichier</button>
        {preview && (
          <button className="btn" disabled={busy} onClick={() => void send(false)}>
            Importer {plural(preview.parsed, "opération", "opérations")}
          </button>
        )}
      </div>
      {error && <p className="error" role="alert" style={{ marginTop: "0.75rem" }}>{error}</p>}
      {preview && (
        <div className="stack" style={{ marginTop: "1rem" }}>
          {preview.already_imported && (
            <div className="notice warn">Ce fichier a déjà été importé : les opérations connues seront ignorées.</div>
          )}
          <p className="muted">
            Format {preview.format.toUpperCase()}
            {preview.detected.encoding && `, encodage ${preview.detected.encoding}`}
            {preview.skipped.length > 0 && `, ${plural(preview.skipped.length, "ligne ignorée", "lignes ignorées")}`}.
          </p>
          <table className="plain">
            <thead>
              <tr><th>Date</th><th>Libellé</th><th className="num">Montant</th></tr>
            </thead>
            <tbody>
              {preview.preview.map((p, i) => (
                <tr key={i}>
                  <td>{shortDate(p.booking_date)}</td>
                  <td>{p.label}</td>
                  <td className="num">{money(p.amount_cents)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {preview.skipped.length > 0 && (
            <p className="faint">
              Lignes ignorées : {preview.skipped.slice(0, 6).map((s) => `ligne ${s.line} (${s.reason})`).join(", ")}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
