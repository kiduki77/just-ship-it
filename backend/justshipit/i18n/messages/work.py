"""Projects and tasks: the owner's controls (`services/control.py`, `services/projects.py`), the work the server
declines to plan (`services/tasks.py`, `services/rounds.py`, `services/plan_screen.py`), what a realign changes
(`orchestration/realign.py`), and the documents and cards a run writes (`orchestration/company_graph.py`,
`analysis/runner.py`). `{ref}` is an id the owner sees, as in P-3 or T-12."""
from justshipit.i18n import Entry

MESSAGES: dict[str, Entry] = {
    # ---------------------------------------------------------------- refusals the console shows
    "work.refuse.no_project": {"en": "There is no project {ref}.", "ko": "{ref} 프로젝트가 없어요."},
    "work.refuse.no_task": {"en": "There is no task {ref}.", "ko": "{ref} 업무가 없어요."},
    "work.refuse.cannot_pause": {"en": "{ref} is {status}, so it can't be paused.",
                                 "ko": "{ref|은} {status} 상태라 멈출 수 없어요."},
    "work.refuse.not_paused": {"en": "{ref} isn't paused.", "ko": "{ref|은} 멈춰 있지 않아요."},
    "work.refuse.cannot_retry_project": {
        "en": "{ref} is {status}. Only a run that failed or stopped can start again.",
        "ko": "{ref|은} {status} 상태예요. 실패했거나 멈춘 실행만 다시 시작할 수 있어요."},
    "work.refuse.run_busy": {"en": "{ref}'s run: {why}", "ko": "{ref} 실행: {why}"},
    "work.refuse.budget_too_low": {"en": "The new budget has to be more than the ${spent} already spent.",
                                   "ko": "새 예산은 이미 쓴 ${spent}보다 커야 해요."},
    "work.refuse.month_limit_too_low": {"en": "The monthly budget has to be more than the ${spent} already spent this "
                                              "month.",
                                        "ko": "월 한도는 이번 달에 이미 쓴 ${spent}보다 커야 해요."},
    "work.refuse.empty_directive": {"en": "Write the instruction first.", "ko": "지시할 내용을 적어 주세요."},
    "work.refuse.archived_project": {"en": "{ref} is archived.", "ko": "{ref|은} 보관한 프로젝트예요."},
    "work.refuse.not_archived_project": {"en": "{ref} isn't archived.", "ko": "{ref|은} 보관한 프로젝트가 아니에요."},
    "work.refuse.no_directive": {"en": "{ref} has no instruction #{n}.", "ko": "{ref}에는 지시 #{n|이} 없어요."},
    "work.refuse.rule_changed": {"en": "The rules on instruction #{n} have changed. Reload the page and check again.",
                                 "ko": "지시 #{n}의 규칙이 바뀌었어요. 화면을 새로 고친 뒤 다시 확인해 주세요."},
    "work.refuse.empty_name": {"en": "Give it a name.", "ko": "이름을 적어 주세요."},
    "work.refuse.closed_to_work": {
        "en": "{ref} has moved on to its final decision, so it takes no new tasks. Give the work to a new project.",
        "ko": "{ref|은} 최종 결정 단계라 새 업무를 받지 않아요. 새 프로젝트로 맡겨 주세요."},
    "work.refuse.retry_only_failed": {"en": "{ref} is {status}. Only a failed task can be retried.",
                                      "ko": "{ref|은} {status} 상태예요. 실패한 업무만 다시 시도할 수 있어요."},
    "work.refuse.cancel_only_unstarted": {"en": "{ref} is {status}. Only a task that hasn't started can be cancelled.",
                                          "ko": "{ref|은} {status} 상태예요. 시작하기 전인 업무만 취소할 수 있어요."},
    "work.refuse.edit_only_unstarted": {"en": "{ref} has already started. Only a task that hasn't started can be edited.",
                                        "ko": "{ref|은} 이미 시작했어요. 시작하기 전인 업무만 고칠 수 있어요."},
    "work.refuse.bad_owner": {"en": "Tasks can't be given to '{owner}'.", "ko": "'{owner}'에게는 업무를 맡길 수 없어요."},
    "work.refuse.empty_comment": {"en": "Write a comment first.", "ko": "코멘트를 적어 주세요."},
    "work.refuse.need_title_goal": {"en": "Write a title and a goal.", "ko": "제목과 목표를 적어 주세요."},
    "work.refuse.archived_business": {"en": "{ref} is archived.", "ko": "{ref|은} 보관한 사업이에요."},
    "work.refuse.no_plan": {"en": "{ref}: approve the plan first, then start.", "ko": "{ref}: 계획을 먼저 승인해 주세요."},
    "work.refuse.plan_gaps": {
        "en": "{ref}: the approved plan leaves {sections} open, so it can't start yet. Each needs a line you said or "
              "confirmed.",
        "ko": "{ref}: 승인한 계획에 {sections} 칸이 비어 있어서 아직 시작할 수 없어요. 칸마다 오너님이 직접 말했거나 "
              "맞다고 한 줄이 있어야 해요."},
    "work.refuse.archive_busy": {
        "en": "{ref} has a job queued or running, so it can't be archived yet. Archive it once the job ends.",
        "ko": "{ref}: 대기 중이거나 실행 중인 작업이 있어서 아직 보관할 수 없어요. 작업이 끝나면 보관해 주세요."},
    "work.archived_note": {"en": "Project archived", "ko": "프로젝트 보관"},

    # ---------------------------------------------------------------- activity lines for the owner's own actions
    "work.activity.paused": {"en": "{ref} paused", "ko": "{ref} 멈춤"},
    "work.activity.resumed": {"en": "{ref} resumed", "ko": "{ref} 재개"},
    "work.activity.retried": {"en": "{ref} run again from where it stopped", "ko": "{ref} 멈춘 곳부터 다시 실행"},
    "work.activity.budget": {"en": "{ref} budget ${old} → ${new}", "ko": "{ref} 예산 ${old} → ${new}"},
    "work.activity.directive": {"en": "{ref} instruction #{n}: {text}", "ko": "{ref} 지시 #{n}: {text}"},
    "work.activity.rule_removed": {"en": "{ref}: you took the rule '{label}' off instruction #{n}",
                                   "ko": "{ref} 지시 #{n}에서 '{label}' 규칙을 뺐어요"},
    "work.activity.renamed": {"en": "{ref} renamed: {old} → {new}", "ko": "{ref} 이름 변경: {old} → {new}"},
    "work.activity.task_edited": {"en": "{ref} {title}: you edited {fields}", "ko": "{ref} {title}: 직접 고침({fields})"},
    "work.activity.task_comment": {"en": "{ref} {title}: your comment", "ko": "{ref} {title}: 내 코멘트"},
    "work.activity.month_limit": {"en": "{month} monthly budget ${old} → ${new}",
                                  "ko": "{month} 월 한도 ${old} → ${new}"},

    # ---------------------------------------------------------------- what a budget raise does to a stopped run
    "work.restart.not_scheduled": {"en": "No restart was scheduled. {why}.", "ko": "다시 실행은 예약하지 않았어요. {why}."},
    "work.restart.carries_on": {"en": "The run carries on with the new budget. {why}.",
                                "ko": "새 예산으로 이어서 실행해요. {why}."},
    "work.restart.after_hold": {
        "en": "The previous background process may still be finishing a call, so the run restarts from where it "
              "stopped at {time}.",
        "ko": "이전 백그라운드 프로세스가 하던 호출을 아직 끝내고 있을 수 있어서 {time}에 멈춘 곳부터 다시 "
              "실행해요."},
    "work.restart.scheduled": {"en": "The run restarts from where it stopped.", "ko": "멈춘 곳부터 다시 실행할게요."},

    # ---------------------------------------------------------------- why a task stopped or never started
    "work.task.cancelled_by_owner": {"en": "You cancelled it", "ko": "직접 취소했어요"},
    "work.task.cancelled_by_owner_why": {"en": "You cancelled it: {reason}", "ko": "직접 취소했어요: {reason}"},
    "work.task.cancelled_by_ceo": {"en": "The Project Manager cancelled it to follow your instruction",
                                   "ko": "오너님의 지시에 맞춰 프로젝트 매니저가 취소했어요"},
    "work.task.cancelled_by_rule": {"en": "Cancelled by your instruction {ref} ({label})",
                                    "ko": "지시 {ref}에 따라 취소했어요({label})"},
    "work.task.cancelled_by_expiry": {"en": "Cancelled: it waited for {ref}, which closed with no answer by its deadline",
                                      "ko": "{ref} 결정을 기다리던 업무예요. 기한까지 답이 없어 결정이 닫혀서 취소했어요"},
    "work.run.internal_error": {"en": "An internal error occurred.", "ko": "내부 오류가 났어요."},
    "work.task.unarchived": {
        "en": "Unarchived. This run was in progress when it was archived; run it again to pick up where it left off.",
        "ko": "보관을 해제했어요. 보관 전에 진행 중이던 실행이라, 다시 실행하면 멈춘 곳부터 이어서 해요."},
    "work.task.default_criterion": {"en": "Answers the question in the goal, with evidence",
                                    "ko": "목표의 질문에 근거를 들어 답하기"},
    "work.task.review_failed": {"en": "Didn't pass review: {unmet}", "ko": "검토를 통과하지 못했어요: {unmet}"},
    "work.task.alone_unmet": {"en": "Met none of its criteria, by its author's own account: {unmet}",
                              "ko": "작성자가 스스로 밝히기로, 완료 기준을 하나도 채우지 못했어요: {unmet}"},
    "work.task.review_again": {"en": "the reviewer asked for another pass", "ko": "검토자가 다시 하라고 했어요"},
    "work.task.not_reviewed": {"en": "(no independent review)", "ko": "(독립 검토를 하지 못함)"},
    "work.task.round_spent": {
        "en": "Not started: this round's analysis budget (about ${limit}) is spent. The Project Manager plans it again next "
              "round if it's still needed.",
        "ko": "이번 회차의 분석 예산(약 ${limit})을 다 써서 시작하지 않았어요. 필요하면 프로젝트 매니저가 다음 회차에 다시 계획해요."},
    "work.task.not_planned": {"en": "Didn't create '{title}'. {why}", "ko": "'{title}' 업무는 만들지 않았어요. {why}"},
    "work.task.round_cap": {"en": {"one": "A round takes at most {n} task.", "other": "A round takes at most {n} tasks."},
                            "ko": "한 회차에 업무는 {n}개까지예요."},
    "work.task.ruled_out": {"en": "Your instruction {ref} ruled this out ({label})",
                            "ko": "지시 {ref}에서 하지 말라고 한 일이에요({label})"},
    "work.task.redone_too_often": {"en": "Already done {n} times ({refs}). Finished work is redone once at most.",
                                   "ko": "이미 {n}번 끝낸 일이에요({refs}). 다시 하는 건 한 번까지예요."},
    "work.task.redo_needs_evidence": {"en": "Already done ({refs}). Redoing it needs new evidence.",
                                      "ko": "이미 끝낸 일이에요({refs}). 다시 하려면 새 근거가 있어야 해요."},

    # ---------------------------------------------------------------- instruction rules the server refuses
    "work.rule.no_words": {"en": "it names no words", "ko": "낱말이 없어요"},
    "work.rule.one_letter": {"en": "one-letter words ({words}) appear in almost every title",
                             "ko": "한 글자 낱말({words})은 거의 모든 제목에 들어가요"},
    "work.rule.short_latin": {"en": "words under three letters ({words}) are too short to name one piece of work",
                              "ko": "세 글자가 안 되는 영문 낱말({words})로는 일 하나를 가리킬 수 없어요"},
    "work.rule.too_broad": {"en": "it matches {hit} of the business's {n} tasks and waiting decisions, which is too broad",
                            "ko": "사업의 업무와 기다리는 결정 {n}개 중 {hit}개의 제목에 맞아서 너무 넓어요"},
    "work.rule.refused": {"en": "Instruction #{n}: didn't apply the rule '{label}': {why}",
                          "ko": "지시 #{n}의 '{label}' 규칙은 적용하지 않았어요: {why}"},
    "work.rule.withdrawn": {"en": "Your instruction {ref} ({label})", "ko": "내 지시 {ref}({label})"},

    # ---------------------------------------------------------------- the work plan document
    "work.doc.plan.title": {"en": "Work plan", "ko": "실행 계획"},
    "work.doc.plan.heading": {"en": "Work plan: {name}", "ko": "실행 계획: {name}"},
    "work.doc.plan.goal": {"en": "How the Project Manager reads the goal", "ko": "프로젝트 매니저가 이해한 목표"},
    "work.doc.plan.deliverables": {"en": "Deliverables", "ko": "결과물"},
    "work.doc.plan.success": {"en": "Success criteria", "ko": "성공 기준"},
    "work.doc.plan.unknowns": {"en": "Biggest unknowns", "ko": "가장 불확실한 것"},
    "work.doc.plan.first_talks": {"en": "Talk to these people this week", "ko": "이번 주에 이야기할 사람"},
    "work.doc.plan.stop_rule": {"en": "Keep going or stop: {rule}", "ko": "계속할지 멈출지: {rule}"},
    "work.doc.plan.tasks": {"en": "Tasks and their order", "ko": "업무와 순서"},

    # ---------------------------------------------------------------- a task's result document
    "work.doc.task.meta": {"en": "By {owner} · {status}", "ko": "{owner} 담당 · {status}"},
    "work.doc.task.reworked": {"en": {"one": "Redone once after review", "other": "Redone {n} times after review"},
                               "ko": "검토를 받고 {n}번 다시 했어요"},
    "work.doc.task.review": {"en": "Reviewed by {reviewer}: {verdict}", "ko": "{reviewer} 검토: {verdict}"},
    "work.doc.task.contested": {"en": "The reviewers disagree about the conclusion.",
                                "ko": "결론을 두고 검토 의견이 갈렸어요."},
    "work.doc.task.status.DONE": {"en": "Done", "ko": "완료"},
    "work.doc.task.status.FAILED": {"en": "Didn't pass review", "ko": "검토를 통과하지 못했어요"},
    "work.doc.task.status.APPROVAL_REQUIRED": {"en": "Waiting for your approval", "ko": "승인을 기다려요"},
    "work.doc.task.verdict.pass": {"en": "passed", "ko": "통과"},
    "work.doc.task.verdict.partial": {"en": "partly met", "ko": "일부만 채웠어요"},
    "work.doc.task.verdict.unmet": {"en": "not met", "ko": "기준을 채우지 못했어요"},
    "work.doc.task.verdict.rework": {"en": "needs another pass", "ko": "다시 해야 해요"},
    "work.doc.task.verdict.escalate": {"en": "needs your call", "ko": "오너님 판단이 필요해요"},
    "work.doc.task.verdict.unreviewed": {"en": "couldn't be reviewed", "ko": "검토하지 못했어요"},
    "work.doc.task.verdict.unchecked": {"en": "Not reviewed: one role did this task alone, and no second role checked it.",
                                        "ko": "검토 없음: 한 역할이 혼자 한 업무라서, 다른 역할이 확인하지 않았어요."},
    "work.doc.task.summary": {"en": "Summary", "ko": "요약"},
    "work.doc.task.evidence": {"en": "Evidence", "ko": "근거"},
    "work.doc.task.assumptions": {"en": "Assumptions", "ko": "가정"},
    "work.doc.task.risks": {"en": "Risks", "ko": "리스크"},
    "work.doc.task.open_questions": {"en": "Open questions", "ko": "아직 답하지 못한 질문"},
    "work.doc.task.independent_review": {"en": "Independent review", "ko": "독립 검토"},
    "work.doc.task.author_unmet": {"en": "What its author says is unmet", "ko": "맡은 역할이 못 채웠다고 한 기준"},
    "work.doc.task.unmet": {"en": "Not met: {x}", "ko": "못 채운 기준: {x}"},
    "work.doc.task.note": {"en": "Still open, not blocking: {x}", "ko": "남은 점(막지는 않음): {x}"},
    "work.doc.task.counter": {"en": "Counterpoint: {x}", "ko": "반론: {x}"},
    "work.doc.finance": {"en": "Finance engine results (computed, not estimated)", "ko": "재무 엔진 계산 결과(서버가 계산)"},
    "work.doc.finance_basis": {"en": "Calculated from: {refs}", "ko": "계산 근거: {refs}"},

    # ---------------------------------------------------------------- a meeting
    "work.meeting.contested": {
        "en": "Independent review left these tasks contested. Decide whether to keep each conclusion, change it, or "
              "hold off judging it.",
        "ko": "독립 검토에서 의견이 갈린 업무예요. 업무마다 결론을 그대로 둘지, 고칠지, 판단을 미룰지 정해 주세요."},
    "work.meeting.contested_task": {"en": "{ref} {title} (owner: {owner})", "ko": "{ref} {title} (담당 {owner})"},
    "work.meeting.counter": {"en": "Counterpoint: {x}", "ko": "반론: {x}"},
    "work.meeting.absent": {"en": "(absent: {why})", "ko": "(불참: {why})"},
    "work.doc.minutes.title": {"en": "Meeting notes: {agenda}", "ko": "회의록: {agenda}"},
    "work.doc.minutes.heading": {"en": "Meeting notes M-{id}: {agenda}", "ko": "회의록 M-{id}: {agenda}"},
    "work.doc.minutes.attendees": {"en": "Attended: {names}", "ko": "참석: {names}"},
    "work.doc.minutes.evidence": {"en": "Evidence: {refs}", "ko": "근거: {refs}"},

    # ---------------------------------------------------------------- a decision record
    "work.doc.decision.heading": {"en": "Decision DEC-{id}: {title}", "ko": "결정 DEC-{id}: {title}"},
    "work.doc.decision.verdict": {"en": "Decision: **{decision}**", "ko": "결정: **{decision}**"},
    "work.decision.kind.adopt": {"en": "Go ahead", "ko": "진행"},
    "work.decision.kind.defer": {"en": "Hold", "ko": "보류"},
    "work.decision.kind.reject": {"en": "Stop", "ko": "중단"},
    "work.decision.kind.conditional": {"en": "Go ahead, with conditions", "ko": "조건부 진행"},
    "work.doc.decision.yes": {"en": "yes", "ko": "예"},
    "work.doc.decision.no": {"en": "no", "ko": "아니요"},
    "work.doc.decision.rationale": {"en": "Why: {x}", "ko": "이유: {x}"},
    "work.doc.decision.for": {"en": "Evidence for: {refs}", "ko": "뒷받침하는 근거: {refs}"},
    "work.doc.decision.against": {"en": "Evidence against: {refs} — {dissent}", "ko": "반대 근거: {refs} — {dissent}"},
    "work.doc.decision.open": {"en": "Still open: {x}", "ko": "아직 답하지 못한 것: {x}"},
    "work.doc.decision.follow_up": {"en": "Follow-up tasks: {refs}", "ko": "후속 업무: {refs}"},
    "work.doc.decision.needs_you": {"en": "Needs your approval: {yes}", "ko": "오너 승인 필요: {yes}"},

    # ---------------------------------------------------------------- the final report and its evidence list
    "work.doc.evidence.title": {"en": "Evidence list", "ko": "근거 목록"},
    "work.doc.evidence.heading": {"en": "Evidence list: {name}", "ko": "근거 목록: {name}"},
    "work.doc.evidence.columns": {"en": "| ID | Kind | Server check | By | Claim | Source |",
                                  "ko": "| ID | 종류 | 서버 확인 | 작성 | 주장 | 출처 |"},
    "work.doc.report.recommendation": {"en": "Recommendation", "ko": "추천"},
    "work.doc.report.summary": {"en": "Summary", "ko": "요약"},
    "work.doc.report.grounding": {"en": "How well it's backed: {mix}", "ko": "근거 수준: {mix}"},
    "work.doc.report.assumed": {
        "en": {"one": " {assumed} of {n} conclusion rests on assumptions or inference.",
               "other": " {assumed} of {n} conclusions rest on assumptions or inference."},
        "ko": " 결론 {n}개 중 {assumed}개는 가정·추론에 기대요."},
    "work.doc.report.conclusions": {"en": "Conclusions and what backs them", "ko": "결론과 근거"},
    "work.doc.report.firm": {"en": "checked", "ko": "확인된 근거"},
    "work.doc.report.sourced": {"en": "sourced, not yet checked", "ko": "출처만 있고 확인 전"},
    "work.doc.report.assumed_tag": {"en": "rests on assumptions", "ko": "가정·추론에 기댐"},
    "work.doc.report.validation": {"en": "What to check with customers", "ko": "고객에게 확인할 것"},
    "work.doc.report.evidence": {"en": "Evidence", "ko": "근거"},
    "work.doc.report.evidence_where": {
        "en": {"one": "The source and check for the {n} piece of evidence are in '{title}' in the document list.",
               "other": "The sources and checks for all {n} pieces of evidence are in '{title}' in the document list."},
        "ko": "근거 {n}건의 출처와 확인 상태는 문서 목록의 '{title}'에 따로 모아 뒀어요."},

    # ---------------------------------------------------------------- the decisions a run puts to the owner
    "work.ask.build.title": {"en": "Build: {title}", "ko": "개발 시작: {title}"},
    "work.ask.build.summary": {
        "en": "This is the first thing you chose to ship, so the product agent writes its spec now rather than waiting "
              "for the research to finish. Building starts once you approve the spec.\nProblem to solve: {problem}",
        "ko": "오너님이 정한 첫 결과물이라 조사가 끝나기를 기다리지 않고 제품기획 에이전트가 명세를 써요. 명세를 승인하면 개발을 "
              "시작해요.\n해결해야 할 문제: {problem}"},
    "work.ask.build.inputs": {"en": "Task results for the spec: {refs}", "ko": "명세에 쓸 업무 결과: {refs}"},
    "work.ask.proposal.title": {"en": "Build proposal: {title}", "ko": "개발 제안: {title}"},
    "work.ask.proposal.title_more": {"en": "Build proposal: {title} and {n} more", "ko": "개발 제안: {title} 외 {n}건"},
    "work.ask.proposal.why": {"en": {"one": "{n} build need in the final report ({path})",
                                     "other": "{n} build needs in the final report ({path})"},
                              "ko": "최종 보고서({path})에 적힌 개발할 일 {n}건"},
    # An option's words say the decision it is (`services/verdicts.py`): each of these must read as approve, defer
    # and decline in its own language.
    "work.option.write_spec": {"en": "Approve and write the spec", "ko": "명세 쓰기 시작"},
    "work.option.approve": {"en": "Approve", "ko": "승인"},
    "work.option.hold": {"en": "Hold", "ko": "보류"},
    "work.option.decline": {"en": "Decline", "ko": "반려"},

    # ---------------------------------------------------------------- the data analysis sandbox
    "work.analysis.from_ledger": {
        "en": "Calculated from the project's own record inputs/{file} (assumptions included), not from source data; {rep}",
        "ko": "프로젝트 내부 기록 inputs/{file}(가정 포함)에서 계산한 값이라 원자료 근거가 아니에요. {rep}"},
    "work.analysis.reproduced": {"en": "reproduced on a rerun", "ko": "다시 실행해서 같은 값이 나왔어요"},
    "work.analysis.not_reproduced": {"en": "not confirmed on a rerun", "ko": "다시 실행해서 확인하지 못했어요"},
    "work.analysis.computed_reproduced": {
        "en": "Calculated from inputs/{file} (sha256 {sha}) and reproduced on a rerun",
        "ko": "inputs/{file}(sha256 {sha})에서 계산했고, 다시 실행해서 같은 값이 나왔어요"},
    "work.analysis.computed": {"en": "Calculated from inputs/{file}, but a rerun gave a different result",
                               "ko": "inputs/{file}에서 계산했지만 다시 실행한 결과가 달라서 확인하지 못했어요"},
    "work.analysis.owner_file": {"en": "A file you provided; the value isn't in results.json",
                                 "ko": "오너님이 올린 파일인데 이 값이 results.json에 없어요"},
    "work.analysis.transcribed": {
        "en": "Calculated from numbers the model typed into its script, not from a file the server fetched. Check the "
              "inputs against their source.",
        "ko": "서버가 받은 원자료가 아니라 모델이 스크립트에 옮겨 적은 수치로 계산한 값이에요. 입력 수치를 출처와 맞춰 봐야 해요."},
    "work.analysis.appendix": {"en": "Data analysis record (run by the server)", "ko": "데이터 분석 기록(서버가 실행)"},
    "work.analysis.steps": {
        "en": {"one": "{n} run · rerun {repro} · script and results: {where}",
               "other": "{n} runs · rerun {repro} · script and results: {where}"},
        "ko": "실행 {n}번 · 다시 실행 {repro} · 스크립트와 결과: {where}"},
    "work.analysis.repro_ok": {"en": "matched ✔", "ko": "일치 ✔"},
    "work.analysis.repro_no": {"en": "not confirmed ✘", "ko": "확인 못 함 ✘"},
    "work.analysis.input": {"en": "Input", "ko": "입력"},
    "work.analysis.no_inputs": {
        "en": "⚠ The server fetched no input file: the numbers used were typed into the script by the model (source "
              "data not checked).",
        "ko": "⚠ 서버가 받은 입력 파일이 없어요. 계산에 쓴 수치는 모델이 스크립트에 직접 적은 거예요(원자료 확인 안 됨)."},
    "work.analysis.final_script": {"en": "Final script", "ko": "최종 스크립트"},
}
