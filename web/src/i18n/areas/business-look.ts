// The Business page's Look tab (business/Look.tsx): the style pack and main color a business's new products are built
// from. The packs' own names and pitches come from the server (GET /styles).
import type { Area } from "../types";

export const businessLook = {
  "business-look.lead": { en: "Pick the look this business's new products are built from: a style and one main color.",
    ko: "이 사업의 새 제품을 어떤 디자인으로 만들지 골라요. 스타일 하나와 메인 색 하나만 정하면 돼요." },
  "business-look.default": { en: "You haven't picked one yet, so new products use the default, {pack}.",
    ko: "아직 고르지 않아서 새 제품은 기본값인 {pack|으로} 만들어요." },
  "business-look.style": { en: "Style", ko: "스타일" },
  "business-look.styles_label": { en: "Styles to build from", ko: "고를 수 있는 스타일" },
  "business-look.shot": { en: "A sample screen in {pack}", ko: "{pack|으로} 만든 예시 화면" },
  "business-look.color": { en: "Main color", ko: "메인 색" },
  "business-look.color_hint": { en: "One color for buttons, links and the selected item. The style sets every other color.",
    ko: "버튼, 링크, 선택한 항목에 쓰는 색이에요. 나머지 색은 스타일이 정해요." },
  "business-look.swatches": { en: "Suggested colors", ko: "추천 색" },
  "business-look.custom": { en: "Or type a hex color", ko: "직접 입력(hex)" },
  "business-look.bad_hex": { en: "Type a hex color like #3182f6.", ko: "#3182f6 같은 hex 색으로 입력해 주세요." },
  "business-look.preview": { en: "Preview", ko: "미리 보기" },
  "business-look.preview_note": { en: "How a new product uses this color.", ko: "새 제품에서는 이 색을 이렇게 써요." },
  "business-look.preview_failed": { en: "Couldn't preview this color. {reason}", ko: "이 색으로 미리 보지 못했어요. {reason}" },
  "business-look.save": { en: "Save look", ko: "디자인 저장" },
  "business-look.saving": { en: "Saving…", ko: "저장하는 중…" },
  "business-look.saved": { en: "Saved. The next new product uses this look.", ko: "저장했어요. 다음 새 제품부터 이 디자인으로 만들어요." },
  "business-look.only_new": { en: "Only new products use it. Products already built keep their look.",
    ko: "새로 만드는 제품에만 적용해요. 이미 만든 제품은 지금 디자인 그대로예요." },

  // the swatches, named for a screen reader
  "business-look.swatch.blue": { en: "Blue", ko: "파랑" },
  "business-look.swatch.indigo": { en: "Indigo", ko: "남색" },
  "business-look.swatch.violet": { en: "Violet", ko: "보라" },
  "business-look.swatch.teal": { en: "Teal", ko: "청록" },
  "business-look.swatch.green": { en: "Green", ko: "초록" },
  "business-look.swatch.amber": { en: "Amber", ko: "호박색" },
  "business-look.swatch.orange": { en: "Orange", ko: "주황" },
  "business-look.swatch.rose": { en: "Rose", ko: "장미색" },

  // the sample screen fragment the preview draws
  "business-look.mock.title": { en: "Today's orders", ko: "오늘 들어온 주문" },
  "business-look.mock.badge": { en: "3 new", ko: "새 주문 3건" },
  "business-look.mock.link": { en: "View all", ko: "전체 보기" },
  "business-look.mock.order": { en: "Order #{n}", ko: "주문 {n}" },
  "business-look.mock.paid": { en: "Paid", ko: "결제 완료" },
  "business-look.mock.packing": { en: "Packing", ko: "포장 중" },
  "business-look.mock.button": { en: "Ship selected", ko: "선택한 주문 보내기" },
} as const satisfies Area<"business-look">;
