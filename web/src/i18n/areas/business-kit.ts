// The First 10 kit (docs/design/first-ten-kit.md): the section at the top of the People tab (business/Kit.tsx). Who to reach
// today, a message for each, and wording for the owner's profile and invites, written for the owner to copy. The company
// sends nothing. Counts, limits, the reason a text was refused and the reason a person has no draft come from the server
// in the owner's language; nothing here assumes how long either language's words are.
import type { Area } from "../types";

export const businessKit = {
  "business-kit.progress": { en: "{have} of {n} got value", ko: "{n}명 중 {have}명이 도움 됨" },
  "business-kit.lead": {
    en: "The company writes these for you to copy. It sends nothing.",
    ko: "회사가 쓰고, 오너님이 복사해서 직접 보내요. 회사는 아무것도 보내지 않아요." },
  "business-kit.prepare": { en: "Prepare the kit", ko: "키트 준비하기" },
  "business-kit.writing": { en: "Writing…", ko: "쓰는 중…" },
  "business-kit.writing_note": {
    en: "The company is writing. This can take a few minutes. What is already here stays readable.",
    ko: "회사가 쓰고 있어요. 몇 분 걸릴 수 있어요. 이미 있는 글은 그대로 볼 수 있어요." },
  "business-kit.written": { en: "The kit was updated.", ko: "키트가 새로 써졌어요." },
  "business-kit.again_failed": {
    en: "Writing again did not work: {reason} This is the earlier text.",
    ko: "다시 쓰지 못했어요: {reason} 지금 보이는 글은 이전에 쓴 글이에요." },
  "business-kit.cannot": {
    en: "The kit can't be written right now. Writing needs an approved plan on a business that is not archived.",
    ko: "지금은 키트를 쓸 수 없어요. 계획이 승인된, 보관하지 않은 사업이어야 해요." },
  "business-kit.cannot_go": { en: "Open the plan", ko: "계획 열기" },
  "business-kit.first_empty": {
    en: "Nothing is written yet. One click writes a message for each person below, your profile wording and your invite wording.",
    ko: "아직 쓴 글이 없어요. 한 번 누르면 아래 사람마다 메시지, 프로필 문구, 초대 문구를 써요." },

  "business-kit.load_failed": { en: "The kit did not load.", ko: "키트를 불러오지 못했어요." },
  "business-kit.stale_error": {
    en: "The kit could not refresh, so what you see may be out of date.",
    ko: "키트를 새로 불러오지 못했어요. 지금 보이는 내용이 오래됐을 수 있어요." },
  "business-kit.folded_note": { en: "Done. Open it to read the wording again.", ko: "다 됐어요. 열면 문구를 다시 볼 수 있어요." },

  "business-kit.today": { en: "Reach out today", ko: "오늘 연락할 사람" },
  "business-kit.today_empty": { en: "No one to reach today.", ko: "오늘 연락할 사람이 없어요." },
  "business-kit.today_empty_body": {
    en: "Add someone you know, or have the company look for people.",
    ko: "아는 사람을 직접 추가하거나, 회사가 사람을 찾게 할 수 있어요." },
  "business-kit.add_person": { en: "Add someone you know", ko: "아는 사람 추가하기" },
  "business-kit.find": { en: "Look for people", ko: "사람 찾기" },
  "business-kit.finding": { en: "Asking…", ko: "요청하는 중…" },
  "business-kit.find_queued": {
    en: "The company is looking for people. New ones appear in the list.",
    ko: "회사가 사람을 찾고 있어요. 찾으면 명단에 올라와요." },
  "business-kit.find_today": {
    en: "The company already looked today. Ask again tomorrow.",
    ko: "회사가 오늘 이미 찾아봤어요. 내일 다시 요청해 주세요." },

  "business-kit.draft": { en: "Message draft", ko: "메시지 초안" },
  "business-kit.draft_for": { en: "message draft for {name}", ko: "{name}에게 보낼 메시지 초안" },
  "business-kit.reached": { en: "I reached out", ko: "연락했어요" },
  "business-kit.reached_for": { en: "I reached out to {name}", ko: "{name}에게 연락했어요" },
  "business-kit.reached_saving": { en: "Saving…", ko: "저장하는 중…" },
  "business-kit.reached_done": { en: "{name} moved to {stage}.", ko: "{name}: {stage} 단계로 옮겼어요." },
  "business-kit.write_draft": { en: "Write a draft", ko: "초안 쓰기" },
  "business-kit.write_draft_for": { en: "Write a draft for {name}", ko: "{name}의 초안 쓰기" },
  "business-kit.rewrite": { en: "Write again", ko: "다시 쓰기" },
  "business-kit.rewrite_for": { en: "Write again: {what}", ko: "다시 쓰기: {what}" },
  "business-kit.write": { en: "Write", ko: "쓰기" },
  "business-kit.write_for": { en: "Write: {what}", ko: "쓰기: {what}" },

  "business-kit.count": { en: "{n} of {max} characters", ko: "{max}자 중 {n}자" },
  "business-kit.from": {
    en: { one: "Written from {n} thing they said", other: "Written from {n} things they said" },
    ko: "그 사람이 한 말 {n}개를 바탕으로 썼어요" },
  "business-kit.from_none": { en: "Written from what the list says about them.", ko: "명단에 적힌 내용을 바탕으로 썼어요." },
  "business-kit.in_lang": { en: "Written in {lang}", ko: "쓴 언어: {lang}" },
  "business-kit.made": { en: "Written {date}", ko: "{date}에 썼어요" },
  "business-kit.slot_of": { en: "{part} ({slot})", ko: "{part} ({slot})" },
  "business-kit.stale": { en: "Written before the latest change.", ko: "최근 바뀐 내용이 반영되기 전에 쓴 글이에요." },
  "business-kit.guess": {
    en: "Written from the company's guess about your customers, not from what you said.",
    ko: "오너님이 말한 내용이 아니라 고객에 대한 회사의 짐작을 바탕으로 썼어요." },
  "business-kit.guess_all": {
    en: "All of these were written from the company's guess about your customers, not from what you said.",
    ko: "아래 글은 모두 오너님이 말한 내용이 아니라 고객에 대한 회사의 짐작을 바탕으로 썼어요." },
  "business-kit.refused": { en: "Not written", ko: "쓰지 못했어요" },
  "business-kit.unwritten": { en: "Not written yet.", ko: "아직 쓰지 않았어요." },

  "business-kit.profile": { en: "Profile wording", ko: "프로필 문구" },
  "business-kit.profile_help": {
    en: "For your own accounts, in three sizes so one fits wherever you sign up.",
    ko: "오너님 계정에 쓸 문구예요. 크기가 세 가지라서 어디에 가입하든 맞는 걸 골라 쓸 수 있어요." },
  "business-kit.profile_product_only": {
    en: "You said nothing about yourself in the kickoff chat, so this describes the product and who it is for.",
    ko: "시작 대화에서 오너님 이야기를 하지 않으셔서, 제품과 누구를 위한 것인지만 담았어요." },
  "business-kit.slot.profile.line": { en: "One line", ko: "한 줄" },
  "business-kit.slot.profile.short": { en: "Short", ko: "짧게" },
  "business-kit.slot.profile.about": { en: "About", ko: "소개" },

  "business-kit.invite": { en: "Invite wording", ko: "초대 문구" },
  "business-kit.invite_help": {
    en: "Invites one person to try the product. Pick the one that fits who you are writing to.",
    ko: "한 사람을 제품 써 보기에 초대하는 글이에요. 받는 사람에게 맞는 걸 골라 쓰세요." },
  "business-kit.slot.invite.known": { en: "For someone you know", ko: "아는 사람에게" },
  "business-kit.slot.invite.problem": { en: "For someone who has the problem", ko: "그 문제를 겪는 사람에게" },
  "business-kit.link_ready": { en: "The link in these texts is your place:", ko: "글에 넣은 링크는 오너님의 이 장소예요:" },
  "business-kit.link_none": { en: "Add your place to put a link here.", ko: "내 장소를 추가하면 글에 링크를 넣을 수 있어요." },
  "business-kit.link_go": { en: "Add your place", ko: "장소 추가하기" },
} as const satisfies Area<"business-kit">;
