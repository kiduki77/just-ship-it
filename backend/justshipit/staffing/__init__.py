"""The Staffing Agency Manager: places the people for each job, records why, and answers for the decision later
(docs/design/staffing-structure.md). It is a rule, not a model. Code outside this package imports only from here."""
from justshipit.staffing.config import checked_family, default_family, load_limits, task_families
from justshipit.staffing.crew import (
    HISTORY_EXPLORE,
    HISTORY_REASONS,
    RULES,
    RULES_VERSION,
    Duty,
    Flow,
    History,
    JobFacts,
    Limits,
    Placement,
    Reason,
    Rule,
    Shape,
    Slot,
    Stat,
    SubjectKind,
    Tally,
    fixed_path,
    place,
    verifier_role,
)
from justshipit.staffing.facts import task_facts
from justshipit.staffing.ledger import amend, author, ensure, record_turn, task_crew, verifier, verifier_of
from justshipit.staffing.outcome import AUDIT, Outcome, Spend
from justshipit.staffing.recall import Bundle, Record, RecordKind, recall_bundle
from justshipit.staffing.stats import placement_stats, recorded_history
from justshipit.staffing.solo import (
    AMENDMENT_REASONS,
    UNCHECKED,
    alone_task_ids,
    audited,
    enqueue_audit,
    settle_retry,
    settle_solo,
)
from justshipit.staffing.turns import (
    AcceptanceTurn,
    AuthorTurn,
    BuildReviewTurn,
    BuildTurn,
    Turn,
    VerifierTurn,
)
from justshipit.staffing.views import CrewView, Standing, crew_views

__all__ = ["AMENDMENT_REASONS", "AUDIT", "HISTORY_EXPLORE", "HISTORY_REASONS", "RULES", "RULES_VERSION", "UNCHECKED",
           "AcceptanceTurn", "AuthorTurn", "BuildReviewTurn", "BuildTurn", "Bundle", "CrewView", "Duty", "Flow", "History",
           "JobFacts", "Limits", "Outcome", "Placement", "Reason", "Record", "RecordKind", "Rule", "Shape", "Slot", "Spend",
           "Standing", "Stat", "SubjectKind", "Tally", "Turn", "VerifierTurn", "alone_task_ids", "amend", "audited",
           "author", "checked_family", "crew_views", "default_family", "enqueue_audit", "ensure", "fixed_path",
           "load_limits", "place", "placement_stats", "recall_bundle", "record_turn", "recorded_history",
           "settle_retry", "settle_solo", "task_crew", "task_facts", "task_families", "verifier",
           "verifier_of", "verifier_role"]
