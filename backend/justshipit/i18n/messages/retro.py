"""An experiment's retro (`services/bet_retro.py`): the notes on what the server changed in the Project Manager's
answer, the reasons a next experiment waits where it does, and the two decision cards the retro files."""
from justshipit.i18n import Entry

MESSAGES: dict[str, Entry] = {
    "retro.verdict.continue": {"en": "Keep going", "ko": "계속하기"},
    "retro.verdict.stop": {"en": "Stop", "ko": "멈추기"},
    "retro.verdict.change": {"en": "Change direction", "ko": "방향 바꾸기"},
    "retro.verdict.routine": {"en": "Make it a routine", "ko": "정기 업무로 돌리기"},
    "retro.activity": {"en": {"one": "{title}: retro says {verdict}, {n} next experiment",
                              "other": "{title}: retro says {verdict}, {n} next experiments"},
                       "ko": "{title}: 회고 판정은 {verdict}, 다음 실험 {n}개"},
    "retro.invalid.no_next_bet": {"en": "The retro proposed no next experiment.",
                                  "ko": "회고에 다음 실험이 하나도 없어요."},

    "retro.note.cause_ids": {"en": "The cause \"{cause}\" cited evidence that isn't in the record, so those ids were "
                                   "removed.",
                             "ko": "원인 후보 '{cause}'에 기록에 없는 근거가 붙어 있어서 그 id를 뺐어요."},
    "retro.note.quote_not_found": {"en": "Removed a quote credited to {person}, because it isn't in anything they said.",
                                   "ko": "{person}의 말로 옮긴 문장이 기록에 없어서 뺐어요."},
    "retro.note.no_quotes": {"en": "No users have said anything yet.", "ko": "아직 사용자가 한 말이 없어요."},
    "retro.note.routine_after_failure": {
        "en": "The run failed or used up its budget, so the retro changes direction instead of making a routine.",
        "ko": "실행이 실패했거나 예산을 다 써서, 정기 업무 대신 방향을 바꾸기로 했어요."},
    "retro.note.routine_no_value": {
        "en": "No one has got value or come back yet, so there's no case for a routine. The verdict is Keep going "
              "instead.",
        "ko": "도움 됨이나 재사용까지 온 사람이 없어서 정기 업무로 돌릴 근거가 없어요. 계속하기로 바꿨어요."},
    "retro.note.routine_cap": {
        "en": {"one": "There's already {n} routine, counting ones waiting for approval, so the verdict is Keep going "
                      "instead of adding another.",
               "other": "There are already {n} routines, counting ones waiting for approval, so the verdict is Keep "
                        "going instead of adding another."},
        "ko": "정기 업무가 이미 {n}개 있어서(승인 대기 포함) 더 늘리지 않고 계속하기로 바꿨어요."},
    "retro.note.routine_default_every": {"en": "The retro didn't say how often to repeat it, so it's every 7 days.",
                                         "ko": "반복 주기를 정하지 않아서 7일마다로 했어요."},
    "retro.note.routine_card": {"en": "The retro asks you whether to make it a routine. Nothing runs until you "
                                      "approve.",
                                "ko": "정기 업무로 돌릴지 결정으로 올렸어요. 승인하기 전에는 돌리지 않아요."},
    "retro.note.to_cycle": {"en": "The next experiments stay in this retro for the Project Manager's next cycle proposal, where you "
                                  "approve them as stages. Nothing new was started or put in your decisions.",
                            "ko": "다음 실험은 이 회고에 남겨 두고 프로젝트 매니저의 다음 사이클 제안에서 단계로 승인받아요. 새로 "
                                  "시작하거나 결정으로 올린 건 없어요."},
    "retro.note.out_of_scope": {"en": "\"{title}\" goes beyond the plan, so it's waiting for your decision.",
                                "ko": "'{title}' 실험은 계획 범위 밖이라 결정으로 올렸어요."},
    "retro.note.channel_kept": {"en": "Didn't drop {channel}. {why}", "ko": "{channel} 채널은 그만두지 않았어요. {why}"},
    "retro.note.channel_unknown": {"en": "There's no such channel.", "ko": "없는 채널이에요."},

    "retro.reason.picked": {"en": "The Project Manager picked this in the retro of \"{bet}\". {reason}",
                            "ko": "'{bet}' 실험 회고에서 프로젝트 매니저가 골랐어요. {reason}"},
    "retro.reason.no_slot": {"en": "This came out of the retro of \"{bet}\" and fits the plan. Every slot is taken, so it "
                                   "waits as a candidate.",
                             "ko": "'{bet}' 실험 회고에서 나왔고 계획 범위 안이에요. 동시에 할 자리가 없어서 후보로 둬요."},
    "retro.reason.out_of_scope": {"en": "This goes beyond the plan, so it's waiting for your decision.",
                                  "ko": "계획 범위 밖이라 결정으로 올렸어요."},

    "retro.usd": {"en": "${usd} equivalent", "ko": "${usd} 환산"},
    "retro.from_retro": {"en": "Suggested by the retro", "ko": "회고의 제안"},

    "retro.routine.seen": {"en": "On the people list, {got} got value and {back} came back",
                           "ko": "명단에서 도움 됨 {got}명, 재사용 {back}명"},
    "retro.routine.title": {"en": "Make it a routine: {title}", "ko": "정기 업무로 돌리기: {title}"},
    "retro.routine.summary": {
        "en": {"one": "\"{bet}\" worked ({seen}). The retro suggests starting a new run of this every day: {what}",
               "other": "\"{bet}\" worked ({seen}). The retro suggests starting a new run of this every {n} days: "
                        "{what}"},
        "ko": "'{bet}' 실험에서 효과가 있었어요({seen}). 이 일을 {n}일마다 새로 실행하려고 해요: {what}"},
    "retro.routine.headline": {"en": "Make this experiment a routine?", "ko": "이 실험을 정기 업무로 돌릴까요?"},
    "retro.routine.owner_does": {"en": "Decide whether to run it on a schedule. If you do, a new run starts on that "
                                       "schedule.",
                                 "ko": "돌릴지 정해 주세요. 돌리면 정해진 주기마다 실행을 새로 시작해요."},
    "retro.routine.why": {"en": "This experiment worked ({seen}).", "ko": "이 실험에서 효과가 있었어요({seen})."},
    "retro.routine.evidence": {"en": "Evidence", "ko": "근거"},
    "retro.routine.people_list": {"en": "People list", "ko": "명단"},
    "retro.routine.every": {"en": "How often", "ko": "주기"},
    "retro.routine.every_value": {"en": {"one": "Every day", "other": "Every {n} days"}, "ko": "{n}일마다"},
    "retro.routine.per_run": {"en": "Budget per run", "ko": "한 번 돌 때 예산"},
    "retro.routine.per_run_bet": {"en": "The experiment's budget", "ko": "실험의 예산"},
    "retro.routine.per_run_default": {"en": "The default project budget", "ko": "프로젝트 기본 예산"},
    "retro.routine.month": {"en": "Most in a month", "ko": "한 달 최대"},
    "retro.routine.month_basis": {"en": {"one": "${usd} x {n} run", "other": "${usd} x {n} runs"},
                                  "ko": "${usd} x {n}번"},
    "retro.routine.yes_summary": {
        "en": {"one": "A new run starts every day, and each run spends usage.",
               "other": "A new run starts every {n} days, and each run spends usage."},
        "ko": "{n}일마다 실행을 새로 시작하고 사용량을 써요."},
    "retro.routine.yes_risk": {"en": "It keeps spending usage until you stop it.", "ko": "멈추기 전까지 사용량을 계속 써요."},
    "retro.routine.no": {"en": "No routine", "ko": "하지 않기"},
    "retro.routine.no_summary": {"en": "No routine. The experiment's retro stays as it is.",
                                 "ko": "정기 업무를 만들지 않아요. 실험 회고는 그대로 남아요."},

    "retro.scope.title": {"en": "Next experiment beyond the plan: {title}", "ko": "계획 범위 밖의 다음 실험: {title}"},
    "retro.scope.summary": {"en": "The next experiment from the retro of \"{bet}\" goes beyond the plan. {pitch}",
                            "ko": "'{bet}' 실험 회고에서 나온 다음 실험이 계획 범위를 넘어요. {pitch}"},
    "retro.scope.headline": {"en": "Add \"{title}\" as a candidate for the next experiment?",
                             "ko": "'{title}' 실험을 다음 실험 후보로 올릴까요?"},
    "retro.scope.owner_does": {"en": "Decide whether to add it. Adding it doesn't start it; you start it yourself.",
                               "ko": "후보로 올릴지 정해 주세요. 올려도 시작은 따로 눌러야 해요."},
    "retro.scope.why": {"en": "The next experiment from the retro goes beyond the plan.",
                        "ko": "회고에서 나온 다음 실험이 계획 범위를 넘어요."},
    "retro.scope.days": {"en": "Time limit", "ko": "기간 한도"},
    "retro.scope.days_value": {"en": {"one": "{n} day", "other": "{n} days"}, "ko": "{n}일"},
    "retro.scope.budget": {"en": "Budget", "ko": "예산"},
    "retro.scope.no_budget": {"en": "Not set", "ko": "정하지 않음"},
    "retro.scope.yes": {"en": "Add as a candidate", "ko": "후보로 올리기"},
    "retro.scope.yes_summary": {"en": "It waits as a candidate and starts when you pick it.",
                                "ko": "후보로 남고, 오너님이 고르면 시작해요."},
    "retro.scope.yes_risk": {"en": "It's outside the plan, so the business may change direction.",
                             "ko": "계획 범위 밖이라 사업 방향이 바뀔 수 있어요."},
    "retro.scope.no": {"en": "Drop it", "ko": "하지 않기"},
    "retro.scope.no_summary": {"en": "Drops the candidate. The plan stays as it is.",
                               "ko": "후보를 버려요. 계획은 그대로예요."},
}
