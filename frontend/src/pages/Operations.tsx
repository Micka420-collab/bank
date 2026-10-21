import { useMemo, useState, type FormEvent } from "react";
import { api, useApi, type Account, type Category, type ImportResult, type Transaction } from "../api";
import { Icon } from "../components/Icon";
import { Amount, ErrorNote, Field, Loading, useAsyncAction, useToast } from "../components/ui";
import { dayNumber, merchantCase, money, monthLabel, plural, shortDate } from "../format";

const MONTHS_LONG = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"];
function monthTitle(key: string): string {
  const [y, m] = key.split("-").map(Number);
  return `${MONTHS_LONG[(m ?? 1) - 1]} ${y}`;
}

type Page = { items: Transaction[]; total: number };
const PAGE = 100;

export function Operations({ openImport = false }: { openImport?: boolean }) {
  const accounts = useApi<Account[]>("/api/accounts");
  const categories = useApi<Category[]>("/api/categories");
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("");
  const [account, setAccount] = useState("");
  const [limit, setLimit] = useState(PAGE);
  const [showImport, setShowImport] = useState(openImport);

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

  const items = list.data?.items ?? [];
  const sumIn = items.filter((t) => t.amount_cents > 0 && !t.is_transfer).reduce((a, t) => a + t.amount_cents, 0);
  const sumOut = items.filter((t) => t.amount_cents < 0 && !t.is_transfer).reduce((a, t) => a + t.amount_cents, 0);
  const filtered = Boolean(query || category || account);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Opérations</h1>
          <p className="muted">Tout ce qui entre et sort, classé. Corrige une catégorie : Bank apprend pour les suivantes.</p>
        </div>
        <button className="btn" onClick={() => setShowImport((v) => !v)} aria-expanded={showImport}>
          <Icon name={showImport ? "close" : "upload"} /> {showImport ? "Fermer l'import" : "Importer un relevé"}
        </button>
      </div>

      {showImport && (
        <ImportPanel accounts={activeAccounts} onImported={() => { list.reload(); accounts.reload(); }} />
      )}

      <form className="card filters" onSubmit={submitSearch} role="search" aria-label="Filtrer les opérations">
        <label className="search">
          <Icon name="search" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Libellé, marchand, note…" aria-label="Rechercher" />
        </label>
        <select className="inline" value={category} aria-label="Catégorie" onChange={(e) => { setCategory(e.target.value); setLimit(PAGE); }}>
          <option value="">Toutes les catégories</option>
          {(categories.data ?? []).map((c) => (
            <option key={c.slug} value={c.slug}>{c.name}</option>
          ))}
        </select>
        <select className="inline" value={account} aria-label="Compte" onChange={(e) => { setAccount(e.target.value); setLimit(PAGE); }}>
          <option value="">Tous les comptes</option>
          {activeAccounts.map((a) => (
            <option key={a.id} value={a.id}>{a.name}</option>
          ))}
        </select>
        {filtered && (
          <button type="button" className="btn ghost sm" onClick={() => { setQ(""); setQuery(""); setCategory(""); setAccount(""); setLimit(PAGE); }}>
            <Icon name="close" /> Effacer
          </button>
        )}
      </form>

      <section className="card" style={{ marginTop: "1rem" }} aria-live="polite">
        {list.error && <ErrorNote message={list.error} onRetry={list.reload} />}
        {!list.data && !list.error && <Loading />}
        {list.data && list.data.total === 0 && (
          filtered ? (
            <p className="muted">Aucune opération ne correspond.</p>
          ) : (
            <div className="empty-inline">
              <p className="muted">Aucune opération pour l'instant.</p>
              <div className="actions" style={{ marginTop: "0.5rem" }}>
                <button className="btn" onClick={() => setShowImport(true)}><Icon name="upload" /> Importer un relevé</button>
                <a className="btn quiet" href="#/connecteurs"><Icon name="bank" /> Connecter ma banque</a>
              </div>
            </div>
          )
        )}
        {list.data && list.data.total > 0 && (
          <>
            <div className="ledger-summary">
              <span><b>{plural(list.data.total, "opération", "opérations")}</b>{filtered ? " trouvées" : ""}</span>
              <span className="faint">sur les {items.length} affichées : <b className="amount in">+{money(sumIn, { whole: true })}</b> · <b>{money(sumOut, { whole: true })}</b></span>
            </div>
            <TransactionLedger items={items} categories={categories.data ?? []} onChanged={list.reload} />
            {items.length < list.data.total && (
              <div className="actions" style={{ justifyContent: "center" }}>
                <button className="btn quiet" onClick={() => setLimit((l) => Math.min(500, l + PAGE))} disabled={limit >= 500}>
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

  let lastMonth = "";
  return (
    <ul className="ledger">
      {days.map(([date, txns]) => {
        const month = date.slice(0, 7);
        const showMonth = month !== lastMonth;
        lastMonth = month;
        const dayTotal = txns.filter((t) => !t.is_transfer).reduce((a, t) => a + t.amount_cents, 0);
        return (
          <li className="ledger-day" key={date}>
            {showMonth && <h3 className="ledger-month">{monthTitle(month)}</h3>}
            <div className="ledger-date" aria-hidden="true">
              <strong>{dayNumber(date)}</strong>
              <span>{monthLabel(date.slice(0, 7))}</span>
            </div>
            <div className="ledger-rows">
              {txns.map((t) => (
                <div className="ledger-row txn" key={t.id}>
                  <div className="ledger-label">
                    <span className="sr-only">{shortDate(date)} : </span>
                    <b>{merchantCase(t.merchant || t.label)}</b>{" "}
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
              {txns.length > 1 && <div className="day-total">{money(dayTotal, { signed: true })} sur la journée</div>}
            </div>
          </li>
        );
      })}
    </ul>
  );
}

function ImportPanel({ accounts, onImported }: { accounts: Account[]; onImported: () => void }) {
  const [accountId, setAccountId] = useState<string>(accounts[0] ? String(accounts[0].id) : "");
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<ImportResult | null>(null);
  const [newName, setNewName] = useState("Compte courant");
  const [busy, error, run] = useAsyncAction();
  const toast = useToast();

  const createAccount = (e: FormEvent) => {
    e.preventDefault();
    void run(async () => {
      const a = await api<Account>("/api/accounts", { method: "POST", body: { name: newName.trim() || "Compte courant", kind: "checking", balance_cents: 0 } });
      setAccountId(String(a.id));
      toast(`Compte « ${a.name} » créé : tu peux importer son relevé.`);
      onImported();
    });
  };

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
      <form className="panel" onSubmit={createAccount} aria-label="Premier compte">
        <p className="muted">Un relevé appartient à un compte : donne-lui un nom, le solde se calera sur le relevé.</p>
        <div className="form-grid" style={{ marginTop: "0.75rem" }}>
          <Field label="Nom du compte"><input value={newName} onChange={(e) => setNewName(e.target.value)} required maxLength={120} /></Field>
        </div>
        {error && <p className="error" role="alert">{error}</p>}
        <div className="actions"><button className="btn" disabled={busy}>Créer le compte et continuer</button></div>
      </form>
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
          <div className="table-scroll"><table className="plain">
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
          </table></div>
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
