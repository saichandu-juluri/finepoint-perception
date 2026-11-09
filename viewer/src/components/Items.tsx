// The items of a step's check.json: what was checked, what came out, and what to do. Worst first.
import { STATUS_WORD } from "@/lib/job";
import type { Item } from "@/server/camrig";

const ORDER = ["fail", "flag", "not_tested", "pass"];

export function ItemList({ items, sort = true }: { items: Item[]; sort?: boolean }) {
  const list = sort ? [...items].sort((a, b) => ORDER.indexOf(a.status) - ORDER.indexOf(b.status)) : items;
  return (
    <ul className="items">
      {list.map((i, k) => (
        <li key={k} data-item-status={i.status}>
          <span className={`stp st-${i.status}`}>{STATUS_WORD[i.status] ?? i.status}</span>
          <span><b>{i.name}</b>{i.message ? `: ${i.message}` : ""}</span>
          {i.fix && i.status !== "pass" && <span className="it-fix"><b>What to do:</b> {i.fix}</span>}
        </li>
      ))}
    </ul>
  );
}
