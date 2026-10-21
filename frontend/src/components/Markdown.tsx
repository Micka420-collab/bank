// Rendu du sous-ensemble de Markdown produit par Bank (titres, listes, gras), en éléments React :
// aucun HTML injecté, donc un libellé d'opération piégé reste du texte inerte.

import type { ReactNode } from "react";

export type Block =
  | { type: "h1"; text: string }
  | { type: "h2"; text: string }
  | { type: "p"; text: string }
  | { type: "ul"; items: { text: string; children: string[] }[] };

export function parseMarkdown(src: string): Block[] {
  const blocks: Block[] = [];
  let list: Extract<Block, { type: "ul" }> | null = null;
  for (const raw of src.split("\n")) {
    const line = raw.replace(/\s+$/, "");
    const nested = /^ {2,}- (.*)$/.exec(line);
    const item = /^- (.*)$/.exec(line);
    if (nested && list && list.items.length > 0) {
      list.items[list.items.length - 1]!.children.push(nested[1]!);
      continue;
    }
    if (item) {
      if (!list) {
        list = { type: "ul", items: [] };
        blocks.push(list);
      }
      list.items.push({ text: item[1]!, children: [] });
      continue;
    }
    list = null;
    if (line === "") continue;
    if (line.startsWith("## ")) blocks.push({ type: "h2", text: line.slice(3) });
    else if (line.startsWith("# ")) blocks.push({ type: "h1", text: line.slice(2) });
    else blocks.push({ type: "p", text: line });
  }
  return blocks;
}

export function inline(text: string): ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, i) =>
    part.startsWith("**") && part.endsWith("**") && part.length > 4 ? <strong key={i}>{part.slice(2, -2)}</strong> : part,
  );
}

export function Markdown({ source }: { source: string }) {
  return (
    <div className="md">
      {parseMarkdown(source).map((b, i) => {
        if (b.type === "h1") return <h3 key={i}>{inline(b.text)}</h3>;
        if (b.type === "h2") return <h4 key={i}>{inline(b.text)}</h4>;
        if (b.type === "p") return <p key={i}>{inline(b.text)}</p>;
        return (
          <ul key={i}>
            {b.items.map((it, j) => (
              <li key={j}>
                {inline(it.text)}
                {it.children.length > 0 && (
                  <ul>
                    {it.children.map((c, k) => <li key={k}>{inline(c)}</li>)}
                  </ul>
                )}
              </li>
            ))}
          </ul>
        );
      })}
    </div>
  );
}
