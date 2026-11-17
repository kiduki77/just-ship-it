import { refreshLive, Row, useLive } from "../api";
import { useBusinessSelection, useFollowBusiness } from "../business";
import { panelTarget, Today, TODAY_TABLES } from "../today";
import { Boundary } from "./boundary";
import { DecisionPanel, NextItem } from "./decision";
import { usePanel } from "./panel";
import { POST_KINDS, TaskPanel } from "./task";

/** Mounted once per item, so an item opened again after the panel closed follows its business again. */
function FollowItem({ business, item, title }: { business: number | null | undefined; item: string; title: string }) {
  useFollowBusiness(business, item, title);
  return null;
}

/**
 * The panel named by the address (`?req=` or `?task=`), on whichever page the owner is. An item opened by its address
 * shows under its own business (the address may come from another one). When an item is done, the next thing the owner
 * can open in place is the first one left on its business's list, which is the list the home shows unless the owner
 * went back to another business while it stayed open.
 */
export function PanelHost() {
  const { current, open, close } = usePanel();
  const { current: selected, today } = useBusinessSelection();
  const path = current && (current.key === "req" ? `/requests/${current.id}` : `/owner-tasks/${current.id}`);
  const { data } = useLive<Row>(path, []);
  // the answer for the item before this one stays until this one's arrives
  const shown = data && current && data.id === current.id ? data : null;
  const business: number | null | undefined = shown ? (shown.business_id ?? null) : undefined;
  const elsewhere = business != null && business !== selected?.id;
  const { data: theirs } = useLive<Today>(elsewhere ? `/businesses/${business}/today?all=true` : null, TODAY_TABLES);
  if (!current) return null;
  const ref = `${current.key === "req" ? "REQ" : "OT"}-${current.id}`;
  const list = elsewhere ? theirs : today;
  const item = list?.items.find((it) => it.ref !== ref && panelTarget(it));
  const target = item && panelTarget(item);
  const changed = () => refreshLive(TODAY_TABLES);
  const next: NextItem | null = item && target
    ? { title: item.title, kind: target.key === "req" ? "decision" : "todo", post: POST_KINDS.includes(item.kind), open: () => open(target, true) }
    : null;
  return (
    <Boundary reset={ref} panel={{ onClose: close }}>
      <FollowItem key={ref} business={business} item={ref} title={shown?.title ?? ""} />
      {current.key === "req"
        ? <DecisionPanel id={current.id} next={next} onClose={close} onChanged={changed} />
        : <TaskPanel id={current.id} next={next} onClose={close} onChanged={changed} />}
    </Boundary>
  );
}
