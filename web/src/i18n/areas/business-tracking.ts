// Tracking (docs/design/tracking.md): the "Your link" block on a post card, the "how did they hear about you?" field in
// the people record and the person to-do, and the "Where people came from" section of the Numbers tab
// (business/Tracking.tsx, business/Places.tsx, business/HeardField.tsx, components/yourlink.tsx). The words a figure's
// basis carries ("read", "told", "you typed", "at least"), the row names and the sign-up question come from the server.
import type { Area } from "../types";

export const businessTracking = {
  // the owner's places, by kind
  "business-tracking.door.site": { en: "Your site", ko: "내 사이트" },
  "business-tracking.door.store": { en: "Your store page", ko: "내 가게 페이지" },
  "business-tracking.door.map": { en: "Your map listing", ko: "내 지도 등록" },
  "business-tracking.door.package": { en: "Your package page", ko: "내 패키지 페이지" },
  "business-tracking.door.repo": { en: "Your repository", ko: "내 저장소" },

  // the card's "Your link" block
  "business-tracking.link.tagged": {
    en: "This place hides where visitors come from, so the link carries {tag}. Your analytics shows it as the source.",
    ko: "이곳은 방문자가 어디서 왔는지 숨겨요. 그래서 링크에 태그를 달았어요({tag}). 분석 도구에서 유입 경로로 볼 수 있어요." },
  "business-tracking.link.plain": {
    en: "On {place} the link is the plain address. A tag there adds nothing the company can read, and readers tend to distrust tracking links.",
    ko: "{place}에 올릴 때는 태그 없는 주소를 써요. 여기서는 태그로 얻는 게 없고, 추적 링크를 의심하는 사람도 많아요." },
  "business-tracking.link.plain_dropped": {
    en: "The link is the plain address, because nothing at this address can read a tag back. When someone says where they saw you, tap +1 on the Numbers tab.",
    ko: "이 주소는 태그를 읽어 낼 수 없어서 그대로 썼어요. 어디서 봤다고 말하는 사람이 있으면 숫자 탭에서 +1로 세 주세요." },
  "business-tracking.link.say_head": { en: "Say this in your post", ko: "글에 이렇게 적어 주세요" },
  "business-tracking.link.say_copy": { en: "the line to say", ko: "적을 문구" },
  "business-tracking.link.say_help": {
    en: "This place gives no room for a link. When someone mentions it, tap +1 on the Numbers tab.",
    ko: "이곳은 링크를 걸 수 없어요. 누가 그렇게 말하면 숫자 탭에서 +1을 눌러 주세요." },
  "business-tracking.link.no_door": {
    en: "Add your site, store page or map listing on the Numbers tab, and the next cards get a link.",
    ko: "숫자 탭에서 내 사이트, 가게 페이지, 지도 등록 중 하나를 추가하면 다음 카드부터 링크가 붙어요." },
  "business-tracking.link.go_places": { en: "Open Your places", ko: "내 장소 열기" },
  "business-tracking.link.go_numbers": { en: "Open the Numbers tab", ko: "숫자 탭 열기" },

  // how did they hear about you?
  "business-tracking.heard.label": { en: "How did they hear about you?", ko: "어디서 알게 됐대요?" },
  "business-tracking.heard.words": { en: "Their words", ko: "그 사람이 한 말" },
  "business-tracking.heard.words_help": {
    en: "Optional, up to {n} characters, as they said it. No email address or phone number.",
    ko: "선택, {n}자까지 들은 그대로 적어요. 이메일 주소나 전화번호는 적지 마세요." },
  "business-tracking.heard.keep": { en: "Leave it as it is and nothing is saved.", ko: "그대로 두면 아무것도 저장하지 않아요." },
  "business-tracking.heard.on_row": { en: "Heard about you from {label}", ko: "알게 된 곳: {label}" },
  "business-tracking.heard.step": { en: "where they heard", ko: "알게 된 곳" },
  "business-tracking.heard.saved": { en: "Saved where they heard about you.", ko: "알게 된 곳을 저장했어요." },
  "business-tracking.heard.loading": { en: "Loading choices…", ko: "고를 곳을 불러오는 중…" },

  // the Numbers tab section
  "business-tracking.came.title": { en: "Where people came from", ko: "사람들이 온 곳" },
  "business-tracking.came.lead": {
    en: "Posts, visits, people and who got value, by the place they came from. Each number says whether it was read, told to you or typed by you.",
    ko: "글, 방문, 명단의 사람, 도움 됨까지 온 사람을 온 곳별로 보여 줘요. 숫자마다 읽은 건지, 들은 건지, 직접 입력한 건지 적혀 있어요." },
  "business-tracking.came.too_few": {
    en: "Too few people to tell channels apart yet. These are counts, not a ranking.",
    ko: "아직 사람이 적어서 채널끼리 비교하기 일러요. 순위가 아니라 개수예요." },
  "business-tracking.came.too_few_n": {
    en: "{n} of {min} answers so far, counting what people said and what you tapped with +1.",
    ko: "지금까지 답 {n}/{min}개예요. 사람들이 말한 것과 +1로 센 것을 합친 수예요." },
  "business-tracking.came.first": { en: "First {n} users", ko: "첫 {n}명" },
  "business-tracking.came.first_none": { en: "None yet.", ko: "아직 없어요." },
  "business-tracking.came.first_far": { en: "{have} of {n} so far.", ko: "지금 {have}/{n}명이에요." },
  "business-tracking.came.first_done": { en: "The {n}th user came on {date}.", ko: "{n}번째 사용자가 {date}에 왔어요." },
  "business-tracking.came.first_est": {
    en: { one: "The day is estimated for {n} person: their move wasn't logged, so it is when their row last changed.",
      other: "The day is estimated for {n} people: their moves weren't logged, so it is when their rows last changed." },
    ko: "{n}명은 옮긴 기록이 없어서 명단이 마지막으로 바뀐 날로 짐작했어요." },
  "business-tracking.came.mix": { en: "{n} {label}", ko: "{label} {n}명" },

  "business-tracking.came.caption": {
    en: "Where people came from: posts, visits, sign-ups, people on the list, people who got value, and people you were told about, one row per place.",
    ko: "온 곳별 글, 방문, 가입, 명단의 사람, 도움 됨까지 온 사람, 직접 들은 수예요." },
  "business-tracking.came.col.place": { en: "Place", ko: "온 곳" },
  "business-tracking.came.col.posts": { en: "Posts", ko: "글" },
  "business-tracking.came.col.visits": { en: "Visits", ko: "방문" },
  "business-tracking.came.col.signups": { en: "Sign-ups", ko: "가입" },
  "business-tracking.came.col.people": { en: "On the list", ko: "명단" },
  "business-tracking.came.col.got_value": { en: "Got value", ko: "도움 됨" },
  "business-tracking.came.col.told": { en: "Told you", ko: "직접 들은 수" },
  "business-tracking.came.legend": {
    en: "Told you counts people who are not on your list and said they came from that place. It is never added to On the list.",
    ko: "직접 들은 수는 명단에 없고 그곳에서 왔다고 말한 사람이에요. 명단의 사람 수에는 더하지 않아요." },
  "business-tracking.came.basis_told": { en: "told", ko: "들음" },
  "business-tracking.came.none": { en: "none", ko: "없음" },
  "business-tracking.came.unknown": { en: "unknown", ko: "모름" },
  "business-tracking.came.na": { en: "not applicable", ko: "해당 없음" },
  "business-tracking.came.window": { en: "last 14 days, as of {date}", ko: "{date} 기준 최근 14일" },
  "business-tracking.came.as_of": { en: "as of {date}", ko: "{date} 기준" },

  "business-tracking.came.analytics": { en: "Add from your analytics", ko: "분석 도구 숫자 입력" },
  "business-tracking.came.analytics_for": { en: "Add from your analytics for {label}", ko: "{label} 분석 도구 숫자 입력" },
  "business-tracking.came.analytics_help": {
    en: "Type what your own analytics shows for each tag, as of today. A box you leave alone keeps its value.",
    ko: "내 분석 도구에 나온 태그별 숫자를 오늘 기준으로 적어 주세요. 건드리지 않은 칸은 그대로 둬요." },
  "business-tracking.came.analytics_visits": { en: "Visits for {tag}", ko: "{tag} 방문" },
  "business-tracking.came.analytics_signups": { en: "Sign-ups for {tag}", ko: "{tag} 가입" },
  "business-tracking.came.analytics_save": { en: "Save", ko: "저장" },
  "business-tracking.came.analytics_saving": { en: "Saving…", ko: "저장하는 중…" },
  "business-tracking.came.analytics_nothing": { en: "Change a number to save it.", ko: "숫자를 바꿔야 저장할 수 있어요." },
  "business-tracking.came.analytics_bad": { en: "Use a whole number, 0 or more.", ko: "0 이상의 정수로 적어 주세요." },
  "business-tracking.came.analytics_saved": { en: "Saved for {tag}.", ko: "{tag} 숫자를 저장했어요." },

  "business-tracking.tally.plus": { en: "+1 to {label}", ko: "{label}에 +1" },
  "business-tracking.tally.minus": { en: "Take one back from {label}", ko: "{label}에서 하나 빼기" },
  "business-tracking.tally.today": { en: "{n} today", ko: "오늘 {n}" },
  "business-tracking.tally.failed": { en: "Couldn't count that for {label}. {reason}", ko: "{label}에서 온 사람을 세지 못했어요. {reason}" },
  "business-tracking.tally.new_title": { en: "Count someone who isn't on your list", ko: "명단에 없는 사람 세기" },
  "business-tracking.tally.new_help": {
    en: "Someone at the counter or in a message who told you where they heard about you. Nothing about them is stored.",
    ko: "가게에서나 메시지로 어디서 알게 됐는지 말해 준 사람이에요. 그 사람에 대한 정보는 저장하지 않아요." },
  "business-tracking.tally.new_pick": { en: "Where they came from", ko: "어디서 왔나요" },
  "business-tracking.tally.new_pick_ph": { en: "Pick a place", ko: "곳을 골라 주세요" },

  "business-tracking.hosts.title": { en: "GitHub visits by website", ko: "웹사이트별 GitHub 방문" },
  "business-tracking.hosts.note": {
    en: "GitHub keeps the last 14 days. Each line is the newest reading, and the total is at least what the daily reads add up to without counting a day twice.",
    ko: "GitHub는 최근 14일만 알려 줘요. 줄마다 가장 최근에 읽은 값이고, 합계는 같은 날을 두 번 세지 않은 최소 값이에요." },
  "business-tracking.hosts.shared": {
    en: "{names}: the same website, so the visits are not split between them.",
    ko: "{names} 모두에 걸친 방문이라 나누지 않았어요." },
  "business-tracking.hosts.organic": { en: "Found you on their own", ko: "스스로 찾아옴" },
  "business-tracking.hosts.total": { en: "in all", ko: "합계" },

  "business-tracking.traffic.unreadable": { en: "GitHub visits are unknown, so none are shown.", ko: "GitHub 방문 수를 읽지 못해서 보여 줄 수 없어요." },
  "business-tracking.traffic.stale": {
    en: "GitHub numbers as of {date}. The daily read has not run since.",
    ko: "GitHub 숫자는 {date} 기준이에요. 그 뒤로 하루 한 번 읽는 작업이 돌지 않았어요." },

  "business-tracking.came.empty": { en: "Nothing to count yet", ko: "아직 셀 게 없어요" },
  "business-tracking.came.empty_body": {
    en: "Your next post card carries a link, and the people list asks how each person heard.",
    ko: "다음 홍보 카드에는 내 링크가 붙고, 명단에서는 사람마다 어디서 알게 됐는지 물어봐요." },
  "business-tracking.came.no_door": {
    en: "Add your site, store page or map listing so post cards carry a link.",
    ko: "내 사이트, 가게 페이지, 지도 등록을 추가하면 홍보 카드에 링크가 붙어요." },
  "business-tracking.came.stale_error": {
    en: "Couldn't refresh this section. These are the last values it loaded.",
    ko: "이 부분을 새로 불러오지 못했어요. 마지막으로 불러온 값이에요." },
  "business-tracking.came.load_failed": { en: "Couldn't load where people came from.", ko: "사람들이 온 곳을 불러오지 못했어요." },

  // Your places
  "business-tracking.places.title": { en: "Your places", ko: "내 장소" },
  "business-tracking.places.lead": {
    en: "The addresses your post cards link to. Where the place keeps a tag, the link carries one.",
    ko: "홍보 카드가 연결하는 주소예요. 태그를 읽을 수 있는 곳에는 링크에 태그가 붙어요." },
  "business-tracking.places.derived": {
    en: "From the GitHub address of this business. It can't be changed here.",
    ko: "이 사업에 연결한 GitHub 주소에서 가져왔어요. 여기서는 바꿀 수 없어요." },
  "business-tracking.places.param": { en: "Tag name: {param}", ko: "태그 이름: {param}" },
  "business-tracking.places.no_param": { en: "No tag: nothing here can read one back.", ko: "태그 없음: 이곳은 태그를 읽어 낼 수 없어요." },
  "business-tracking.places.none": { en: "No places yet.", ko: "아직 장소가 없어요." },
  "business-tracking.places.edit": { en: "Edit places", ko: "장소 고치기" },
  "business-tracking.places.add": { en: "Add a place", ko: "장소 추가" },
  "business-tracking.places.remove": { en: "Remove place {n}", ko: "{n}번째 장소 빼기" },
  "business-tracking.places.remove_short": { en: "Remove", ko: "빼기" },
  "business-tracking.places.kind": { en: "Kind", ko: "종류" },
  "business-tracking.places.url": { en: "Address", ko: "주소" },
  "business-tracking.places.param_label": { en: "Tag name", ko: "태그 이름" },
  "business-tracking.places.param_std": { en: "Standard for this kind", ko: "종류에 맞는 기본값" },
  "business-tracking.places.param_none": { en: "No tag", ko: "태그 없음" },
  "business-tracking.places.param_help": {
    en: "Your analytics reads the tag from this part of the address. Keep the standard unless it reads only utm_source.",
    ko: "분석 도구가 주소의 이 부분에서 태그를 읽어요. utm_source만 읽는 도구가 아니면 기본값으로 두세요." },
  "business-tracking.places.save": { en: "Save places", ko: "장소 저장" },
  "business-tracking.places.saving": { en: "Saving…", ko: "저장하는 중…" },
  "business-tracking.places.saved": { en: "Saved your places.", ko: "장소를 저장했어요." },
  "business-tracking.places.group": { en: "Place {n}", ko: "{n}번째 장소" },
  "business-tracking.places.offer": { en: "Your repository lists {host}. Use it as your site?", ko: "저장소에 적힌 사이트가 있어요({host}). 내 사이트로 쓸까요?" },
  "business-tracking.places.offer_use": { en: "Use {host}", ko: "{host} 쓰기" },

  // Ask on your sign-up form
  "business-tracking.signup.help": {
    en: "Add this question to your own sign-up form. When people answer, count them here with +1.",
    ko: "내 가입 화면에 이 질문을 넣어 보세요. 답이 쌓이면 여기서 +1로 세 주세요." },
  "business-tracking.signup.question": { en: "Question", ko: "질문" },
  "business-tracking.signup.options": { en: "Answers to offer", ko: "고를 수 있는 답" },
  "business-tracking.signup.copy_question": { en: "the question", ko: "질문" },
  "business-tracking.signup.copy_options": { en: "the answers", ko: "답 목록" },

  // the Reactions list used to show the GitHub referrers; the metric's name as the server words it, to tell them apart
  "business-tracking.referrer_name": { en: "Referrer", ko: "유입 경로" },
} as const satisfies Area<"business-tracking">;
