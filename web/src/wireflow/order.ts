import type { Plan, Row } from "./types";

/** The frames one step of a flow draws, from the plan the card draws them from: its screen, the states branching off
 * it, and on the last step the frame of the empty case. Null when the plan lacks the step's screen, which then draws
 * nothing. */
export function stepFrames(plan: Plan, row: Row, si: number): { screen: string; branches: string[]; empty: string | null } | null {
  const step = row.steps[si];
  if (!plan.screens[step.screen]) return null;
  const emptyScreen = si === row.steps.length - 1 ? row.empty?.screen : null;
  return {
    screen: step.screen,
    branches: step.branches.filter((id) => plan.screens[id]),
    empty: emptyScreen && plan.screens[emptyScreen] ? emptyScreen : null,
  };
}

/** The screens no flow walks, which the card draws after the flows. */
export const looseFrames = (plan: Plan): string[] => plan.loose.filter((id) => plan.screens[id]);

/** Every screen once, in the order its frame appears on the card. The numbers on the owner's notes follow it. */
export function displayOrder(plan: Plan): string[] {
  const walked = plan.rows.flatMap((row) => row.steps.flatMap((_, si) => {
    const f = stepFrames(plan, row, si);
    return f ? [f.screen, ...f.branches, ...(f.empty ? [f.empty] : [])] : [];
  }));
  return [...new Set([...walked, ...looseFrames(plan)])];
}
