// "Places and rules" (docs/design/channel-rules.md): the list on the Numbers tab of every place the owner could post, and
// whether the company read its rules at the place itself, plus the mark on a post card. A place whose rules were not read
// gets no post written. The reason, the name and the rules text come from the server in the owner's language; the state
// is always words, never colour alone. The words "Rules checked" / "Rules unchecked" are the glossary's (`term.rules_*`).
import type { Area } from "../types";

export const businessRules = {
  "business-rules.title": { en: "Places and rules", ko: "채널과 규칙" },
  "business-rules.lead": {
    en: "Each place where you could post, and whether the company read its rules at the place itself. You post by hand. The company never posts for you.",
    ko: "글을 올릴 수 있는 채널마다, 회사가 규칙을 그 채널에서 직접 읽었는지 보여 줘요. 올리는 건 오너님이 직접 해요. 회사는 대신 올리지 않아요." },
  "business-rules.checked_dated": { en: "Rules checked · {date}", ko: "규칙 확인됨 · {date}" },
  "business-rules.unchecked_lead": {
    en: "The company writes nothing for these. You can still post there yourself after reading their rules.",
    ko: "이 채널에는 회사가 글을 쓰지 않아요. 규칙을 직접 읽은 뒤 오너님이 직접 올릴 수는 있어요." },
  "business-rules.no_checked": {
    en: "No place for this market has its rules checked yet, so the company writes no posts.",
    ko: "이 사업의 시장에는 규칙을 확인한 채널이 아직 없어서, 회사가 쓰는 글이 없어요." },
  "business-rules.none": {
    en: "No places for this business's market yet.",
    ko: "이 사업의 시장에 맞는 채널이 아직 없어요." },
  "business-rules.load_failed": { en: "The places did not load.", ko: "채널 목록을 불러오지 못했어요." },
  "business-rules.stale_error": {
    en: "The places could not refresh, so what you see may be out of date.",
    ko: "채널 목록을 새로 불러오지 못했어요. 지금 보이는 내용이 오래됐을 수 있어요." },

  "business-rules.source": { en: "Rules at the source", ko: "규칙 원문" },
  "business-rules.background": { en: "Background page, not its rules", ko: "참고 페이지 (이 채널의 규칙이 아니에요)" },
  "business-rules.link_sr": { en: " ({place}, opens in a new tab)", ko: " ({place}, 새 탭에서 열려요)" },
  "business-rules.does.points": {
    en: "The company writes points. You write the post yourself.",
    ko: "회사가 글감을 정리해 줘요. 글은 오너님이 직접 써요." },
  "business-rules.does.draft": {
    en: "The company writes a draft for you to edit.",
    ko: "회사가 초안을 써 줘요. 오너님이 고쳐서 올려요." },
  "business-rules.rules_text": { en: "Rules text", ko: "규칙 내용" },
  "business-rules.notes": { en: "Notes for reading its rules yourself", ko: "규칙을 직접 읽을 때 볼 점" },
  "business-rules.no_fit": { en: "Does not fit this business", ko: "이 사업에는 맞지 않아요" },
  "business-rules.needs": { en: "Before you post here: {items}", ko: "올리기 전에 끝낼 일: {items}" },
  "business-rules.dropped": { en: "Dropped for this business", ko: "이 사업에서는 뺀 채널이에요" },

  "business-rules.card.see": { en: "Places and rules", ko: "채널과 규칙" },
  "business-rules.card.own": {
    en: "Your own place: no outside rules apply.",
    ko: "오너님의 사이트나 저장소라서, 바깥 규칙은 없어요." },
} as const satisfies Area<"business-rules">;
