---
version: 1
slug: "web-src-main-tsx"
primary_target: "web/src/main.tsx"
related_targets: ["web/src/pages/Home.tsx","web/src/pages/Approvals.tsx","web/src/pages/MyTasks.tsx","web/src/pages/Tasks.tsx","web/src/pages/Business.tsx"]
---

# Owner console (all owner and company screens)

Mode: Operate. Audience: the solo founder checking in a few times a day on a desktop browser (1280–1920px wide), sometimes after days away. Job: in one look know what the company is doing, what it spent, what needs the owner, and how close the business is to its first users; then decide or act without hunting.

Revised by the owner's approval of the 2026-11-04 critic findings (F1-F12): every screen opens on one status sentence and the next action, numbers only when they need action; no tile rows, icon squares, chip piles or charts over no data; red for failure only. This supersedes the tile-row parts of the 2026-11-03 pin below.

Pinned by the owner (2026-11-03): borrow Paperclip's UX (sidebar IA with Work / Agents / Company groups, dashboard of metric cards + agent run cards + 14-day charts + recent activity/tasks, decision queue with tabs and inline actions, status board) and the density of the owner's reference image (light grey canvas, white bordered cards, metric tiles with icon and link, view switcher Kanban/List, status columns with counts, cards with ID, priority flag, dates). Light theme by default, dark as an opt-in setting. Replace the old "calm, mostly empty" look.

Keep: decision card content and approval flow (recommendation first, typed strong-approve, reversibility), confidence vocabulary, PRODUCT.md terminology, Korean UI, Ctrl+K and j/k, side panel for REQ/T.

## Direction contract

THESIS: A company control room you can read in ten seconds, written like a report to a superior: every owner screen opens on one sentence that answers "what needs me, what broke, is it moving", then the work itself laid out by state. Refuses the category default of a lone to-do list on white space.

OWN-WORLD: Light neutral ground (#f4f5f7) with white panels and 1px hairline borders, no decorative shadows; near-black primary buttons; one indigo accent for selection and links; state colours (blue running, amber waiting, green done, red failed/blocked, violet review) do real jobs on chips, column headers and chart series. Pretendard 14px body, 600 weight for titles, tabular numerals and mono only for IDs, money and times.

STORY: The owner lands on the dashboard, sees four numbers and which agents are working, scans the 14-day bars for trouble, opens the decision board, decides from the column "지금 결정", checks spend against the month cap, and leaves knowing nothing is stuck.

FIRST VIEWPORT: Left sidebar 240px (brand + business switcher, search, 대시보드/결정/내 할 일, groups 일, 에이전트 with live dots, 회사). Top bar: breadcrumb left, live status and theme right. Main fills the width: page title + one-line summary; alert banner if any; 4 metric tiles in one row (결정 대기, 진행 중 업무, 이번 달 사용액 with cap bar, 두 번 이상 쓴 사람/목표); then working-agent cards; then 14-day charts in a row of three; then 오늘 할 일 and 최근 활동 side by side.

FORM: Standard web shell with status board (kanban) as the signature move: every collection that has states (decisions, owner to-dos, tasks) can be read as columns with counts. Pinned by the owner: the roll was run for the record (assigned its own index 3) and the owner's pinned direction overrides it. seed:604715401e4875e3

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
