import { Route, Routes } from "react-router-dom";
import { NotFound } from "../components/kit";
import { ActivityLog } from "./audit/Activity";
import { Costs } from "./audit/Costs";
import { Queue } from "./audit/Queue";
import { RunDetail, Runs } from "./audit/Runs";

/** The record screens: activity, model calls, background work and costs (pages/audit/*). */
export { ActivityLog };

export default function Audit() {
  return (
    <Routes>
      <Route index element={<ActivityLog />} />
      <Route path="runs" element={<Runs />} />
      <Route path="runs/:id" element={<RunDetail />} />
      <Route path="costs" element={<Costs />} />
      <Route path="queue" element={<Queue />} />
      <Route path="*" element={<NotFound />} />
    </Routes>
  );
}
