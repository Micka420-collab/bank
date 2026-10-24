// Le journal d'activité parle français : chaque action enregistrée par le serveur a un libellé, et un identifiant
// inconnu reste lisible (domaine en clair, jamais un identifiant brut avec ses points).

import { describe, expect, it } from "vitest";
import { ACTIONS, labelAction } from "./audit-labels";

// Les actions que le serveur enregistre (backend : `audit.record(db, actor, "<action>", …)`), relevées à la main.
const BACKEND_ACTIONS = `
account.create account.update agent.token.create agent.token.denied agent.token.revoke aids.simulate assistant.ask
assistant.configure assistant.conversation_deleted assistant.denied assistant.failed assistant.feedback assistant.hints
assistant.read_page assistant.remove assistant.secret_refused assistant.test assistant.update assistant.web_search
assistant.web_search_failed autonomy.allowed autonomy.denied autonomy.simulated autonomy.stopped backup.failed
backup.verify_done backup.verify_failed bank.connect.done bank.connect.failed bank.connect.start bank.revoke bank.setup.check bank.setup.denied
bank.setup.refused bank.setup.removed bank.setup.saved bank.sync budget.settings
category.update channel.link_updated channel.linked channel.links_reset channel.pairing_cancelled channel.pairing_denied
channel.pairing_failed channel.pairing_started channel.preferences channel.removed channel.secret_refused
channel.test_sent channel.unlinked consent.grant correlation.decide crypto.endpoints crypto.endpoints.denied
crypto.exchange.add crypto.exchange.denied crypto.exchange.failed crypto.exchange.refused crypto.exchange.revoke
crypto.exchange.sync crypto.holding.delete crypto.holding.set crypto.sync crypto.tax.add crypto.tax.delete
crypto.wallet.add crypto.wallet.delete decision.purchase document.apply document.delete energy.connect
energy.disconnect energy.sync energy.sync_failed energy.sync_manual energy.tariff export.denied export.done
goal.create goal.delete home.dvf_refresh home.profile home.profile_removed import.file initiative.act loan.create loan.delete login.failed login.locked login.mfa_failed
login.passkey_failed login.passkey_unknown login.success logout mail.configure mail.dismiss mail.import mail.remove
mail.scan mcp.auth_failed mcp.denied mcp.refused memory.delete memory.edit memory.forget_all memory.forget_denied
memory.proposal_approved memory.proposal_rejected memory.review memory.settings mfa.enabled obligation.add
obligation.auto obligation.delete obligation.dismiss obligation.done obligation.enable obligation.undo
obligation.update onboarding.answer onboarding.clear opportunity.confirm opportunity.priorities opportunity.start
opportunity.time_value passkey.added passkey.denied passkey.removed password.change_denied password.changed
planning.connect planning.disconnect planning.model planning.sync_failed planning.sync_manual procedure.approve
procedure.close procedure.create procedure.delete procedure.fields procedure.pieces procedure.restore procedure.sent
procedure.update profile.update proposal.approve proposal.create proposal.reject push.subscribe push.test
rates.revert rates.update place.geocode place.remove papers.task_done papers.task_close
energy.contract energy.max_power energy.max_power_failed tempo.refresh tempo.history energy.load_curve weather.refresh
housing.dpe_refresh housing.dpe_choose housing.market_refresh housing.details housing.control_refresh
housing.taxes_refresh housing.risks_probe
push.unsubscribe retirement.profile retirement.profile_removed rule.create search.configure search.denied
search.remove search.test search.test_failed setup.denied setup.done transaction.update vault.applied vault.assist
vault.delete vault.export vault.export_denied vault.field_removed vault.field_set vault.file_read vault.read_failed
vault.refused_sensitive vault.reprocess vault.update vault.upload vault.verified watch.all_seen watch.source_add
watch.source_delete watch.source_update yearahead.custom.add yearahead.custom.remove yearahead.ignore
`.split(/\s+/).filter(Boolean);

describe("libellés du journal", () => {
  it("covers every action the server records", () => {
    const missing = BACKEND_ACTIONS.filter((a) => !ACTIONS[a]);
    expect(missing).toEqual([]);
    for (const a of BACKEND_ACTIONS) expect(labelAction(a)).not.toMatch(/\w\.\w/);
  });

  it("keeps an unknown action readable", () => {
    expect(labelAction("crypto.price_alert.fired")).toBe("Crypto : price alert fired");
    expect(labelAction("mcp.get_dossier")).toBe("Agent IA : get dossier");
    expect(labelAction("mystery")).toBe("Mystery");
    expect(labelAction("retirement.profile")).toBe("Relevé de carrière enregistré");
  });
});
