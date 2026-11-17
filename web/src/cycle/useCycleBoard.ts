import { useCallback } from "react";
import { post, useLive } from "../api";
import { BOARD_TABLES, CycleBoard, Decision } from "./types";

/**
 * The dashboard's board for one business, live. `decide` is the owner's one write path; whatever the outcome it reads
 * the board again, so a card that went stale (409 with a sentence) is replaced by the cycle's real state. `onDecided`
 * runs after a decision the server took.
 */
export function useCycleBoard(businessId: number | null, onDecided?: () => void) {
  const live = useLive<CycleBoard>(businessId === null ? null : `/businesses/${businessId}/cycle-board`, BOARD_TABLES);
  const { reload } = live;
  const decide = useCallback(async (cycleId: number, decision: Decision) => {
    try {
      await post(`/cycles/${cycleId}/decisions`, decision);
      // the owner's list carries "start the cycle" and stage-end items, which this decision just settled
      onDecided?.();
    } finally {
      reload();
    }
  }, [reload, onDecided]);
  return { ...live, decide };
}
