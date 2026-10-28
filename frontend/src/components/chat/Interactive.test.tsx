import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ChatBlock, InteractiveBlock } from "../../api";
import { ChatBlocks } from "./Blocks";
import { serializeWidgetAnswers } from "./Interactive";
import { ToastProvider } from "../ui";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const poll: InteractiveBlock = {
  type: "interactive", kind: "poll", title: "Tes priorités",
  description: "Par quoi veux-tu commencer ?", submit_label: "Continuer",
  fields: [{ id: "priority", kind: "single", label: "Ton choix", required: true, options: ["Épargner", "Réduire les frais", "Investir"] }],
};
const checklist: InteractiveBlock = {
  type: "interactive", kind: "checklist", title: "Mon dossier", description: "",
  submit_label: "Envoyer",
  fields: [{ id: "pieces", kind: "multiple", label: "Documents prêts", required: true, options: ["Bulletin de paie", "Bail", "Pièce d'identité"] }],
};

describe("widgets de conversation Bank", () => {
  let el: HTMLDivElement;
  let root: Root;
  const ask = vi.fn();

  beforeEach(() => {
    ask.mockReset();
    el = document.createElement("div");
    document.body.appendChild(el);
    root = createRoot(el);
  });
  afterEach(async () => {
    await act(async () => root.unmount());
    el.remove();
  });

  const render = (blocks: ChatBlock[]) => act(async () => root.render(
    <ToastProvider><ChatBlocks blocks={blocks} handlers={{ onAsk: ask, onNavigate: vi.fn() }} /></ToastProvider>,
  ));
  const click = (target: HTMLElement) => act(async () => { target.click(); });
  const submit = () => act(async () => { el.querySelector<HTMLFormElement>(".chat-interactive form")!.requestSubmit(); });

  it("validates required poll choice then sends a new user question once", async () => {
    await render([poll]);
    expect(el.querySelectorAll('input[type="radio"]')).toHaveLength(3);
    expect(el.querySelector(".chat-interactive-type")?.textContent).toBe("Sondage");
    await submit();
    expect(ask).not.toHaveBeenCalled();
    expect(el.querySelector('[role="alert"]')?.textContent).toContain("Ton choix");
    await click(el.querySelectorAll<HTMLInputElement>('input[type="radio"]')[1]!);
    await submit();
    expect(ask).toHaveBeenCalledTimes(1);
    expect(ask).toHaveBeenCalledWith("Mes réponses à « Tes priorités » :\n- Ton choix : Réduire les frais");
    expect(el.querySelector(".chat-interactive-sent")?.textContent).toContain("Réponses envoyées");
    expect(el.querySelector<HTMLInputElement>('input[type="radio"]')?.disabled).toBe(true);
  });

  it("allows multiple checked choices, shows progress and keeps the exact answer", async () => {
    await render([checklist]);
    expect(el.querySelector(".chat-check-progress")?.textContent).toBe("0 sur 3 cases cochées");
    const boxes = el.querySelectorAll<HTMLInputElement>('input[type="checkbox"]');
    await click(boxes[0]!);
    await click(boxes[2]!);
    expect(el.querySelector(".chat-check-progress")?.textContent).toBe("2 sur 3 cases cochées");
    await submit();
    expect(ask).toHaveBeenCalledWith(
      "Mes réponses à « Mon dossier » :\n- Documents prêts : Bulletin de paie, Pièce d'identité",
    );
  });

  it("renders editable text, number, checkbox, scale in a form and serializes them", async () => {
    const form: InteractiveBlock = {
      type: "interactive", kind: "form", title: "Ton objectif", description: "", submit_label: "Valider",
      fields: [
        { id: "name", label: "Nom du projet", required: false, kind: "text" },
        { id: "amount", label: "Objectif en euros", required: false, kind: "number" },
        { id: "consent", label: "Vérifié", required: false, kind: "boolean" },
        { id: "confidence", label: "Confiance", required: false, kind: "scale", min: 1, max: 5 },
      ],
    };
    expect(serializeWidgetAnswers(form, { name: "Voyage", amount: "400", consent: true, confidence: 4 })).toContain("- Confiance : 4");
    await render([form]);
    const inputs = el.querySelectorAll(".chat-interactive-field");
    expect(inputs).toHaveLength(4);
    const set = (input: HTMLInputElement | HTMLTextAreaElement, value: string) => act(async () => {
      const base = input.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      Object.getOwnPropertyDescriptor(base, "value")!.set!.call(input, value);
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await set(el.querySelector("textarea")!, "Voyage");
    await set(el.querySelector('input[type="number"]')!, "400");
    await click(el.querySelector<HTMLInputElement>('input[type="checkbox"]')!);
    await act(async () => {
      const input = el.querySelector<HTMLInputElement>('input[type="range"]')!;
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, "4");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await submit();
    expect(ask).toHaveBeenCalledTimes(1);
    expect(ask.mock.calls[0]![0]).toContain("- Nom du projet : Voyage");
    expect(ask.mock.calls[0]![0]).toContain("- Objectif en euros : 400");
    expect(ask.mock.calls[0]![0]).toContain("- Vérifié : Oui");
  });

  it("can annotate a specific diagram node with a textual follow-up", async () => {
    await render([{
      type: "diagram", title: "Dossier APL", kind: "steps",
      nodes: [{ id: "data", label: "Mes revenus" }, { id: "send", label: "Envoi" }],
      edges: [{ from: "data", to: "send" }],
    }]);
    const annotation = el.querySelector<HTMLDetailsElement>(".chat-annotate")!;
    expect(annotation).toBeTruthy();
    await click(annotation.querySelector("summary")!);
    const select = annotation.querySelector<HTMLSelectElement>("select")!;
    await act(async () => { select.value = "send"; select.dispatchEvent(new Event("change", { bubbles: true })); });
    const textarea = annotation.querySelector<HTMLTextAreaElement>("textarea")!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(textarea, "Je dois relire avant.");
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => { annotation.querySelector("form")!.requestSubmit(); });
    expect(ask).toHaveBeenCalledWith("Concernant le schéma « Dossier APL », j'annote l'étape « Envoi » : Je dois relire avant.");
  });
});
