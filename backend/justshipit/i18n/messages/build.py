"""Development, for the owner: where each build stands (`engineering/status.py`), the lines a build's steps leave
(`engineering/worker.py`), why it stopped, the cards it puts in Decisions (`engineering/specs.py`, `worker.py`), what a
spec did with the owner's asks (`engineering/owner_trace.py`), and the sandbox and runtime errors behind a stop."""
from justshipit.i18n import Entry

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_UPDATE_GIT = {"en": "Update git to 2.45.1 or later, or apply your distribution's security update, then try again.",
               "ko": "git을 2.45.1 이상으로 올리거나 배포판 보안 업데이트를 적용한 뒤 다시 시도해 주세요."}

# What the owner may have in progress in a checkout, and how to finish it (worker._refuse_busy).
_BUSY = (
    ("merge", "a merge", "Finish it with `git commit` or cancel it with `git merge --abort`",
     "머지가", "`git commit`으로 끝내거나 `git merge --abort`로 취소한 뒤"),
    ("cherry_pick", "a cherry-pick",
     "Finish it with `git cherry-pick --continue` or cancel it with `git cherry-pick --abort`",
     "cherry-pick이", "`git cherry-pick --continue`로 끝내거나 `git cherry-pick --abort`로 취소한 뒤"),
    ("revert", "a revert", "Finish it with `git revert --continue` or cancel it with `git revert --abort`",
     "revert가", "`git revert --continue`로 끝내거나 `git revert --abort`로 취소한 뒤"),
    ("rebase", "a rebase", "Finish it with `git rebase --continue` or cancel it with `git rebase --abort`",
     "rebase가", "`git rebase --continue`로 끝내거나 `git rebase --abort`로 취소한 뒤"),
    ("rebase_apply", "a rebase or git am",
     ("Finish it with `git rebase --continue` (or `git am --continue`) or cancel it with `git rebase --abort` "
      "(or `git am --abort`)"),
     "rebase나 git am이", ("`git rebase --continue`(또는 `git am --continue`)로 끝내거나 `git rebase --abort`(또는 "
                         "`git am --abort`)로 취소한 뒤")),
    ("bisect", "a bisect", "End it with `git bisect reset`", "bisect가", "`git bisect reset`으로 끝낸 뒤"),
)

