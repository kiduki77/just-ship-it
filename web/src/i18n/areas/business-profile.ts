// The Business page's "Your customers" card (business/Customers.tsx): the customer description with who stands behind
// each line. The line names and every code label come from the server (GET /businesses/{id}/profile), so none is here.
import type { Area } from "../types";

export const businessProfile = {
  "business-profile.title": { en: "Your customers", ko: "고객 설명" },

  "business-profile.right": { en: "Right", ko: "맞아요" },
  "business-profile.not_right": { en: "Not right", ko: "아니에요" },
  "business-profile.change": { en: "Change", ko: "고치기" },
  "business-profile.tell": { en: "Tell us", ko: "알려 주기" },
  // the same buttons for a screen reader, which hears three of each in a row
  "business-profile.right_for": { en: "Right: {line}", ko: "맞아요: {line}" },
  "business-profile.not_right_for": { en: "Not right: {line}", ko: "아니에요: {line}" },
  "business-profile.change_for": { en: "Change: {line}", ko: "고치기: {line}" },
  "business-profile.tell_for": { en: "Tell us: {line}", ko: "알려 주기: {line}" },

  "business-profile.details": { en: "Details", ko: "자세히" },
  "business-profile.uses": { en: "The company uses: {list}", ko: "회사가 쓰는 값: {list}" },
  "business-profile.uses_none": {
    en: "This is in your own words only, so the company can't pick places from it. Pick from the list too.",
    ko: "직접 쓴 말만 있어서 회사가 곳을 고르는 데 쓰지 못해요. 목록에서도 골라 주세요." },
  "business-profile.unknown": {
    en: "Not known yet. Promotion and the people finder skip places that depend on it.",
    ko: "아직 몰라요. 홍보와 사람 찾기가 이 값이 필요한 곳은 건너뛰어요." },
  "business-profile.stale": {
    en: "Your plan changed on {date}, after this was written. Is it still right?",
    ko: "이 설명을 쓴 뒤인 {date}에 계획이 바뀌었어요. 지금도 맞나요?" },

  "business-profile.backfilled": { en: "The Project Manager wrote these from your plan. Check each one.", ko: "프로젝트 매니저가 계획을 보고 쓴 내용이에요. 하나씩 확인해 주세요." },
  "business-profile.reading": { en: "The Project Manager is reading your answer.", ko: "프로젝트 매니저가 답을 읽고 있어요." },
  "business-profile.not_written": { en: "The company hasn't written this yet.", ko: "회사가 아직 이 설명을 쓰지 못했어요." },
  "business-profile.answer": { en: "Your answer to the Project Manager", ko: "프로젝트 매니저에게 보낼 답" },
  "business-profile.answer_ph": { en: "A few words are enough.", ko: "몇 마디면 충분해요." },
  "business-profile.send": { en: "Send", ko: "보내기" },
  "business-profile.sending": { en: "Sending…", ko: "보내는 중…" },

  "business-profile.instead": { en: "What is it instead?", ko: "그럼 어떤 게 맞나요?" },
  "business-profile.sentence": { en: "Or in your own words", ko: "직접 쓰기" },
  "business-profile.sentence_hint": {
    en: "The company picks places from the choices above. Your words are kept next to them.",
    ko: "회사는 위에서 고른 항목으로 곳을 정해요. 직접 쓴 말은 그 옆에 남겨요." },
  "business-profile.save": { en: "Save", ko: "저장" },
  "business-profile.saving": { en: "Saving…", ko: "저장하는 중…" },
  "business-profile.cancel": { en: "Cancel", ko: "취소" },
  "business-profile.nothing_picked": { en: "Pick one, or write it in your own words.", ko: "하나 고르거나 직접 적어 주세요." },

  // {line} is the server's line name, always followed by a fixed word, so no Korean particle depends on it
  "business-profile.done.right": { en: "Confirmed: {line}.", ko: "{line} 항목을 맞다고 확인했어요." },
  "business-profile.done.not_right": { en: "Marked not right: {line}.", ko: "{line} 항목이 맞지 않다고 표시했어요." },
  "business-profile.done.change": { en: "Changed: {line}.", ko: "{line} 항목을 고쳤어요." },
  "business-profile.undo": { en: "Undo", ko: "되돌리기" },
  "business-profile.undo_for": { en: "Undo: {line}", ko: "되돌리기: {line}" },

  "business-profile.load_failed": { en: "Couldn't load your customers.", ko: "고객 설명을 불러오지 못했어요." },
  "business-profile.refresh_failed": {
    en: "Couldn't refresh this card. What you see may be out of date.",
    ko: "새로 불러오지 못했어요. 보이는 내용이 오래됐을 수 있어요." },

  // links from other screens to the card
  "business-profile.check": { en: "Check it", ko: "확인해 주세요" },
  "business-profile.go": { en: "Go to Your customers", ko: "고객 설명으로 가기" },
  "business-profile.from_plan": { en: "See your customer description", ko: "고객 설명 보기" },
} as const satisfies Area<"business-profile">;
