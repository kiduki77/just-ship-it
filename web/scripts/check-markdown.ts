// How agent Markdown renders, checked without a browser: `npm run check`.
import assert from "node:assert/strict";
import { markdownHtml, servedImage } from "../src/markdown.ts";

const html = (text: string) => markdownHtml(text).trim();

// Round 6 (미영's Kakao document): "빈칸은 **[메뉴 이름]**과 **[마감 시간]**" showed the first pair's asterisks.
assert.equal(html("빈칸은 **[메뉴 이름]**과 **[마감 시간]** 두 가지"),
  "<p>빈칸은 <strong>[메뉴 이름]</strong>과 <strong>[마감 시간]</strong> 두 가지</p>");
assert.equal(html("가게 이름은**[미영 반찬]**이에요"), "<p>가게 이름은<strong>[미영 반찬]</strong>이에요</p>");
assert.equal(html("**'주문'**을 눌러요"), "<p><strong>&#39;주문&#39;</strong>을 눌러요</p>");
assert.equal(html("**주문**을 눌러요"), "<p><strong>주문</strong>을 눌러요</p>");
assert.equal(html("**a** b **[c]**을"), "<p><strong>a</strong> b <strong>[c]</strong>을</p>");
assert.equal(html("2 ** 3 ** 4"), "<p>2 ** 3 ** 4</p>");
assert.equal(html("T-46~T-49, ~~gone~~"), "<p>T-46~T-49, <del>gone</del></p>");
assert.doesNotMatch(html("**x\\**y"), /<strong>/, "an escaped asterisk closes nothing");

// An image in agent Markdown is a GET the owner's browser makes with the console's cookie: only a build's capture.
const PAGE = "http://127.0.0.1:8000/projects/3/dev?job=7";
for (const src of ["/api/dev-jobs/7/screens/list-success-1440.png", "/api/dev-jobs/7/walk/0a1b2c3d4e5f/JN1.2.png",
  "http://127.0.0.1:8000/api/dev-jobs/7/screens/first-1280.png"]) {
  assert.ok(servedImage(src, PAGE), src);
}
for (const src of ["/api/events", "/api/projects/3/archive", "/api/dev-jobs/7/merge-again", "/api/export.csv", "/favicon.png",
  "/api/dev-jobs/7/screens/../../../events", "/api/dev-jobs/7/screens/a.png?x=1", "/api/dev-jobs/7/screens/a.svg",
  "//evil.example/api/dev-jobs/7/screens/a.png", "https://evil.example/pixel.png",
  "http://localhost:8000/api/dev-jobs/7/screens/a.png", "http://127.0.0.1:4173/api/dev-jobs/7/screens/a.png",
  "data:image/png;base64,AAAA", "javascript:alert(1)", ""]) {
  assert.ok(!servedImage(src, PAGE), src);
}
console.log("markdown: ok");