MESSAGES: dict[str, Entry] = {
    # A moment the owner reads: with its date, since the owner may read it the next day, and the server's zone.
    **{f"build.month.{i}": {"en": name, "ko": f"{i}월"} for i, name in enumerate(_MONTHS, 1)},
    "build.at": {"en": "{month} {day}, {hm} ({tz})", "ko": "{month} {day}일 {hm} ({tz})"},

    # ---- where a development stands (status.py)
    "build.doing.INTAKE": {"en": "Reading the spec.", "ko": "명세를 읽고 있어요."},
    "build.doing.PLAN_REVIEW": {"en": "Planning the build.", "ko": "구현 계획을 세우고 있어요."},
    "build.doing.IMPLEMENTING": {"en": "Building the features.", "ko": "기능을 만들고 있어요."},
    "build.doing.QA": {"en": "Running the tests.", "ko": "테스트를 돌리고 있어요."},
    "build.doing.REVIEWING": {"en": "A separate reviewer is reading the code.", "ko": "다른 검토자가 코드를 보고 있어요."},
    "build.doing.SHIPPING": {"en": "Delivering the branch.", "ko": "브랜치를 전달하고 있어요."},

    "build.stage.spec_waiting": {"en": "Waiting for the product agent to write the spec.", "ko": "명세를 쓸 차례를 기다려요."},
    "build.stage.spec_job_missing": {"en": "No job to write the spec is on the queue. Check the decision again.",
                                     "ko": "명세를 쓸 작업이 대기열에 없어요. 결정을 다시 확인해 주세요."},
    "build.stage.spec_writing": {"en": "The product agent is writing the spec.", "ko": "제품기획 에이전트가 명세를 쓰고 있어요."},
    "build.stage.spec_writing_next": {"en": "When it's done, it comes to Decisions for your approval.",
                                      "ko": "다 쓰면 결정으로 올라와요."},
    "build.stage.spec_failed": {"en": "Couldn't write the spec.", "ko": "명세를 쓰지 못했어요."},
    "build.stage.spec_redrafting": {"en": "The product agent is rewriting the spec from your notes.",
                                    "ko": "제품기획 에이전트가 메모대로 명세를 다시 쓰고 있어요."},
    "build.stage.spec_redraft_failed": {"en": "Couldn't rewrite the spec from your notes.",
                                        "ko": "메모대로 명세를 다시 쓰지 못했어요."},
    "build.stage.spec_redraft_failed_next": {"en": "Your notes are kept. Try again and it starts from them.",
                                             "ko": "메모는 그대로 있어요. 다시 시도하면 그 메모로 다시 써요."},
    "build.stage.spec_redraft_stopped": {"en": "Rewriting the spec from your notes was stopped.",
                                         "ko": "메모대로 다시 쓰기를 멈췄어요."},
    "build.notes.dropped": {"en": "You dropped your notes on the screens and declined the spec",
                            "ko": "화면에 남긴 메모를 버리고 명세를 반려했어요"},
    "build.notes.refuse_not_sent_back": {
        "en": "This isn't a spec you sent back with notes on its screens, so there are no notes to drop.",
        "ko": "화면 메모와 함께 돌려보낸 명세가 아니라서 버릴 메모가 없어요."},
    "build.notes.refuse_moved_on": {
        "en": "The spec was already rewritten or decided, so these notes can't be dropped any more.",
        "ko": "명세를 이미 다시 썼거나 결정해서 이 메모는 이제 버릴 수 없어요."},
    "build.notes.refuse_writing": {
        "en": "The product agent is rewriting the spec from these notes right now. Wait for it to finish.",
        "ko": "제품기획 에이전트가 지금 이 메모대로 명세를 다시 쓰고 있어요. 끝날 때까지 기다려 주세요."},
    "build.stage.spec_pending": {"en": "Waiting for you to approve the spec.", "ko": "명세 승인을 기다려요."},
    "build.stage.spec_pending_next": {"en": "The product agent wrote the spec. Approve it and the engineering team starts right away.",
                                      "ko": "제품기획 에이전트가 명세를 썼어요. 승인하면 개발팀이 바로 시작해요."},
    "build.stage.spec_deferred": {"en": "You put the spec on hold.", "ko": "명세 승인을 보류했어요."},
    "build.stage.spec_deferred_next": {"en": "Approve it and the engineering team starts.",
                                       "ko": "승인하면 개발팀이 시작해요."},
    "build.stage.preparing": {"en": "Getting ready to start building.", "ko": "개발을 시작할 준비를 하고 있어요."},
    "build.stage.prepare_failed": {"en": "Couldn't start building.", "ko": "개발을 시작하지 못했어요."},
    "build.stage.queued": {"en": "Waiting to start building.", "ko": "개발 시작을 기다려요."},
    "build.stage.no_worker_job": {"en": "The background process isn't working on it. Start over with 'Rebuild from scratch'.",
                                  "ko": "맡아서 하는 백그라운드 프로세스가 없어요. '처음부터 다시 만들기'로 새로 시작해 주세요."},
    "build.stage.running": {"en": "In progress. {doing}", "ko": "개발 중이에요. {doing}"},
    "build.stage.redeliver_waiting": {"en": "Waiting to deliver again.", "ko": "다시 전달할 차례를 기다려요."},
    "build.stage.question": {"en": "The engineering team is waiting for your answer.",
                             "ko": "개발팀이 질문의 답을 기다려요."},
    "build.stage.question_next": {"en": "The product agent couldn't answer it from the spec. Answer it and the build carries on.",
                                  "ko": "제품기획 에이전트가 명세만으로는 답하지 못한 질문이에요. 답하면 개발을 이어 가요."},
    "build.stage.dropped": {"en": "You dropped this build. Nothing was merged.",
                            "ko": "이 개발 결과는 버렸어요. 머지한 건 없어요."},
    "build.stage.failed_put_off": {"en": "This build failed its check. You haven't decided yet whether to fix or drop it.",
                                   "ko": "이 개발 결과는 점검을 통과하지 못했어요. 고칠지 버릴지는 아직 정하지 않았어요."},
    "build.stage.failed_put_off_next": {"en": "Nothing was merged. Decide whether to fix or drop it, or throw this result "
                                              "away to build it again from scratch.",
                                        "ko": "머지한 건 없어요. 고칠지 버릴지 정하거나, 처음부터 다시 만들려면 이 결과를 "
                                              "버려 주세요."},
    "build.stage.discarded": {"en": "You threw this result away. Waiting for the new attempt.",
                              "ko": "결과를 버렸어요. 새로 만들 차례를 기다려요."},
    "build.stage.ship_stuck": {"en": "The code is done (tests and review passed), but delivery got stuck.",
                               "ko": "코드는 다 만들었어요(테스트·리뷰 통과). 전달하다 막혔어요."},
    "build.stage.ship_stuck_next": {"en": "'Deliver again' picks up from this step without rebuilding. The cause is in "
                                          "the raw error in the build log.",
                                    "ko": "'전달만 다시'를 누르면 이 단계부터 이어 가요. 다시 만들지는 않아요. 원인은 "
                                          "진행 기록의 오류 원문에 있어요."},
    "build.stage.stopped": {"en": "The build stopped.", "ko": "개발이 멈췄어요."},
    "build.stage.worker_lost": {"en": "The background process building it stopped responding.",
                                "ko": "개발하던 백그라운드 프로세스가 응답하지 않았어요."},
    "build.stage.rebuild_at": {"en": "You can rebuild from scratch at {until}.",
                               "ko": "{until} 이후에 처음부터 다시 만들 수 있어요."},
    "build.stage.resuming": {"en": "The background process restarted. The build picks up where it left off.",
                             "ko": "백그라운드 프로세스가 다시 시작해서 하던 곳부터 이어서 만들어요."},
    "build.stage.interrupted": {"en": "The background process stopped partway, so this run can't pick up where it "
                                      "left off. Once the background process is back and closes it, you'll see "
                                      "what to do.",
                                "ko": "백그라운드 프로세스가 중간에 멈춰서 하던 곳부터 이어 갈 수 없어요. 다시 "
                                      "켜져서 이 작업을 정리하면 할 일을 알려 드릴게요."},
    "build.stage.usage_paused": {"en": "Hit a usage limit. The build picks up where it left off at {until}.",
                                 "ko": "사용 한도에 걸려서 {until}에 하던 곳부터 이어서 만들어요."},
    "build.stage.usage_reset": {"en": "The usage limit has reset. The build picks up where it left off.",
                                "ko": "사용 한도가 풀려서 하던 곳부터 이어서 만들어요."},
    "build.stage.no_answer": {"en": {"one": "The background process building it hasn't responded for {n} minute.",
                                     "other": "The background process building it hasn't responded for {n} minutes."},
                              "ko": "개발하던 백그라운드 프로세스가 {n}분째 응답이 없어요."},
    "build.stage.no_answer_next": {"en": "If it still hasn't answered by {until}, the build is marked stopped.",
                                   "ko": "{until}까지 응답이 없으면 멈춘 것으로 봐요."},
    "build.stage.no_cause": {"en": "No cause was recorded.", "ko": "원인 기록이 없어요."},
    "build.stage.merge_waiting": {"en": "Waiting for you to approve the merge.", "ko": "머지 승인을 기다려요."},
    "build.stage.merge_waiting_next": {"en": "The build is done and its branch delivered. Merging is yours to approve.",
                                       "ko": "개발을 끝내고 브랜치를 전달했어요. 머지는 오너님이 승인해요."},
    "build.stage.accepting": {"en": "The product agent is checking each acceptance criterion.",
                              "ko": "제품기획 에이전트가 완료 기준을 하나씩 확인하고 있어요."},
    "build.stage.delivered_check": {"en": "Check the delivered result.", "ko": "전달한 결과를 확인해 주세요."},
    "build.stage.delivered": {"en": "Delivered.", "ko": "전달했어요."},
    "build.stage.fix_question": {"en": "Waiting for you to say what to change.", "ko": "무엇을 고칠지 답을 기다려요."},
    "build.stage.fix_question_next": {"en": "Answer in Decisions, and the engineering team changes it on the same branch.",
                                      "ko": "결정에서 답해 주시면 개발팀이 같은 브랜치에서 고쳐요."},
    "build.stage.fix_waiting": {"en": "The fix you asked for waits to start.", "ko": "요청한 수정이 시작을 기다려요."},
    "build.stage.fix_budget": {"en": "The fix you asked for needs more budget.", "ko": "요청한 수정에 예산이 더 필요해요."},
    "build.stage.fix_budget_next": {"en": "A fix needs about ${need}, and ${left} is left. Raise the budget, then choose "
                                          "Fix it. You can also merge the build as it is.",
                                    "ko": "수정에 ${need} 정도 드는데 ${left} 남았어요. 예산을 올린 뒤 '고쳐 주세요'를 "
                                          "눌러 주세요. 지금 상태로 머지할 수도 있어요."},
    "build.stage.fix_budget_ok": {"en": "The budget covers it now. Choose Fix it to start.",
                                  "ko": "이제 예산이 충분해요. '고쳐 주세요'를 누르면 시작해요."},
    "build.stage.fix_stopped": {"en": "The fix stopped.", "ko": "수정이 멈췄어요."},

    "build.link.decision": {"en": "See the decision", "ko": "결정 보기"},
    "build.link.spec": {"en": "Review the spec", "ko": "명세 보고 승인하기"},
    "build.link.answer": {"en": "Answer the question", "ko": "질문에 답하기"},
    "build.link.budget": {"en": "Raise the budget", "ko": "예산 올리기"},
    "build.link.merge": {"en": "Decide on the merge", "ko": "머지 결정하기"},

    # why a queued worker job has not started
    "build.queue.usage": {"en": "Hit the {reason}, so it waits until {until}.", "ko": "{reason}에 닿아 {until}까지 기다려요."},
    "build.queue.usage_limit": {"en": "usage limit", "ko": "사용 한도"},
    "build.queue.starts_at": {"en": "Starts at {until}.", "ko": "{until}에 시작해요."},
    "build.queue.held": {"en": "Waits until {until} so the previous background process can finish what it was "
                                "doing.",
                         "ko": "{until}까지 기다려요. 이전 백그라운드 프로세스가 하던 일을 마저 끝낼 수 있게요."},
    "build.queue.ahead": {"en": "Starts as soon as the job ahead of it finishes.", "ko": "앞선 작업이 끝나면 바로 시작해요."},
    "build.queue.worker_down": {"en": "The background process is off, so nothing can start. Turn it on in a terminal "
                                      "with `uv run ship supervise`.",
                                "ko": "백그라운드 프로세스가 꺼져 있어 시작하지 못해요. 터미널에서 `uv run ship supervise`로 켜 주세요."},
    "build.queue.busy": {"en": {"one": "The background process is busy with {n} other job. This one starts when a slot "
                                       "frees up.",
                                "other": "The background process is busy with {n} other jobs. This one starts when "
                                         "a slot frees up."},
                         "ko": "백그라운드 프로세스가 다른 작업 {n}개를 하고 있어요. 자리가 나면 시작해요."},
    "build.queue.soon": {"en": "Starting soon.", "ko": "곧 시작해요."},

    # ---- a build's step lines (worker.py), stored as keys so the log reads in the language of the moment
    "build.step.intake_start": {"en": "Started reading the approved spec.", "ko": "승인한 명세를 읽기 시작했어요."},
    "build.step.intake_done": {"en": "Read the spec and settled how to build it.", "ko": "명세를 읽고 만들 방향을 정했어요."},
    "build.step.usage_deferred": {"en": "Hit a usage limit, so the start is put off.", "ko": "사용 한도에 닿아 시작을 미뤘어요."},
    "build.step.plan_start": {"en": "Planning the build.", "ko": "구현 계획을 세우고 있어요."},
    "build.step.plan_done": {"en": {"one": "Settled the build plan ({n} decision made).",
                                    "other": "Settled the build plan ({n} decisions made)."},
                             "ko": "구현 계획을 정했어요(정한 것 {n}개)."},
    "build.step.build_start": {"en": "Started building.", "ko": "기능을 만들기 시작했어요."},
    "build.step.build_done": {"en": "Built it. Files changed: {files}. Tests run: {tests}.",
                              "ko": "기능을 만들었어요. 파일 {files}개를 바꾸고 테스트 {tests}개를 돌렸어요."},
    "build.step.tests_run": {"en": "Running the tests (round {n}).", "ko": "테스트를 돌리고 있어요({n}번째)."},
    "build.step.tests_passed": {"en": "The tests passed.", "ko": "테스트를 통과했어요."},
    "build.step.tests_failed": {"en": "The tests failed. Fixing them and running them again.",
                                "ko": "테스트가 실패했어요. 고친 뒤 다시 돌려요."},
    "build.step.tests_none": {"en": "No test command, so the tests were skipped.",
                              "ko": "테스트 명령이 없어 테스트를 건너뛰었어요."},
    "build.step.review_start": {"en": "Started the code review. The engineer and a separate reviewer each look.",
                                "ko": "코드 리뷰를 시작했어요. 엔지니어와 다른 검토자가 따로 봐요."},
    "build.step.review_done": {"en": "The separate reviewer finished.", "ko": "다른 검토자가 리뷰를 마쳤어요."},
    "build.step.review_blocking": {"en": {"one": "The review found {n} thing that must be fixed.",
                                          "other": "The review found {n} things that must be fixed."},
                                   "ko": "리뷰에서 꼭 고칠 점이 {n}개 나왔어요."},
    "build.step.review_passed": {"en": "Passed the review.", "ko": "리뷰를 통과했어요."},
    "build.step.clarify": {"en": {"one": "Asked the product agent {n} question the spec doesn't answer.",
                                  "other": "Asked the product agent {n} questions the spec doesn't answer."},
                           "ko": "명세로 정할 수 없는 질문이 {n}개 생겨 제품기획 에이전트에게 물었어요."},
    "build.step.shipping": {"en": "Delivering the branch that passed the tests and the review.",
                            "ko": "테스트와 리뷰를 통과한 브랜치를 전달하고 있어요."},
    "build.step.delivered": {"en": "Delivered. The product agent is checking it against the acceptance criteria.",
                             "ko": "전달했어요. 제품기획 에이전트가 완료 기준을 하나씩 확인해요."},
    "build.step.delivered_local": {"en": "Delivered on this computer. It goes to the repository only when you approve "
                                         "the merge. The product agent is checking it now.",
                                   "ko": "이 컴퓨터에 전달했어요. 머지를 승인할 때 저장소에 올려요. 제품기획 에이전트가 "
                                         "지금 확인해요."},
    "build.step.merge_set_aside": {"en": "Merge approval {ref} is set aside while the change you asked for in team chat "
                                         "is made.",
                                   "ko": "팀 대화에서 요청한 수정을 하는 동안 머지 승인 {ref} 카드는 내려 뒀어요."},
    "build.step.card_set_aside": {"en": "{ref} is set aside while the change you asked for in team chat is made.",
                                  "ko": "팀 대화에서 요청한 수정을 하는 동안 {ref} 카드는 내려 뒀어요."},
    "build.step.pr_opened": {"en": "Opened the PR.", "ko": "PR을 열었어요."},
    "build.step.pushed": {"en": "Pushed the branch to the repository.", "ko": "브랜치를 저장소에 올렸어요."},
    "build.step.accept_retry": {"en": "The acceptance check failed. Trying again (attempt {attempt} of {max}).",
                                "ko": "완료 기준 확인에 실패해서 다시 해요({max}번 중 {attempt}번째)."},
    "build.step.accept_skipped": {"en": "Skipped the acceptance check.", "ko": "완료 기준 확인을 하지 않았어요."},
    "build.step.accepted": {"en": "The product agent accepted it. The merge is in Decisions.",
                            "ko": "제품기획 에이전트가 완료 기준을 모두 확인했어요. 머지 결정을 올렸어요."},
    "build.step.not_accepted": {"en": "The product agent found some acceptance criteria unmet. You decide whether to merge.",
                                "ko": "제품기획 에이전트가 보기에 완료 기준 일부를 채우지 못했어요. 머지할지는 오너님이 정해요."},
    "build.step.resumed": {"en": "The background process restarted, so the build picks up where it left off.",
                           "ko": "백그라운드 프로세스가 다시 시작해서 하던 곳부터 이어서 만들고 있어요."},
    "build.step.usage_paused": {"en": "Hit a usage limit, so the build stopped here. It picks up where it left off once "
                                      "the limit resets.",
                                "ko": "사용 한도에 걸려서 여기서 멈췄어요. 한도가 풀리면 하던 곳부터 이어서 만들어요."},
    "build.step.waited": {"en": "The usage limit reset, so the build picks up where it left off.",
                          "ko": "사용 한도가 풀려서 하던 곳부터 이어서 만들고 있어요."},
    "build.step.deliver_again": {"en": "Delivering again, as you asked. Nothing is rebuilt.",
                                 "ko": "요청한 대로 전달만 다시 해요. 다시 만들지는 않아요."},
    "build.step.deliver_again_refused": {"en": "Didn't deliver again.", "ko": "전달만 다시 하지 않았어요."},
    "build.step.redelivering": {"en": "Delivering the branch that passed the tests and the review again.",
                                "ko": "테스트와 리뷰를 통과한 브랜치를 다시 전달하고 있어요."},
    "build.step.accept_again": {"en": "Checking the acceptance criteria again, as you asked.",
                                "ko": "요청한 대로 완료 기준을 다시 확인해요."},
    "build.step.merge_again": {"en": "Asked you again to approve the merge.", "ko": "머지 결정을 다시 올렸어요."},
    "build.step.discarded": {"en": "Threw this result away. Building it again from the same spec.",
                             "ko": "이 결과를 버리고 같은 명세로 새로 만들어요."},
    "build.step.merge_rejected": {"en": "You rejected the merge. The branch stays as it is.",
                                  "ko": "머지를 반려했어요. 브랜치는 그대로예요."},
    "build.step.merge_deferred": {"en": "You put the merge on hold. The branch stays as it is.",
                                  "ko": "머지를 보류했어요. 브랜치는 그대로예요."},
    "build.step.merged": {"en": "Merged.", "ko": "머지했어요."},
    "build.step.fix_asked": {"en": "You turned the merge down without a note, so the team asked what to change.",
                             "ko": "메모 없이 머지를 반려해서 무엇을 고칠지 물어봤어요."},
    "build.step.fix_queued": {"en": "You asked for changes. The engineering team makes them on the same branch.",
                              "ko": "수정 요청을 받았어요. 개발팀이 같은 브랜치에서 고쳐요."},
    "build.step.fix_budget": {"en": "You asked for changes. A fix needs about ${need} and the budget has ${left} left, so "
                                    "it starts once you raise the budget.",
                              "ko": "수정 요청을 받았어요. 수정에 ${need} 정도 드는데 ${left} 남아서, 예산을 올리면 "
                                    "시작해요."},
    "build.step.fix_start": {"en": "Started the changes you asked for.", "ko": "요청한 수정을 시작했어요."},
    "build.step.fix_stopped": {"en": "The fix stopped. The build you were shown is unchanged.",
                               "ko": "수정이 멈췄어요. 보여 드린 결과는 그대로예요."},
    "build.step.fix_skipped": {"en": "No fix started: this build no longer waits for a merge decision.",
                               "ko": "이 개발은 더 이상 머지 결정을 기다리지 않아서 수정을 시작하지 않았어요."},
    "build.step.fix_held": {"en": "No fix started: a merge decision for this build is still waiting. Decide on it "
                                  "first.",
                            "ko": "이 개발의 머지 결정이 아직 기다리고 있어서 수정을 시작하지 않았어요. 머지부터 "
                                  "정해 주세요."},
    "build.fix.stopped": {"en": "The fix stopped: {cause} The build you were shown is unchanged. You can merge it, ask "
                                "for the fix again, or throw it away.",
                          "ko": "수정이 멈췄어요: {cause} 보여 드린 결과는 그대로예요. 머지하거나, 수정을 다시 "
                                "요청하거나, 버릴 수 있어요."},
    "build.fix.interrupted": {"en": "the background process stopped in the middle of it.",
                              "ko": "작업 도중 백그라운드 프로세스가 멈췄어요."},
    "build.fix.stopped_pushed": {"en": "The fix stopped after its branch went to the code host: {cause} That branch is the "
                                       "build now and nobody has checked it. Check it again before you merge it, or throw "
                                       "it away.",
                                 "ko": "고친 브랜치를 코드 저장소 서버에 올린 뒤 수정이 멈췄어요: {cause} 이제 그 브랜치가 "
                                       "결과인데 아직 아무도 확인하지 않았어요. 머지하기 전에 다시 확인하거나 결과를 "
                                       "버려 주세요."},
    "build.fix.not_restored": {"en": "A checkout of the product has its branch open, so the branch still holds the "
                                     "stopped fix and a merge waits until that checkout moves to another branch.",
                               "ko": "제품 폴더에 이 브랜치가 열려 있어서 멈춘 수정이 브랜치에 남아 있어요. 그 폴더를 "
                                     "다른 브랜치로 옮기기 전에는 머지할 수 없어요."},
    "build.fix_card.title": {"en": "What should change before you merge it? {title}",
                             "ko": "머지 전에 무엇을 고칠까요? {title}"},
    "build.fix_card.lead": {"en": "You turned the merge down. Write what should change, and the engineering team changes "
                                  "it on the same branch, then asks you about the merge again.",
                            "ko": "머지를 반려했어요. 고칠 점을 적어 주시면 개발팀이 같은 브랜치에서 고친 뒤 머지를 "
                                  "다시 물어볼게요."},
    "build.fix_card.unmet": {"en": "What the product agent found unmet:", "ko": "제품기획 에이전트가 채우지 못했다고 본 기준이에요."},
    "build.fix_card.why": {"en": "A fix starts from what you want changed.", "ko": "고칠 점을 알아야 수정을 시작해요."},
    "build.step.failed_after_delivery": {"en": "The delivered branch is intact. A later step failed.",
                                         "ko": "전달한 브랜치는 그대로예요. 그다음 단계가 실패했어요."},

    # ---- why a build stopped (DevJob.error, in the language of the moment)
    # budget_scope reads a stored stop by these two, so a change keeps the old words matching too
    "build.budget.stop": {"en": "The build stopped: the budget ran out.", "ko": "예산이 모자라 개발을 멈췄어요."},
    "build.budget.month": {"en": "The company's monthly budget is used up.",
                           "ko": "회사 전체 월 한도를 이번 달에 다 썼어요."},
    "build.budget.month_next": {"en": "Raise the monthly budget in Settings, or wait for next month, then choose "
                                      "'Rebuild from scratch'.",
                                "ko": "설정에서 월 한도를 올리거나 다음 달까지 기다린 뒤 '처음부터 다시 만들기'를 눌러 주세요."},
    "build.budget.project": {"en": "Spent ${spent} of the ${budget} budget (research and analysis ${research}, building "
                                   "${dev}). Finishing the build needs about ${short} more. Raise the budget in project "
                                   "settings, then choose 'Rebuild from scratch'.",
                             "ko": "예산 ${budget} 중 ${spent} 사용(조사·분석 ${research}, 개발 ${dev}). 개발을 끝내려면 "
                                   "약 ${short} 더 필요해요. 프로젝트 설정에서 예산을 올린 뒤 '처음부터 다시 만들기'를 "
                                   "눌러 주세요."},

    "build.error.docker_down": {"en": "Can't reach Docker. Check that Docker Desktop is running, then try again.",
                                "ko": "Docker에 연결할 수 없어요. Docker Desktop이 켜져 있는지 확인하고 다시 시도해 주세요."},
    "build.error.engineer_image": {"en": "The engineer sandbox image ({image}) is missing. Build it with "
                                         "`uv run ship sandbox build engineer`, then try again.",
                                   "ko": "엔지니어 샌드박스 이미지({image})가 없어요. `uv run ship sandbox build "
                                         "engineer`로 만든 뒤 다시 시도해 주세요."},
    "build.error.not_from_start": {"en": "J-{id} is {status}, so it won't run again from the start. The queue ran it "
                                         "again after the background process stopped. If it didn't finish, start a "
                                         "new attempt.",
                                   "ko": "J-{id|는} {status} 상태라 처음부터 다시 실행하지 않아요. 백그라운드 "
                                         "프로세스가 멈춘 뒤 대기열이 다시 실행한 경우예요. 끝나지 않았으면 새로 "
                                         "시도해 주세요."},
    "build.error.engineer": {"en": "The engineer's step failed: {cause}", "ko": "엔지니어 단계가 실패했어요: {cause}"},
    "build.error.reviewer": {"en": "The separate code review failed: {cause}", "ko": "다른 검토자의 코드 리뷰가 실패했어요: {cause}"},
    "build.error.unresolved": {"en": "Still not fixed after {n} rounds of fixes (tests passed: {tests}, must-fix "
                                     "findings: {blocking}).",
                               "ko": "{n}번 고쳤는데도 해결하지 못했어요(테스트 통과: {tests}, 꼭 고칠 점: {blocking}개)."},
    "build.error.no_commits": {"en": "The build produced no commits.", "ko": "개발 작업이 커밋을 하나도 만들지 않았어요."},
    "build.error.push": {"en": "Couldn't push the branch: {cause}", "ko": "브랜치를 올리지 못했어요: {cause}"},
    "build.error.pr": {"en": "Couldn't open the PR: {cause}", "ko": "PR을 열지 못했어요: {cause}"},
    "build.error.gh_merge": {"en": "gh couldn't merge the PR: {cause}", "ko": "gh로 PR을 머지하지 못했어요: {cause}"},
    "build.error.accept_failed": {"en": "The acceptance check failed: {cause}", "ko": "완료 기준 확인이 실패했어요: {cause}"},
    "build.error.merge_failed": {"en": "The merge didn't land, and the merge approval is still unused: {cause}",
                                 "ko": "머지하지 못했어요. 머지 승인은 쓰지 않고 남겨 뒀어요: {cause}"},
    "build.error.stalled": {"en": "No background process was working on J-{id}, so it was closed as failed and a new "
                                  "attempt was made.",
                            "ko": "J-{id|를} 맡은 백그라운드 프로세스가 없어서 실패로 정리하고 새로 시도했어요."},
    "build.error.not_plain": {"en": "{path} in the engineer's clone isn't a plain file or folder (it may be a symbolic "
                                    "link), so nothing was written there. Delete the work tree and try again.",
                              "ko": "엔지니어 clone에 있는 {path} 경로가 일반 파일이나 폴더가 아니라서(심볼릭 링크일 수 "
                                    "있어요) 쓰지 않았어요. 작업 트리를 지우고 다시 시도해 주세요."},
    "build.error.attributes": {"en": "products/{name}/.git/info/attributes has attribute settings, so the background process won't "
                                     "touch the engineer's branch. git can't switch that file off, so a filter or merge "
                                     "driver set there could run on this PC against files the engineer wrote. Remove "
                                     "those settings, or, if only your own git needs them, move them to a global "
                                     "core.attributesFile (the background process doesn't read it). Then try again.",
                               "ko": "products/{name}/.git/info/attributes에 속성 설정이 있어서 엔지니어 브랜치를 "
                                     "건드리지 않았어요. git은 이 파일을 끌 수 없어서, 거기 설정한 필터나 머지 "
                                     "드라이버가 엔지니어가 쓴 파일을 이 PC에서 실행할 수 있어요. 그 설정을 지우거나, "
                                     "오너님이 쓰는 git에만 필요하면 전역 core.attributesFile로 옮긴 뒤(백그라운드 "
                                     "프로세스는 읽지 않아요) 다시 시도해 주세요."},
    "build.error.git_missing": {"en": "Can't find git. Install git 2.45.1 or later and put it on PATH.",
                                "ko": "git을 찾지 못했어요. git 2.45.1 이상을 설치하고 PATH에 넣어 주세요."},
    "build.error.git_old": {"en": "git {version} is too old. A git before 2.41 has no --attr-source, so it can't ignore "
                                  "the attributes on an engineer's branch, so container builds can't run. "
                                  + _UPDATE_GIT["en"],
                            "ko": "git {version|은} 너무 오래됐어요. 2.41보다 낮은 git은 --attr-source가 없어 엔지니어 "
                                  "브랜치의 속성을 무시하지 못해서, 컨테이너 개발 작업을 받지 않아요. "
                                  + _UPDATE_GIT["ko"]},
    "build.error.git_unsafe": {"en": "git {version} isn't safe: fetching from an engineer's clone runs a command planted "
                                     "in the clone on this PC (CVE-2024-32465, CVE-2024-32004, confirmed by running the "
                                     "attack). Container builds can't run. " + _UPDATE_GIT["en"],
                               "ko": "git {version|은} 안전하지 않아요. 엔지니어 clone에서 가져올 때 clone에 심은 명령을 "
                                     "이 PC에서 실행해요(CVE-2024-32465, CVE-2024-32004, 직접 시험해 확인). 컨테이너 "
                                     "개발 작업을 받지 않아요. " + _UPDATE_GIT["ko"]},
    "build.error.git_probe": {"en": "Couldn't run the safety test for fetching from an engineer's clone, so container "
                                    "builds can't run. Cause: {cause} " + _UPDATE_GIT["en"],
                              "ko": "엔지니어 clone 가져오기 안전 시험을 돌리지 못해서 컨테이너 개발 작업을 받지 않아요. "
                                    "원인: {cause} " + _UPDATE_GIT["ko"]},
    "build.error.no_test_image": {"en": "The spec names no test image, so the tests didn't run. Tests never run on this "
                                        "PC itself. Name a test image in the spec.",
                                  "ko": "명세에 테스트 이미지가 없어 테스트를 돌리지 않았어요. 테스트는 이 PC에서 직접 "
                                        "돌리지 않아요. 명세에 테스트 이미지를 넣어 주세요."},
    "build.error.tests_docker": {"en": "Can't reach Docker, so the tests didn't run. Check that Docker Desktop is "
                                       "running, then try again.",
                                 "ko": "Docker에 연결할 수 없어 테스트를 돌리지 않았어요. Docker Desktop이 켜져 있는지 "
                                       "확인하고 다시 시도해 주세요."},
    "build.error.test_image_pull": {"en": "Couldn't get the test image ({image}), so the tests didn't run.",
                                    "ko": "테스트 이미지({image})를 받지 못해 테스트를 돌리지 않았어요."},

    # the sandbox and the engineer's runtime (sandbox/docker.py, runtime/host_auth.py, runtime/claude_cli.py)
    "build.sandbox.docker_slow": {"en": "Docker didn't answer within {s} seconds. Check that Docker Desktop is running "
                                        "and responding.",
                                  "ko": "Docker가 {s}초 안에 답하지 않았어요. Docker Desktop이 켜져 있고 응답하는지 "
                                        "확인해 주세요."},
    "build.sandbox.docker_down": {"en": "Docker isn't running. Start Docker Desktop.",
                                  "ko": "Docker가 꺼져 있어요. Docker Desktop을 켜 주세요."},
    "build.sandbox.image_missing": {"en": "The sandbox image {image} is missing. Build it with `ship sandbox build`.",
                                    "ko": "샌드박스 이미지가 없어요({image}). `ship sandbox build`로 만들어 주세요."},
    "build.sandbox.egress_down": {"en": "The {proxy} proxy, the sandbox's only way out, isn't running. Start it with "
                                        "`docker compose up -d egress`.",
                                  "ko": "샌드박스가 밖으로 나가는 유일한 길인 {proxy} 프록시가 꺼져 있어요. "
                                        "`docker compose up -d egress`로 켜 주세요."},
    "build.sandbox.login_missing": {"en": "The engineer sandbox needs this PC's Claude subscription login. Run `claude` "
                                          "on this PC and sign in ({path}). A long-lived token "
                                          "(SHIP_CLAUDE_OAUTH_TOKEN) is never passed to the container.",
                                    "ko": "엔지니어 샌드박스는 이 PC의 Claude 구독 로그인이 있어야 해요. 이 PC에서 "
                                          "`claude`를 실행해 로그인해 주세요({path}). 장기 토큰"
                                          "(SHIP_CLAUDE_OAUTH_TOKEN)은 컨테이너에 넘기지 않아요."},
    "build.sandbox.login_none": {"en": "Not signed in. Sign in to your subscription with `claude` on this PC ({path} is "
                                       "missing).",
                                 "ko": "로그인돼 있지 않아요. 이 PC에서 `claude`로 구독 계정에 로그인해 주세요"
                                       "({path} 없음)."},
    "build.sandbox.login_ok": {"en": "This PC's Claude Code login ({n} min left on the access token, renews itself)",
                               "ko": "이 PC의 Claude Code 로그인(접근 토큰 {n}분 남음, 자동 갱신)"},
    "build.sandbox.host_refused": {"en": "engineering.isolation is host, which lets the engineer read this PC's secrets "
                                         "(var/api_token, the Claude login), so the call was refused. Set it to "
                                         "container. To use host anyway, knowing the risk, set "
                                         "engineering.unsafe_host_isolation_ack to true in config/company.yaml.",
                                   "ko": "engineering.isolation이 host라서 거부했어요. host에서는 엔지니어가 이 PC의 "
                                         "비밀(var/api_token, Claude 로그인)을 읽을 수 있어요. container로 바꿔 주세요. "
                                         "위험을 알고도 쓰려면 config/company.yaml의 "
                                         "engineering.unsafe_host_isolation_ack를 true로 둬 주세요."},

    # what stopped a job, by the kind of error
    "build.cause.grant": {"en": "No merge approval: there's none, it expired, or it was already used. Approve the merge "
                                "again in Decisions.",
                          "ko": "머지 승인이 없어요. 승인하지 않았거나 기한이 지났거나 이미 썼어요. 결정에서 머지를 "
                                "다시 승인해 주세요."},
    "build.cause.agent": {"en": "An agent failed: {cause}", "ko": "에이전트가 실패했어요: {cause}"},
    "build.cause.budget": {"en": "Over budget: {cause}", "ko": "예산을 넘었어요: {cause}"},
    "build.cause.host": {"en": "A step on this PC failed: {cause}", "ko": "이 PC에서 하는 작업이 실패했어요: {cause}"},
    "build.cause.unexpected": {"en": "Unexpected error ({kind}): {cause}", "ko": "예상하지 못한 오류예요({kind}): {cause}"},

    # merge refusals: the owner's checkout is not in a state the merge may change
    "build.merge.wrong_status": {"en": "J-{id} is {status}, so it isn't merged.",
                                 "ko": "J-{id|는} {status} 상태라 머지하지 않았어요."},
    "build.merge.changed_since": {"en": "The build changed after you approved this merge, so nothing was merged. "
                                        "Decide on the new merge in Decisions instead.",
                                  "ko": "머지를 승인한 뒤 결과가 바뀌어서 머지하지 않았어요. 새로 올라온 머지 결정을 "
                                        "정해 주세요."},
    "build.merge.not_delivered": {"en": "The {branch} branch is at {tip}, not at the delivered commit {head}, so nothing "
                                        "was merged. Ask for the fix again or throw the result away.",
                                  "ko": "{branch} 브랜치가 전달한 커밋({head}) 뒤로 바뀌어서 머지하지 않았어요(지금 커밋: "
                                        "{tip}). 수정을 다시 요청하거나 결과를 버려 주세요."},
    "build.merge.conflict": {"en": "The {branch} branch conflicts with {base}, so it wasn't merged. Conflicting files: "
                                   "{files}. Merge it yourself in products/{name} with `git merge --no-ff {branch}` and "
                                   "resolve the conflicts, then approve the merge again.",
                             "ko": "{branch} 브랜치가 {base} 브랜치와 충돌해서 머지하지 않았어요. 충돌한 파일: {files}. "
                                   "products/{name}에서 `git merge --no-ff {branch}`로 직접 머지해 충돌을 푼 뒤, 머지를 "
                                   "다시 승인해 주세요."},
    "build.merge.prunable": {"en": "The work tree at {where} is gone but still in git's work tree list, so nothing was "
                                   "merged. Run `git worktree prune` in products/{name}, then approve the merge again.",
                             "ko": "{where}의 작업 트리 폴더는 없어졌는데 git 작업 트리 목록에는 남아 있어서 머지하지 "
                                   "않았어요. products/{name}에서 `git worktree prune`을 실행한 뒤 머지를 다시 승인해 "
                                   "주세요."},
    "build.merge.checkout_moved": {"en": "The checkout at {where} changed in the meantime, so nothing was merged. Approve "
                                         "the merge again.",
                                   "ko": "그사이 {where}의 체크아웃이 바뀌어서 머지하지 않았어요. 머지를 다시 승인해 주세요."},
    "build.merge.owner_files": {"en": "git refused to merge {base} to protect your files at {where}:\n{said}\nMove or "
                                      "commit those files, then approve the merge again, or merge it yourself in "
                                      "products/{name} with `git merge --no-ff {branch}`.",
                                "ko": "{where}에 있는 오너님의 파일을 지키려고 git이 {base} 머지를 거부했어요:\n{said}\n"
                                      "그 파일을 치우거나 커밋한 뒤 머지를 다시 승인하거나, products/{name}에서 "
                                      "`git merge --no-ff {branch}`로 직접 머지해 주세요."},
    "build.merge.base_moved": {"en": "The {base} branch didn't move to the merge commit {commit} (the checkout changed in "
                                     "the meantime). Check with `git status` in products/{name}, then approve the merge "
                                     "again.",
                               "ko": "{base} 브랜치가 머지 커밋으로 옮겨 가지 않았어요({commit}, 그사이 체크아웃이 "
                                     "바뀌었어요). products/{name}에서 `git status`로 확인한 뒤 머지를 다시 승인해 주세요."},
    **{f"build.merge.busy.{op}": {
        "en": f"{{where}} has {what_en} in progress, so nothing was merged. {finish_en}, then approve the merge again "
              "(or merge it yourself in products/{name} with `git merge --no-ff {branch}`).",
        "ko": f"{{where}}에서 {what_ko} 진행 중이라 머지하지 않았어요. {finish_ko} 머지를 다시 승인해 주세요(또는 "
              "products/{name}에서 `git merge --no-ff {branch}`로 직접 머지해 주세요)."}
       for op, what_en, finish_en, what_ko, finish_ko in _BUSY},

    # why an owner action may not run on a build now
    "build.refuse.not_found": {"en": "J-{id} not found.", "ko": "J-{id|를} 찾지 못했어요."},
    "build.refuse.status.deliver_again": {"en": "J-{id} is {status}, so it can't be delivered again. Only a build that "
                                                "stopped while delivering can be.",
                                          "ko": "J-{id|는} {status} 상태라 전달만 다시 할 수 없어요. 전달하다 멈춘 "
                                                "작업만 이어서 전달해요."},
    "build.refuse.status.accept_again": {"en": "J-{id} is {status}, so its acceptance check can't run again.",
                                         "ko": "J-{id|는} {status} 상태라 완료 기준을 다시 확인할 수 없어요."},
    "build.refuse.status.merge_again": {"en": "J-{id} is {status}, so its merge can't be asked for again.",
                                        "ko": "J-{id|는} {status} 상태라 머지를 다시 요청할 수 없어요."},
    "build.refuse.status.fix_again": {"en": "J-{id} is {status}, so there's no fix to ask for again.",
                                      "ko": "J-{id|는} {status} 상태라 수정을 다시 요청할 수 없어요."},
    "build.refuse.fix_pending": {"en": "A question about what to change is waiting ({ref}). Answer it in Decisions first.",
                                 "ko": "무엇을 고칠지 묻는 질문({ref})이 기다리고 있어요. 결정에서 먼저 답해 주세요."},
    "build.refuse.status.fix_or_drop_again": {"en": "J-{id} is {status}, so there's no fix-or-drop question to ask again.",
                                              "ko": "J-{id|는} {status} 상태라 고칠지 버릴지 다시 물을 수 없어요."},
    "build.refuse.not_failed": {"en": "This build didn't fail its check, so there's no fix-or-drop question to ask.",
                                "ko": "이 개발 결과는 확인을 통과해서 고칠지 버릴지 물을 게 없어요."},
    "build.refuse.no_fix": {"en": "There's no fix to ask for again. Turn the merge down with a note to ask for one.",
                            "ko": "다시 요청할 수정이 없어요. 머지를 반려하면서 메모를 남기면 수정을 요청할 수 있어요."},
    "build.refuse.fix_budget": {"en": "A fix needs about ${need}, and the budget has ${left} left. Raise the project "
                                      "budget first.",
                                "ko": "수정에 ${need} 정도 드는데 ${left} 남았어요. 먼저 프로젝트 예산을 "
                                      "올려 주세요."},
    "build.refuse.status.discard": {"en": "J-{id} is {status}, so its result can't be thrown away.",
                                    "ko": "J-{id|는} {status} 상태라 결과를 버릴 수 없어요."},
    "build.refuse.newer_attempt": {"en": "The same spec has a newer attempt, so this branch isn't delivered.",
                                   "ko": "같은 명세로 새로 만든 작업이 있어서 이 브랜치는 전달하지 않아요."},
    "build.refuse.unreviewed": {"en": "Can't confirm the branch is still at the reviewed commit, so it isn't delivered "
                                      "again. Only what the review read is delivered. Start a new attempt.",
                                "ko": "브랜치가 리뷰한 커밋에 그대로 있는지 확인할 수 없어서 전달만 다시 하지 않아요. "
                                      "리뷰한 것만 전달해요. 새로 시도해 주세요."},
    "build.refuse.deliver_busy": {"en": "A delivery is already queued or under way.",
                                  "ko": "전달이 이미 대기 중이거나 진행 중이에요."},
    "build.refuse.merge_pending": {"en": "A merge decision is waiting ({ref}). Decide on it in Decisions first.",
                                   "ko": "머지 결정이 기다리고 있어요({ref}). 결정에서 먼저 정해 주세요."},
    "build.refuse.busy": {"en": "An acceptance check or a merge is already queued or under way.",
                          "ko": "완료 기준 확인이나 머지가 이미 대기 중이거나 진행 중이에요."},
    "build.refuse.accepted_already": {"en": "The product agent's acceptance check already has a result. Ask for the merge again, or "
                                            "throw this result away.",
                                      "ko": "제품기획 에이전트의 완료 기준 확인 결과가 이미 있어요. 머지를 다시 요청하거나 이 결과를 "
                                            "버려 주세요."},
    "build.refuse.no_acceptance": {"en": "There's no acceptance check result yet, so the merge can't be asked for. Run "
                                         "the acceptance check again.",
                                   "ko": "완료 기준 확인 결과가 없어서 머지를 요청할 수 없어요. 완료 기준 확인을 다시 "
                                         "해 주세요."},
    "build.refuse.merged_already": {"en": "J-{id} is already merged, so its result was recorded as merged instead of "
                                          "thrown away.",
                                    "ko": "J-{id|는} 이미 머지돼 있어서 결과를 버리지 않고 머지됨으로 기록했어요."},
    "build.refuse.forge_silent": {"en": "GitHub didn't say whether J-{id}'s pushed branch ({branch}) has an open PR, so "
                                        "the result wasn't thrown away. Check the gh login and the network, then try "
                                        "again.",
                                  "ko": "J-{id}에서 올린 브랜치({branch})에 열린 PR이 있는지 GitHub에서 답이 없어서 결과를 "
                                        "버리지 않았어요. gh 로그인과 네트워크를 확인한 뒤 다시 시도해 주세요."},
    "build.refuse.pr_merged": {"en": "The PR is already merged on GitHub, so the result wasn't thrown away ({url}).",
                               "ko": "GitHub에서 PR이 이미 머지돼서 결과를 버리지 않았어요({url})."},
    "build.refuse.pr_close": {"en": "Couldn't close the PR, so the result wasn't thrown away ({url}): {said} Close the PR "
                                    "on GitHub, then try again.",
                              "ko": "PR을 닫지 못해서 결과를 버리지 않았어요({url}): {said} GitHub에서 PR을 닫은 뒤 다시 "
                                    "시도해 주세요."},

    # a new attempt of a spec (specs.py)
    "build.retry.no_spec": {"en": "{key} has no approved spec.", "ko": "{key}에는 승인한 명세가 없어요."},
    "build.retry.busy": {"en": "{key}: J-{id} ({status}): {why}", "ko": "{key}: J-{id}({status}): {why}"},
    "build.retry.delivered": {"en": "{key}: J-{id} already delivered a branch ({status}). A new attempt would make a "
                                    "second branch for the same spec, so none was started.",
                              "ko": "{key}: J-{id}에서 이미 브랜치를 전달했어요({status}). 새로 시도하면 같은 명세에 "
                                    "브랜치가 둘이 되니 시작하지 않았어요."},
    "build.retry.not_failed": {"en": "{key}: the last build, J-{id}, is {status}. Only a build that failed, or whose "
                                     "result you threw away, can start again.",
                               "ko": "{key}: 마지막 개발 작업 J-{id|는} {status} 상태예요. 실패했거나 결과를 버린 작업만 "
                                     "다시 시도할 수 있어요."},
    "build.retry.used_up": {"en": "{key}: no attempts left.", "ko": "{key}: 다시 시도할 수 있는 횟수를 다 썼어요."},
    "build.retry.forge_silent": {"en": "{key}: GitHub didn't say whether the branch J-{id} pushed ({branch}, "
                                       "{status}) has an open PR, so no new attempt was started: it could open a second "
                                       "PR for the same spec. Check the gh login and the network, then try again.",
                                 "ko": "{key}: J-{id}에서 올린 브랜치({branch}, {status})에 열린 PR이 있는지 GitHub에서 답이 "
                                       "없어서 새로 시작하지 않았어요. 새로 시도하면 같은 명세에 PR이 둘이 될 수 있어요. "
                                       "gh 로그인과 네트워크를 확인한 뒤 다시 시도해 주세요."},
    "build.retry.pr_open": {"en": "{key}: J-{id} ({status}) already opened a PR ({url}). A new attempt would open a "
                                  "second one for the same spec. Review and merge that PR on GitHub, or close it here "
                                  "with 'Throw away and rebuild'.",
                            "ko": "{key}: J-{id}({status})에서 이미 PR을 열었어요({url}). 새로 시도하면 같은 명세에 PR이 "
                                  "둘이 돼요. GitHub에서 그 PR을 검토해 머지하거나, 여기서 '이 결과 버리고 새로 만들기'로 "
                                  "PR을 닫고 새로 시작해 주세요."},

    # ---- the spec's questions for the owner, before its card (questions.py)
    "build.clarify.title": {"en": "{question} · spec for {title}", "ko": "{question} · {title} 명세"},
    "build.clarify.owner_does": {"en": "Pick an answer, or write your own. It goes into the spec.",
                                 "ko": "답을 고르거나 직접 써 주세요. 답은 명세에 들어가요."},
    "build.clarify.rationale": {"en": "The product agent couldn't settle this from your plan or the research, and it changes what "
                                      "{title} does. Once every question is answered, the spec comes to you for "
                                      "approval with your answers in it.",
                                "ko": "제품기획 에이전트가 계획이나 조사만으로는 정하지 못한 부분이에요. 답에 따라 {title}에서 하는 "
                                      "일이 달라져요. 질문에 모두 답하면 답을 넣은 명세가 승인을 받으러 와요."},
    "build.clarify.decided_inline": {"en": "Decided by the owner: {answer}", "ko": "오너 결정: {answer}"},
    "build.clarify.assumed_inline": {"en": "Assumed, the owner didn't pick: {answer}",
                                     "ko": "오너가 고르지 않아 추천안으로 정함: {answer}"},
    "build.clarify.assumed_note": {"en": "you didn't pick, so the recommended answer",
                                   "ko": "고르지 않아서 추천안으로 정했어요"},
    "build.amend.question": {"en": "Change one of this product's rules?", "ko": "이 제품의 규칙을 바꿀까요?"},
    "build.amend.matters": {"en": "Every version of the product keeps its rules. Now: {old} Proposed: {rule}",
                            "ko": "제품의 모든 버전은 규칙을 지켜요. 지금: {old} 바꾸면: {rule}"},
    "build.amend.change": {"en": "Change the rule", "ko": "규칙 바꾸기"},
    "build.amend.keep": {"en": "Keep it as it is", "ko": "그대로 두기"},
    "build.amend.no_rule": {"en": "No such rule yet.", "ko": "아직 이런 규칙은 없어요."},
    "build.amend.why": {"en": "The product agent asks for this change for this spec. Keep the rule and the build keeps it "
                              "too.",
                        "ko": "제품기획 에이전트가 이 명세 때문에 바꾸자고 했어요. 그대로 두면 개발도 지금 규칙을 지켜요."},
    "build.clarify.owner_note": {"en": "your note: {note}", "ko": "남긴 메모: {note}"},
    "build.error.no_p1": {"en": "The spec has no user story the build must do first (P1), even after a second draft. "
                                "Retry to draft it again.",
                          "ko": "다시 써 봐도 명세에 가장 먼저 만들 사용자 이야기(P1)가 없어요. 다시 시도하면 새로 써요."},
    "build.stage.spec_questions": {"en": {"one": "The spec has {n} question for you.",
                                          "other": "The spec has {n} questions for you."},
                                   "ko": "명세에 답할 질문이 {n}개 있어요."},
    "build.stage.spec_answers_writing": {"en": "Writing your answers into the spec.", "ko": "답을 명세에 넣고 있어요."},
    "build.stage.spec_answers_failed": {"en": "Couldn't write your answers into the spec.",
                                        "ko": "답을 명세에 넣지 못했어요."},
    "build.clarify.open_inline": {"en": "Not decided yet: {what}", "ko": "아직 정하지 않음: {what}"},
    "build.stage.spec_questions_next": {"en": "Answer them and the spec comes to you for approval.",
                                        "ko": "답하면 명세가 승인을 받으러 와요."},

    # ---- the product's constitution (constitution.py), which the owner reads in the spec
    "build.constitution.title": {"en": "{product}: rules every version keeps", "ko": "{product}: 모든 버전이 지키는 규칙"},
    "build.constitution.lead": {"en": "The engineering team builds to these rules, and the review and the acceptance "
                                      "check hold every version to them. Only you change them.",
                                "ko": "개발팀은 이 규칙대로 만들고, 리뷰와 완료 기준 확인은 모든 버전을 이 규칙으로 봐요. "
                                      "규칙은 오너만 바꿀 수 있어요."},
    "build.constitution.keep_look": {"en": "Keep the product's current look: its styles, components and wording.",
                                     "ko": "지금 모습을 지켜요. 스타일, 컴포넌트, 문구를 그대로 써요."},
    "build.constitution.keep_look_why": {"en": "You brought a product people already know.",
                                         "ko": "이미 쓰는 사람이 있는 제품을 가져왔으니까요."},
    "build.constitution.keep_stack": {"en": "Keep its stack: no new framework, language or design system.",
                                      "ko": "지금 쓰는 기술을 지켜요. 새 프레임워크, 언어, 디자인 시스템은 넣지 않아요."},
    "build.constitution.keep_stack_why": {"en": "A change should fit the code that's already there.",
                                          "ko": "바꾸는 부분이 원래 코드에 맞아야 하니까요."},
    "build.constitution.keep_scope": {"en": "Change only what a spec asks for. Working code stays as it is.",
                                      "ko": "명세가 요청한 것만 바꿔요. 잘 돌아가는 코드는 그대로 둬요."},
    "build.constitution.keep_scope_why": {"en": "Each version stays small enough to check.",
                                          "ko": "버전마다 확인할 수 있을 만큼 작게 유지해요."},
    "build.constitution.from_product": {"en": "the product you brought", "ko": "가져온 제품"},
    "build.constitution.from_decision": {"en": "your decision on a spec", "ko": "명세 질문에 한 결정"},
    "build.constitution.governance": {"en": "Changing a rule", "ko": "규칙 바꾸기"},
    "build.constitution.governance_text": {"en": "A spec that needs a different rule asks you first, on a decision card "
                                                 "before the spec's approval. Without your yes, the rule stays.",
                                           "ko": "규칙을 바꿔야 하는 명세는 승인 전에 결정 카드로 먼저 물어봐요. "
                                                 "오너가 바꾸자고 하지 않으면 규칙은 그대로예요."},
    "build.constitution.version": {"en": "Version {version} · first written {ratified} · last changed {amended}",
                                   "ko": "버전 {version} · 처음 쓴 날 {ratified} · 마지막으로 바꾼 날 {amended}"},

    # ---- P1 scenarios at the acceptance check, and where the build and the stories disagree (stories.py)
    "build.scenario.criterion": {"en": "P1 scenario: given {given}, when {when}, then {then}",
                                 "ko": "P1 시나리오: {given} 상태에서 {when}, 결과는 {then}"},
    "build.scenario.all": {"en": "P1 scenarios ({n})", "ko": "P1 시나리오 {n}개"},
    "build.scenario.passed": {"en": "The build's own check ran it and it passed.",
                              "ko": "만든 코드의 확인 스크립트로 돌려서 통과했어요."},
    "build.scenario.failed": {"en": "The build's own check ran it and it failed.",
                              "ko": "만든 코드의 확인 스크립트로 돌렸는데 실패했어요."},
    "build.scenario.missing": {"en": "The build's own check never ran this scenario.",
                               "ko": "만든 코드의 확인 스크립트가 이 시나리오를 돌리지 않았어요."},
    "build.scenario.not_run": {"en": "Nobody ran it: the build's check didn't run at all.",
                               "ko": "아무도 돌려 보지 않았어요. 확인 스크립트가 아예 돌지 않았어요."},
    "build.gap.scenario": {"en": "{title} ({priority}): didn't pass when {given}, {when}: {then}",
                           "ko": "{title}({priority}): 이 경우를 통과하지 못했어요. {given} 상태에서 {when} → {then}"},
    "build.gap.not_run": {"en": "{title} ({priority}): nobody ran its scenarios.",
                          "ko": "{title}({priority}): 시나리오를 아무도 돌려 보지 않았어요."},
    "build.gap.unchecked": {"en": "{title} ({priority}): the acceptance check didn't say whether it's built.",
                            "ko": "{title}({priority}): 완료 기준 확인에서 만들었는지 보지 않았어요."},
    "build.gap.built_partly": {"en": "{title} ({priority}): only partly built. {detail}",
                               "ko": "{title}({priority}): 일부만 만들었어요. {detail}"},
    "build.gap.built_no": {"en": "{title} ({priority}): not built. {detail}",
                           "ko": "{title}({priority}): 만들지 않았어요. {detail}"},
    "build.gap.beyond": {"en": "Built beyond the spec: {detail}", "ko": "명세에 없는데 만든 것: {detail}"},

    # ---- cards in Decisions
    "build.option.approve": {"en": "Approve", "ko": "승인"},
    "build.option.reject": {"en": "Reject", "ko": "반려"},
    "build.option.defer": {"en": "Hold", "ko": "보류"},
    "build.option.merge": {"en": "Approve merge", "ko": "머지 승인"},

    "build.spec_card.title": {"en": "Approve the spec and start building: {title}",
                              "ko": "명세 승인하고 개발 시작: {title}"},
    "build.spec_card.body": {"en": "The product agent wrote the spec. Requirements: {reqs}. Acceptance criteria: {criteria}, {ux} of "
                                   "them for screens and the core task. Approve it and the engineering team starts "
                                   "building this version right away. Merging and deploying are asked separately.",
                             "ko": "제품기획 에이전트가 명세를 썼어요. 요구사항 {reqs}개, 완료 기준 {criteria}개예요(화면 상태·핵심 "
                                   "작업 {ux}개 포함). 승인하면 이 버전으로 개발팀이 바로 만들기 시작해요. 머지와 배포는 "
                                   "따로 물어볼게요."},
    "build.spec_card.body_no_screen": {"en": "The product agent wrote the spec. Requirements: {reqs}. Acceptance criteria: "
                                             "{criteria} (no screens). Approve it and the engineering team starts "
                                             "building this version right away. Merging and deploying are asked "
                                             "separately.",
                                       "ko": "제품기획 에이전트가 명세를 썼어요. 요구사항 {reqs}개, 완료 기준 {criteria}개예요(화면 "
                                             "없음). 승인하면 이 버전으로 개발팀이 바로 만들기 시작해요. 머지와 배포는 "
                                             "따로 물어볼게요."},
    "build.spec_kept": {"en": "Didn't write a new spec for {request}. This product carries on with spec {key}.",
                        "ko": "{request}에서는 새 명세를 쓰지 않았어요. 이 제품은 명세 {key|로} 진행해요."},

    # the design brief's promises as acceptance criteria, in the owner's language
    "build.ux.core_task": {"en": "Given a user opening the app for the first time, When they do the core task "
                                 "({task}), Then it takes at most {steps} clicks or entries.",
                           "ko": "Given 앱을 처음 연 사용자, When 핵심 작업({task})을 하면, Then 클릭·입력 {steps}번 "
                                 "이내에 끝난다."},
    "build.ux.first_glance": {"en": "Given the first screen, When it first opens, Then the eye lands in this order: "
                                    "{order}",
                              "ko": "Given 첫 화면, When 처음 보면, Then 이 순서로 눈에 들어온다: {order}"},
    "build.ux.state": {"en": "Given {screen} · {state}, When the screen shows, Then {shows}",
                       "ko": "Given {screen} · {state}, When 화면을 보면, Then {shows}"},

    "build.question_card.title": {"en": "Question from the engineering team: {key}", "ko": "개발팀 질문: {key}"},
    "build.question_card.why": {"en": "The engineering team can't go on without a business call, and the product agent couldn't "
                                      "answer it from the spec.",
                                "ko": "사업 판단 없이는 개발팀이 진행할 수 없고, 제품기획 에이전트도 명세만으로는 답하지 못한 "
                                      "질문이에요."},
    "build.merge_card.ask": {"en": "Approve the merge", "ko": "머지 승인"},
    "build.merge_card.ask_again": {"en": "Approve the merge (asked again)", "ko": "머지 승인(다시 요청)"},
    "build.merge_card.reask": {"en": "Approve the merge again", "ko": "머지 다시 승인"},
    "build.core_flow.criterion": {"en": "The daily core task works on data shaped like yours, a day with nothing new "
                                        "included",
                                  "ko": "매일 하는 핵심 작업이 실제와 비슷한 데이터로 돌아가요(새 일이 없는 날 포함)"},
    "build.core_flow.not_run": {"en": "Nobody ran it: the build has no check of its daily core task, or the check could "
                                      "not start.",
                                "ko": "아무도 돌려 보지 않았어요. 핵심 작업을 확인하는 스크립트가 없거나 시작하지 "
                                      "못했어요."},
    "build.core_flow.failed": {"en": "It stopped on one of the sample inputs, so the daily task fails as built.",
                               "ko": "샘플 입력 하나에서 멈췄어요. 지금 만든 대로는 매일 하는 작업이 막혀요."},
    "build.core_flow.unrealistic": {"en": "It ran, but not on data shaped like yours.",
                                    "ko": "돌아가긴 했지만 실제와 비슷한 데이터로 돌린 게 아니에요."},
    "build.core_flow.no_quiet_day": {"en": "It ran, but never on a day with nothing new, or that day stopped the user.",
                                     "ko": "돌아가긴 했지만 새 일이 없는 날로는 확인하지 않았거나, 그날 사용자가 "
                                           "막혔어요."},
    "build.core_flow.passed": {"en": "It ran to the end on data shaped like yours.",
                               "ko": "실제와 비슷한 데이터로 끝까지 돌아갔어요."},
    "build.merge_card.ask_fixed": {"en": "Approve the merge (changed as you asked)", "ko": "머지 승인(요청대로 고침)"},
    "build.merge_card.title": {"en": "{ask}: {title}", "ko": "{ask}: {title}"},
    "build.merge_card.title_unmet": {"en": "{ask} (some acceptance criteria unmet): {title}",
                                     "ko": "{ask}(완료 기준 일부 못 채움): {title}"},
    "build.merge_card.where": {"en": "Branch {branch}", "ko": "브랜치 {branch}"},
    "build.merge_card.where_pr": {"en": "Branch {branch} · pull request {pr}", "ko": "브랜치 {branch} · PR {pr}"},
    "build.merge_card.not_pushed": {
        "en": "This version is only on this computer so far. Approving pushes it to the repository, then merges it.",
        "ko": "이 버전은 아직 이 컴퓨터에만 있어요. 승인하면 저장소에 올린 뒤 머지해요."},
    "build.merge_card.copy": {
        "en": "This changes the company's copy in products/{repo}. Your original folder isn't touched; bringing the "
              "change into it is up to you.",
        "ko": "products/{repo}에 있는 회사 사본을 바꿔요. 원래 폴더는 건드리지 않아요. 원래 폴더에 반영하는 건 "
              "오너님이 직접 해요."},
    "build.merge_card.accepted": {"en": "Product agent's verdict: every acceptance criterion is met. {notes}",
                                  "ko": "제품기획 에이전트 판단: 완료 기준을 모두 채웠어요. {notes}"},
    "build.merge_card.unmet": {"en": "Product agent's verdict: some acceptance criteria are unmet. {notes}",
                               "ko": "제품기획 에이전트 판단: 완료 기준 일부를 채우지 못했어요. {notes}"},
    "build.merge_card.screen": {"en": "Screen: {finding}", "ko": "화면: {finding}"},
    "build.merge_card.why": {"en": "Merging changes the product's code, so it always waits for your approval.",
                             "ko": "머지하면 제품 코드가 바뀌어서 꼭 오너님 승인을 받아요."},
    "build.merge_card.no_screens": {"en": "No screen could be captured, so nobody has seen it running.",
                                    "ko": "화면을 한 장도 찍지 못해서, 실행되는 모습을 아무도 보지 못했어요."},
    "build.merge_card.unseen": {"en": "Not seen on screen: {what}. The product agent judged those from the code only.",
                                "ko": "화면으로 보지 못한 곳: {what}. 제품기획 에이전트는 이 부분을 코드로만 판단했어요."},
    "build.merge_card.sample": {"en": "The screens show sample output, not a real run: {why}",
                                "ko": "화면은 실제 실행이 아니라 예시 데이터로 찍었어요: {why}"},

    # ---- after the merge: how people will use it (usable.py)
    "build.use.title": {"en": "Make '{title}' usable", "ko": "'{title}' 쓸 수 있게 하기"},
    "build.use.headline": {"en": "'{title}' is merged, but nobody can use it yet",
                           "ko": "'{title}' 머지는 끝났지만, 아직 아무도 쓸 수 없어요"},
    "build.use.owner_does": {"en": "Pick how people will open it. Your pick becomes a to-do with the steps.",
                             "ko": "사람들이 어떻게 열지 골라 주세요. 고른 방법은 단계가 적힌 내 할 일로 와요."},
    "build.use.why": {"en": "Merged code sits in products/{name} until it runs somewhere.",
                      "ko": "머지한 코드는 어딘가에서 실행하기 전까지 products/{name} 폴더에만 있어요."},
    "build.use.rationale": {
        "en": "The merge put the build into the product's folder. Nothing runs it yet, and the company never deploys "
              "on its own: you run it or put it online yourself, from a to-do.",
        "ko": "머지로 개발 결과가 제품 폴더에 들어갔어요. 아직 실행하는 곳이 없고, 회사가 직접 배포하지는 않아요. 내 "
              "할 일을 보고 직접 실행하거나 올려 주세요."},
    "build.use.option.host": {"en": "Host it on {host}", "ko": "{host}에 올리기"},
    "build.use.option.host_summary": {
        "en": "Anyone with the link can open it. Cost: {cost} (an estimate; check the host's pricing).",
        "ko": "링크만 있으면 누구나 열 수 있어요. 비용: {cost}(추정치라 요금표를 확인해 주세요)."},
    "build.use.option.local": {"en": "Run it on my computer", "ko": "내 컴퓨터에서 실행"},
    "build.use.option.local_summary": {"en": "Free. Only you can open it, at {address}, while it runs.",
                                       "ko": "무료예요. 실행하는 동안 {address} 주소에서 오너님만 열 수 있어요."},
    "build.use.no_address": {"en": "the address it prints", "ko": "화면에 나오는"},
    "build.use.refuse_again": {"en": "J-{id} has nothing to ask again: the question is still waiting, or a way is already "
                                     "picked.",
                               "ko": "J-{id}에는 다시 물을 결정이 없어요. 결정을 기다리고 있거나 이미 방법을 정했어요."},
    "build.use.option.later": {"en": "Not now", "ko": "지금은 안 함"},
    "build.use.option.later_summary": {"en": "Nobody can use it until you pick a way.",
                                       "ko": "방법을 고르기 전까지는 아무도 쓸 수 없어요."},
    "build.use.lead": {"en": "{name} is merged into products/{name}. It doesn't run anywhere yet.",
                       "ko": "products/{name} 폴더에 머지했어요. 아직 실행하는 곳은 없어요."},
    "build.use.needs": {"en": "What the product itself needs:", "ko": "제품에 필요한 것:"},
    "build.use.need_required": {"en": "- {name} (required): {what} Without it: {without}",
                                "ko": "- {name}(필수): {what} 없으면: {without}"},
    "build.use.need_optional": {"en": "- {name} (optional): {what} Without it: {without}",
                                "ko": "- {name}(선택): {what} 없으면: {without}"},
    "build.use.needs_own": {
        "en": "These are the product's own settings. You set them where it runs; Just Ship It never stores them.",
        "ko": "제품 자체의 설정이에요. 실행하는 곳에 오너님이 직접 넣고, Just Ship It은 보관하지 않아요."},
    "build.use.local_heading": {"en": "Run it on your computer, from products/{name}:",
                                "ko": "내 컴퓨터에서 실행하기(products/{name} 폴더에서):"},
    "build.use.from_files": {
        "en": "These steps are copied from the product's own files (README, scripts). Read each one before you run it.",
        "ko": "아래 단계는 제품 파일(README, 스크립트)에서 그대로 옮겨 왔어요. 실행하기 전에 하나씩 읽어 봐 주세요."},
    "build.use.open": {"en": "Then open {address}.", "ko": "그다음 브라우저에서 {address} 주소를 열어 주세요."},
    "build.use.host_heading": {"en": "Host it on {host} ({cost}, an estimate):", "ko": "{host}에 올리기({cost}, 추정치):"},

    # ---- the final report's account of what was merged (usable.py)
    "build.report.heading": {"en": "What was built (checked against the merged code)",
                             "ko": "실제로 만든 것(머지한 코드로 확인)"},
    "build.report.note": {
        "en": "This section comes from the merged code. Where anything else in this report says what the build does, "
              "this section is the one to trust.",
        "ko": "이 부분은 머지한 코드에서 확인한 내용이에요. 보고서의 다른 곳이 개발 결과를 다르게 말하면 이 부분이 "
              "맞아요."},
    "build.report.merged": {"en": "{title}: merged into products/{name} ({base}) on {at}.",
                            "ko": "{title}: {at}에 products/{name}({base})에 머지했어요."},
    "build.report.criteria": {"en": "The product agent checked the branch against the acceptance criteria: {met} of {n} met.",
                              "ko": "제품기획 에이전트가 브랜치를 완료 기준으로 확인했어요. {n}개 중 {met}개를 채웠어요."},
    "build.report.unmet": {"en": "Not met: {criterion}", "ko": "못 채움: {criterion}"},
    "build.report.needs": {"en": "To run for real, it needs: {names}.", "ko": "실제로 쓰려면 이 설정이 필요해요: {names}."},
    "build.report.use": {"en": "How people will use it is decided separately, in Decisions and then My to-dos.",
                         "ko": "사람들이 어떻게 쓸지는 따로 정해요. 결정에서 고르고, 내 할 일에서 해요."},

    # ---- what a spec did with the owner's asks (owner_trace.py)
    "build.trace.directive": {"en": "directive #{n}", "ko": "지시 #{n}"},
    "build.trace.answer": {"en": "answer {ref}", "ko": "답변 {ref}"},
    "build.trace.from_plan": {"en": "Your plan", "ko": "계획"},
    "build.trace.from_directive": {"en": "Your instruction #{n}", "ko": "내 지시 #{n}"},
    "build.trace.from_answer": {"en": "Your answer", "ko": "내 답"},
    "build.trace.from_decision": {"en": "A decision you approved", "ko": "내가 승인한 결정"},
    "build.trace.from_check": {"en": "What you checked", "ko": "내가 직접 확인한 것"},
    "build.trace.from_screen_note": {"en": "Your note on the screen '{screen}'", "ko": "'{screen}' 화면에 남긴 메모"},
    "build.trace.screen_unchanged": {"en": "The spec says it changed a screen for this, but that screen is the same "
                                           "as before.",
                                     "ko": "명세는 화면을 바꿨다고 했지만, 그 화면이 전과 같아요."},
    "build.trace.claimed_missing": {"en": "The spec says it's in, but that requirement isn't in the spec.",
                                    "ko": "넣었다고 했지만 그 요구사항이 명세에 없어요."},
    "build.trace.no_reason": {"en": "The spec left it out without saying why.", "ko": "뺀 이유를 적지 않았어요."},
    "build.trace.unsaid": {"en": "The spec doesn't say what it did with this.", "ko": "명세가 이것을 어떻게 했는지 적지 않았어요."},
    "build.trace.from_skipped_check": {"en": "A check you skipped", "ko": "내가 건너뛴 확인"},
    "build.trace.withdrawn": {"en": "The spec was written from this result, and you reopened the check since.",
                              "ko": "이 결과로 명세를 썼는데, 그 뒤에 확인을 다시 열었어요."},
    "build.trace.late": {"en": "Decided after this spec was written, so the spec doesn't include it.",
                         "ko": "명세를 쓴 뒤에 정해져서 명세에 들어가지 않았어요."},
    "build.trace.none": {"en": "There's no list of what you decided for the product, so nothing was traced.",
                         "ko": "오너님이 정한 기능 목록이 없어 추적하지 않았어요."},
    "build.trace.no_features": {"en": "Nothing you decided is a product feature.",
                                "ko": "오너님이 정한 것 중 제품 기능은 없었어요."},
    "build.trace.all_in": {"en": {"one": "The spec includes the {n} thing you decided.",
                                  "other": "The spec includes all {n} things you decided."},
                           "ko": "오너님이 정한 것 {n}개를 모두 명세에 넣었어요."},
    "build.trace.gaps": {"en": {"one": "Of the {n} thing you decided: {gaps}. Check it before you approve.",
                                "other": "Of the {n} things you decided: {gaps}. Check them before you approve."},
                         "ko": "오너님이 정한 것 {n}개 중 {gaps}. 확인하고 승인해 주세요."},
    "build.trace.dropped": {"en": {"one": "{n} is left out of this version", "other": "{n} are left out of this version"},
                            "ko": "{n}개는 이번 버전에서 뺐어요"},
    "build.trace.missed": {"en": "the spec doesn't cover {n}", "ko": "{n}개는 명세가 다루지 않았어요"},
    "build.trace.and": {"en": ", and ", "ko": ". "},

    # ---- a build that failed its acceptance check is never offered for merge (gate.py, revise.failed_check)
    "build.failed.setup": {"en": "The build failed its check: its setup stopped before anything ran ({line}).",
                           "ko": "개발 결과가 확인을 통과하지 못했어요. 준비 단계에서 멈춰 아무것도 실행되지 않았어요({line})."},
    "build.failed.run": {"en": "The build failed its check: the core daily flow stopped on a sample input.",
                         "ko": "개발 결과가 확인을 통과하지 못했어요. 매일 쓰는 핵심 흐름이 샘플 입력에서 멈췄어요."},
    "build.failed.no_flow": {"en": "The build failed its check: it has no core daily flow script, so nothing could be "
                                   "run.",
                             "ko": "개발 결과가 확인을 통과하지 못했어요. 핵심 흐름 스크립트가 없어 아무것도 실행하지 못했어요."},
    "build.failed.scenarios": {"en": "{n} of {total} must-have scenarios didn't pass.",
                               "ko": "꼭 돼야 하는 시나리오 {total}개 중 {n}개가 통과하지 못했어요."},
    "build.failed.journeys": {"en": "{n} of {total} must-have user journeys didn't work the way a person would use "
                                    "them.",
                              "ko": "꼭 돼야 하는 사용 흐름 {total}개 중 {n}개가 사람이 쓰는 대로 돌아가지 않았어요."},
    "build.failed.review": {"en": "The separate code review asked for fixes.",
                            "ko": "따로 본 코드 검토에서 고칠 점이 나왔어요."},
    "build.failed.criteria": {"en": "Only {met} of {n} acceptance criteria are met.",
                              "ko": "완료 기준 {n}개 중 {met}개만 채웠어요."},
    "build.failed.fix_lead": {"en": "The acceptance check failed. Fix what made it fail:",
                              "ko": "완료 기준 확인을 통과하지 못했어요. 실패한 원인을 고쳐 주세요."},
    "build.failed_card.title": {"en": "The build failed its check: {title}", "ko": "확인을 통과하지 못한 개발: {title}"},
    "build.failed_card.headline": {"en": "The build failed its check, so there is nothing to merge",
                                   "ko": "개발 결과가 확인을 통과하지 못해 머지할 게 없어요"},
    "build.failed_card.owner_does": {"en": "Have it fixed, or drop it.", "ko": "고치게 하거나 버려 주세요."},
    "build.failed_card.recommendation": {
        "en": "Fixing it keeps the work done so far: the engineering team changes the same branch, and it is checked "
              "again before you are asked to merge. Drop it only if this build is no longer wanted.",
        "ko": "고치면 지금까지 만든 것을 살려요. 개발팀이 같은 브랜치를 고치고, 다시 확인한 뒤에 머지를 물어봐요. 이 개발이 "
              "더 필요 없을 때만 버려 주세요."},
    "build.failed_card.fix": {"en": "Fix it", "ko": "고치기"},
    "build.failed_card.fix_summary": {"en": "The engineering team fixes it on the same branch. It needs about ${need} of "
                                            "the project budget.",
                                      "ko": "개발팀이 같은 브랜치에서 고쳐요. 프로젝트 예산이 ${need}쯤 들어요."},
    "build.failed_card.drop": {"en": "Drop it", "ko": "버리기"},
    "build.failed_card.drop_summary": {"en": "Nothing is merged and nothing is rebuilt. The branch stays as it is.",
                                       "ko": "아무것도 머지하지 않고 다시 만들지도 않아요. 브랜치는 그대로 남아요."},
    "build.failed_card.why": {"en": "A build that did not pass its check is never merged.",
                              "ko": "확인을 통과하지 못한 개발 결과는 머지하지 않아요."},
    "build.step.fix_auto": {"en": "The build failed its check. The engineering team is fixing it before you're asked.",
                            "ko": "개발 결과가 확인을 통과하지 못했어요. 오너님께 묻기 전에 개발팀이 먼저 고쳐요."},
    "build.step.failed_asked": {"en": "The build failed its check. Fix it or drop it, in Decisions.",
                                "ko": "개발 결과가 확인을 통과하지 못했어요. 결정에서 고칠지 버릴지 골라 주세요."},
    "build.step.drop_pr_open": {"en": "The build is dropped, but its pull request could not be closed. Close it on GitHub.",
                                "ko": "개발 결과는 버렸지만 PR을 닫지 못했어요. GitHub에서 닫아 주세요."},
    "build.step.dropped": {"en": "You dropped this build. Nothing was merged, and the branch stays.",
                           "ko": "이 개발 결과를 버렸어요. 머지한 건 없고 브랜치는 그대로 남아요."},
    "build.stage.failed_check": {"en": "The build failed its check. Fix it or drop it.",
                                 "ko": "개발 결과가 확인을 통과하지 못했어요. 고칠지 버릴지 정해 주세요."},
    "build.stage.failed_check_next": {"en": "There is nothing to merge. Decide in Decisions.",
                                      "ko": "머지할 건 없어요. 결정에서 골라 주세요."},
    "build.stage.fix_auto_waiting": {"en": "The build failed its check. Its fix waits to start.",
                                     "ko": "개발 결과가 확인을 통과하지 못했어요. 수정을 시작할 차례를 기다려요."},
    "build.refuse.failed_check": {"en": "This build failed its acceptance check, so it can't be merged. Have it fixed, or "
                                        "throw it away and build it again.",
                                  "ko": "이 개발 결과는 완료 기준 확인을 통과하지 못해 머지할 수 없어요. 고치게 하거나 버리고 "
                                        "새로 만들어 주세요."},

    # ---- how far a long build has come (worker.note_progress)
    "build.progress.tasks": {"en": "{done} of {total} planned tasks done.",
                             "ko": "계획한 작업 {total}개 중 {done}개를 마쳤어요."},
    "build.progress.commits": {"en": {"one": "{n} change saved so far.", "other": "{n} changes saved so far."},
                               "ko": "지금까지 변경 {n}개를 저장했어요."},
    # A build that carries on where a stopped run left off (worker.progress_say), never "started building".
    "build.progress.resumed": {"en": "The background process restarted, so the build picks up where it left off.",
                               "ko": "백그라운드 프로세스가 다시 시작해서 하던 곳부터 이어서 만들고 있어요."},
    "build.progress.resumed_tasks": {
        "en": "The background process restarted, so the build picks up where it left off ({done} of {total} planned tasks done).",
        "ko": "백그라운드 프로세스가 다시 시작해서 하던 곳부터 이어서 만들고 있어요({total}개 중 {done}개 마침)."},
    "build.progress.resumed_commits": {
        "en": {"one": "The background process restarted, so the build picks up where it left off ({n} change saved so far).",
               "other": "The background process restarted, so the build picks up where it left off ({n} changes saved so far)."},
        "ko": "백그라운드 프로세스가 다시 시작해서 하던 곳부터 이어서 만들고 있어요(변경 {n}개 저장함)."},
    "build.progress.waited": {"en": "The usage limit reset, so the build picks up where it left off.",
                              "ko": "사용 한도가 풀려서 하던 곳부터 이어서 만들고 있어요."},
    "build.progress.waited_tasks": {
        "en": "The usage limit reset, so the build picks up where it left off ({done} of {total} planned tasks done).",
        "ko": "사용 한도가 풀려서 하던 곳부터 이어서 만들고 있어요({total}개 중 {done}개 마침)."},
    "build.progress.waited_commits": {
        "en": {"one": "The usage limit reset, so the build picks up where it left off ({n} change saved so far).",
               "other": "The usage limit reset, so the build picks up where it left off ({n} changes saved so far)."},
        "ko": "사용 한도가 풀려서 하던 곳부터 이어서 만들고 있어요(변경 {n}개 저장함)."},
    # The lists under a step's details (status.steps).
    "build.step.list.assumptions": {"en": "What it assumed", "ko": "가정한 것"},
    "build.step.list.decisions": {"en": "What it decided", "ko": "정한 것"},

    # ---- who must reach the product, and a product with nothing to open (usable.py)
    "build.use.local_conflict": {"en": "Only you could open it, so {who} couldn't.",
                                 "ko": "오너님 컴퓨터에서만 열려서 열어야 할 사람({who})은 열 수 없어요."},
}
