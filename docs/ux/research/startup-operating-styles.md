# How successful startups actually plan and run work: research notes for an AI-run company roadmap

Researched 2026-11-01. Every claim carries a source URL. Labels used:

- **[verified]** means I read the claim on the linked page this session.
- **[secondary]** means it came from a summary or aggregator, not the primary source.
- **[unverified]** means I could not confirm it. Treat it as a lead.
- **[inference]** means it is my design reading of the evidence, not a sourced fact.

---

## 1. Stage models and their exit gates

### 1.1 Y Combinator: launch, talk to users, grow weekly

- YC's "Essential Startup Advice" (Geoff Ralston and Michael Seibel, 2017) lists, in order, "Launch now", "Build something people want", "Do things that don't scale", "Find the 90/10 solution", "Find 10-100 customers who love your product", and "Write code, talk to users" as the two most important early tasks. [verified] https://www.ycombinator.com/blog/ycs-essential-startup-advice/
- The same list says "Growth is the result of a great product not the precursor" and "Don't scale your team/product until you have built something people want". Growing before product-market fit causes poor retention. [verified] same URL
- It also says to avoid long negotiated deals with big customers, corp-dev conversations, and conferences unless they are the best way to get customers, and that "startups can only solve one problem well at any given time". [verified] same URL
- Paul Graham, "Startup = Growth". A good growth rate during YC is 5 to 7% a week. 10% a week is exceptional. 1% means "you haven't yet figured out what you're doing". [verified] https://paulgraham.com/growth.html
- Measure revenue if you charge, otherwise active users. Founders pick a weekly target they think they can hit and try to hit it every week. Growth becomes "a compass to make almost every decision you face". Buying users or counting inactive users as active throws the compass off. [verified] https://paulgraham.com/growth.html
- Paul Graham, "Default Alive or Default Dead". Assume expenses stay flat and revenue keeps growing at its recent rate. Can the company reach profitability on the money it has? About half the founders he meets cannot answer. The "fatal pinch" is default dead plus slow growth plus not enough time to fix it. Hiring too fast is "by far the biggest killer of startups that raise money". Every founder needs a written plan B with a date to switch to it. [verified] https://paulgraham.com/aord.html
- Sam Altman's Startup Playbook says the goal is "to make something users love", that loved by a few beats liked by many, and that the signal is users "telling their friends to use the product without prompting". It recommends one company-wide growth metric made visible, monthly progress review, a "drumbeat" of visible progress, and thinking only about 10x current scale. [verified] https://playbook.samaltman.com/
- The playbook says "don't let your company start doing the next thing until you've dominated the first thing" and that "the core principle of execution is to never lose momentum". [verified] https://playbook.samaltman.com/
- The official YC investor update template. I could not find a primary YC page. The YC library URL I tried returned 404. [unverified] See section 3.1 for what the secondary sources say.

### 1.2 Lean Startup and Running Lean

- Ash Maurya's Running Lean uses three stages. Problem/Solution Fit asks whether a problem is worth solving. Product/Market Fit asks whether you built something people want. Scale asks how to accelerate growth. Before fit the focus is learning and pivots, after fit it is growth and optimization. [secondary] https://fourweekmba.com/running-lean-ash-maurya and https://www.slideshare.net/slideshow/running-lean-dallas/5666135
- Problem/solution fit is shown through customer discovery findings and landing-page conversions. [secondary] https://digmia.com/en/blog/running-lean-workshop/
- Maurya's "Traction Roadmap" charts business-model outcomes as milestones on a timeline. It is "a contract with stakeholders on what you will achieve" while leaving how for later. Milestones are sized with a 10x rule, working back from a minimum success criterion (example from the source, $1M ARR is about 830 customers at $100/month in year 3, so about 83 in year 2, about 8 in year 1, plus a 90-day goal). [secondary, author's own sites] https://ask.leanstack.com/en/articles/3351550-working-with-the-traction-roadmap and https://www.leanfoundry.com/newsletter/feb-19-2026
- Maurya's "Customer Factory" is the acquisition, activation, retention, revenue, referral funnel, inspired by Dave McClure's AARRR. [secondary] https://www.infoq.com/articles/scaling-lean-ash-maurya
- Eric Ries recommends that "every startup have a regular 'pivot or persevere' meeting". "Less than a few weeks between meetings is too often and more than a few months is too infrequent." Book it in advance to reduce the emotional strain. He warns that vanity metrics lead to false conclusions. [verified] https://www.entrepreneur.com/article/220302
- Innovation accounting as baseline, tune the engine, then pivot or persevere. [secondary] https://www.antoinebuteau.com/lessons-from-eric-ries/

### 1.3 Product-market fit gates people actually use

- Marc Andreessen: product/market fit means "being in a good market with a product that can satisfy that market". With it, customers buy as fast as you can supply, usage outgrows servers, money piles up. Without it, "the customers aren't quite getting value", word of mouth stalls, sales cycles drag. Before fit, do "whatever is required", including changing people, rebuilding the product, or changing market. [verified] https://pmarchive.com/guide_to_startups_part4.html
- Sean Ellis test. Ask "How would you feel if you could no longer use the product?" and measure the share who answer "very disappointed". Below about 40% companies usually struggled, above it they usually had strong traction. [verified as described by First Round] https://review.firstround.com/how-superhuman-built-an-engine-to-find-product-market-fit/
- Origin of the 40% figure (a 2010 Ellis article, benchmark across about 100 startups). [secondary, unverified against Ellis's original] https://umbrex.com/resources/frameworks/product-management-frameworks/sean-ellis-product-market-fit-survey/
- Superhuman PMF engine. Four survey questions (how disappointed, who benefits most, main benefit, how to improve). Starting score 22% in summer 2017. Segmenting to the "very disappointed" personas lifted it to 33%. They wrote a "high-expectation customer" profile in users' own words. Roadmap split half on doubling down on what users love (speed) and half on fixing what holds back "somewhat disappointed" users who value speed (mobile app was the main blocker). They ignored the rest. Score reached 58% within three quarters. [verified] https://review.firstround.com/how-superhuman-built-an-engine-to-find-product-market-fit/
- Lenny Rachitsky lists PMF signs "in decreasing levels of confidence". First, cohort retention curves flatten above zero. Second, explosive word-of-mouth growth. Six-month retention benchmarks: consumer social about 25% good / 45% great, consumer transactional about 30% / 50%, consumer subscription about 40% / 70%. Quotes Casey Winters, "You have product-market fit when your retention creates enough money (or content/virality) to drive sustainable acquisition." Signs 3 and 4 are paywalled. [verified for visible part] https://www.lennysnewsletter.com/i/48314350/how-long-does-it-take-for-companies-to-find-pmf-in-bc
- Enterprise SaaS benchmarks of about 75 to 90% six-month retention. [secondary, unverified] https://echai.ventures/startingup/growth-metrics/what-is-a-good-retention-curve-and-how-do-i-know-if-mine-is-flattening
- Slack's internal gate. Teams that reached 2,000 messages had a 93% chance of still using Slack. More than 90% of sign-ups never invited anyone. [verified] https://review.firstround.com/from-0-to-1b-slacks-founder-shares-their-epic-launch-strategy/

### 1.4 Amazon Working Backwards (PR/FAQ)

- Most major Amazon products since 2004 started from a PR/FAQ. The press release is under a page, the FAQ five pages or less. Reviewers ask "so what?" and a product that is not meaningfully better is not built. Teams write ten or more drafts. Most PR/FAQs never launch, called "a feature, not a bug". [verified] https://www.aboutamazon.com/news/workplace/an-insider-look-at-amazons-culture-and-processes
- PR/FAQ meetings start with silent reading. The most senior people speak last. The line-by-line discussion is the critical part. [verified] same URL

### 1.5 Basecamp Shape Up

- Appetite. "Estimates start with a design and end with a number. Appetites start with a number and end with a design." Fixed time, variable scope. Small batch is 1 to 2 weeks, big batch is a full six weeks. [verified] https://basecamp.com/shapeup/1.2-chapter-03
- Six-week cycles, two-week cool-down. The betting table meets in cool-down with the CEO (final word), CTO, a senior programmer, and a product strategist. No backlog, only a few freshly shaped pitches. No later approval step. [verified] https://basecamp.com/shapeup/2.2-chapter-08
- Circuit breaker. A project that does not ship in its cycle gets no automatic extension. "We intentionally create a risk that the project, as pitched, won't happen." [verified] https://basecamp.com/shapeup/2.2-chapter-08
- Betting-table questions. Does the problem matter? Is the appetite right? Is the solution attractive? Is this the right time? Are the right people available? "Sometimes saying 'no' to the time commitment is really saying no to something else." [verified] https://basecamp.com/shapeup/2.3-chapter-09
- New products run in three modes. R&D mode bets time to spike key pieces and expects nothing to ship ("The aim is to spike, not to ship"). Production mode uses normal shaping and betting with parallel teams. Cleanup mode is a free-for-all before launch that "shouldn't last longer than two cycles". Bets are still made one cycle at a time. [verified] https://basecamp.com/shapeup/2.3-chapter-09
- Hill charts. Uphill is figuring out ("uncertainty, unknowns, and problem solving"). Downhill is execution. Each scope is a dot. Managers read the chart instead of asking for status, and comparing snapshots shows how work is moving. A dot that does not move is "effectively a raised hand". Task counts mislead because to-do lists grow as work progresses. [verified] https://basecamp.com/shapeup/3.4-chapter-13

### 1.6 Linear

- Project and initiative updates carry a health of On track, At risk, or Off track plus a written status, challenges, and next steps. Reminders go to the project lead on a schedule. A project whose last update was On track and is one reminder cycle plus three days overdue shows "Update Missing". Updates roll up from projects to initiatives. An agent can draft an update ("Write with Agent"). [verified] https://linear.app/docs/initiative-and-project-updates
- Project milestones mark "different stages in a project's lifecycle", with optional target dates and a completion percentage from linked issues. The next incomplete milestone is highlighted. [verified] https://linear.app/docs/project-milestones
- Linear Method guidance on roadmaps ("Setting direction is one of the most important things you'll do", review the roadmap on a routine such as weekly meetings, don't shuffle projects too often or you lose momentum, cycles are not tied to releases). [secondary, from search snippets; the method pages did not render for me] https://linear.app/method/roadmap and https://linear.app/features/plan
- Hierarchy Initiatives (months), Projects (weeks), Issues (days). [secondary, third-party summary, unverified as official] https://skills.cat/skills/horuz-ai/claude-plugins/linear-method

### 1.7 GitLab OKRs

- Health statuses. On track means the DRI is confident. Needs attention means some risk. At risk means the DRI does not expect to hit it and urgent action is needed. Objective health is a roll-up of its key results. Score bands on-target 85 to 100%, off-target 70 to 84%, at risk 0 to 69%. [secondary, from search snippets of the handbook] https://handbook.gitlab.com/handbook/company/okrs/
- KR owners give bi-weekly updates with completion percentage and status. [secondary, handbook commit] https://gitlab.com/gitlab-com/content-sites/handbook/commit/a5e9f177
- The company-wide OKR process appears to have been removed in June 2025. [secondary, unverified] https://handbook.gitlab.com/handbook/company/okrs/

### 1.8 Brian Balfour's Four Fits

- Market-product, product-channel, channel-model, model-market. They "form an ecosystem" and must be developed together. He rejects "find product-market fit first, then test channels": "Products are built to fit with channels. Channels do not mold to products." Revisit the fits constantly. [verified] https://brianbalfour.com/four-fits-growth-framework

### 1.9 Premature scaling

- Startup Genome (2011, about 650 startups) defines premature scaling as a dimension of the business (team, spend, product) running ahead of the company's actual stage. Headline figures of 70% or 74% are widely quoted. [secondary, figures unverified; the report PDF did not decode] https://startupgenome.com/insights/premature-scaling-a-deep-dive and https://www.geekwire.com/2011/number-reason-startups-fail-premature-scaling/

---

## 2. Founder stories where the order was unusual

### 2.1 Slack: build with outside teams, widen in rounds

- Development began late 2012. The team used it internally by March 2013. In May 2013 they begged six to ten friendly companies to try it, then shared it with "progressively larger groups", revising between rounds. Rdio went from a small dev group to all 120 employees. [verified] https://review.firstround.com/from-0-to-1b-slacks-founder-shares-their-epic-launch-strategy/
- August 2013 preview release, avoiding the word "beta". 8,000 invite requests on day one, 15,000 two weeks later. About six months of batched invites, each batch followed by fixes. Feedback drove concrete changes such as channel descriptions and member counts. [verified] same URL
- Butterfield's "We Don't Sell Saddles Here" memo. The Medium page returned 403. [unverified] https://medium.com/@stewart/we-dont-sell-saddles-here-4c59524d650d

### 2.2 Airbnb: do things that don't scale

- The founders went door to door in New York recruiting hosts and fixing listings. "About 30 days of going out and engaging in person with users made the difference between success and failure." [verified] https://paulgraham.com/ds.html
- The fix was professional listing photos. Accounts of the effect (bookings 2 to 3x, NYC revenue doubled) vary between retellings. [secondary] https://www.alexanderjarvis.com/airbnb-doing-things-that-dont-scale
- YC's advice list cites Airbnb's founders photographing listings themselves. [verified] https://www.ycombinator.com/blog/ycs-essential-startup-advice/

### 2.3 Stripe: the Collison installation

- When someone agreed to try the beta, the Collisons said "Right then, give me your laptop" and set them up on the spot. Early "instant" merchant accounts were set up manually behind the scenes. [verified] https://paulgraham.com/ds.html

### 2.4 Figma: long R&D before launch

- Work began June 2013. Closed beta December 2015. First dollar charged summer 2017. The build took long because browser-based real-time design was harder than expected. Field, by his own account, "thought that shipping was right around the corner, so I wasn't setting expectations correctly." Senior staff staged an intervention over his management. [verified] https://sequoiacap.com/article/dylan-field-figma-spotlight
- Founded 2012 and over four years from founding to public launch. [secondary] https://en.wikipedia.org/wiki/Dylan_Field
- Lesson [inference]. A long R&D bet can work, but the cost was set by missing expectations, not by the length. Shape Up's R&D mode makes this explicit by betting one cycle at a time.

### 2.5 Notion: restart from scratch

- In 2015 Notion faced running out of money or starting over. The founders laid off the team, moved to Kyoto and rebuilt. Ivan Zhao said they focused too much on what they wanted to bring to the world instead of what the world wanted from them. [secondary] https://figma.com/blog/design-on-a-deadline-how-notion-pulled-itself-back-from-the-brink-of-failure/ and https://cherrytree.beehiiv.com/p/notion-in-motion

### 2.6 Others from "Do Things That Don't Scale"

- Pinterest recruited at a design-bloggers conference after seeing which users were most engaged. Meraki and Pebble assembled hardware by hand first. Wufoo sent hand-written thank-you notes. Facebook launched only at Harvard, "keeping a fire contained at first". Switch to less manual methods gradually, once the market is proven. Big launches and early big-company partnerships rarely drive growth. [verified] https://paulgraham.com/ds.html

---

## 3. Reporting cadence and formats

### 3.1 Founder and investor updates

- Aaron Harris (YC) recommends KPIs first (revenue, cash, burn), defined once and repeated every update, then targeted asks as high as possible, then a short qualitative recap. "There seems to be a correlation between quality and frequency of updates." [secondary, Visible quoting Harris; Harris's own page returned 404] https://visible.vc/blog/tips-from-yc-using-asks-metrics-and-a-recap-to-power-your-investor-updates/
- The common core across templates is summary, KPIs, highlights, lowlights, and specific asks. Highlights capped near five. Lowlights must not be buried. Vague asks are ignored, specific asks get acted on. Length 500 to 800 words. [secondary] https://visible.vc/update_template_category/most-popular and https://www.focusedchaos.co/p/how-to-write-great-investor-updates

### 3.2 Amazon Weekly Business Review

- The WBR answers, in order, what customers experienced last week, how the business did, and whether it is on track. [verified] https://commoncog.com/the-amazon-weekly-business-review/
- The deck orders metrics causally. Controllable input metrics drive output metrics. Outputs are reported, not debated. You move them by driving inputs. [verified] same URL
- The standard chart is the "6-12". The trailing six weeks on the left, the trailing twelve months on the right, last year as a faded line, targets as triangles, box scores below. As few formats as possible so people build intuition. [verified] same URL
- Owners speak to their graph. Normal variation gets "nothing to see here" and the meeting moves on, but no metric is skipped. Unusual moves get a real explanation or a logged follow-up. Strategy is taken offline. A WBR is mature when it runs without its founder. [verified] same URL
- Six-page narratives. A 2004 Bezos email says "the narrative structure of a good memo forces better thought". Silent reading stops executives from bluffing. "A great memo probably should take a week or more." [verified] https://www.cnbc.com/2018/04/23/what-jeff-bezos-learned-from-requiring-6-page-memos-at-amazon.html

### 3.3 Basecamp kickoffs, heartbeats, check-ins

- 37signals six-week cycles open with a written Kickoff and close with a Heartbeat. [secondary] https://basecamp.com/newsletter/2024-09-09 and https://37signals.com/podcast/kickoffs-and-heartbeats/
- Team leads write heartbeats roughly every six weeks to a company-wide "What Works" board, as "a library of accomplishments (and, sometimes, setbacks)". They replace meetings. [verified] https://world.hey.com/jason/what-s-in-a-heartbeat-4fd72d0e
- Automatic check-ins ask "What have you worked on?" in place of standups. People answer daily or bundle days. [secondary] https://basecamp.com/newsletter/2024-09-09

### 3.4 Linear and GitLab health

- Covered in 1.6 and 1.7. Three-level health (on track, at risk, off track) plus a short written status, posted on a reminder schedule by the owner, rolled up. [verified for Linear]

### 3.5 What makes a report followable by a non-expert [inference from the sources above]

- The same few metrics in the same chart format every time, with target and trend (WBR 6-12, Harris "repeat them every month").
- A health word and a hill position instead of percent complete (Linear, Shape Up).
- Lowlights stated plainly next to highlights (investor update core).
- Asks near the top, each specific enough to act on.
- Normal weeks allowed to be short ("nothing to see here").
- Narrative prose for decisions, not bullet fragments (Amazon memos).

---

## 4. Owner and CEO collaboration rituals

- **Betting table.** Senior people pick the next cycle's bets from a few shaped pitches in cool-down, with no later approval step. [verified] https://basecamp.com/shapeup/2.2-chapter-08
- **Kickoff and heartbeat** bracket each cycle in writing. [verified] https://world.hey.com/jason/what-s-in-a-heartbeat-4fd72d0e
- **Pivot or persevere** meeting booked in advance, every few weeks to few months. [verified] https://www.entrepreneur.com/article/220302
- **PR/FAQ review** before a major bet, silent read first, senior speaks last. [verified] https://www.aboutamazon.com/news/workplace/an-insider-look-at-amazons-culture-and-processes
- **Decision classes.** Bezos 2016 letter: decide with "somewhere around 70% of the information you wish you had", be "good at quickly recognizing and correcting bad decisions", "disagree and commit", reversible decisions are "two-way doors" that take a "light-weight process", escalate misalignment fast or "exhaustion" becomes the resolution mechanism. [verified] https://www.aboutamazon.com/news/company-news/2016-letter-to-shareholders
- The Type 1 / Type 2 split (2015 letter). Type 1 is consequential and irreversible and needs a careful process. Most decisions are Type 2. Using the heavy process on everything causes slowness and too little experimentation. [secondary; the aboutamazon 2015 URL returned 404] https://fs.blog/reversible-irreversible-decisions/
- **1:1s.** Andy Grove treats the one-on-one as "the subordinate's meeting, with its agenda and tone set by him", covering indicators, problems, plans, and what nags the subordinate. Frequency scales with the subordinate's task maturity. [secondary] https://getlighthouse.com/blog/high-output-management and https://www.maxmednik.com/blog/notes-on-high-output-management-by-andrew-s-grove
- **Writing culture.** Stripe uses pre-meeting memos and kickoff docs with goals, risks, and success metrics. [secondary] https://slab.com/guides/how-success-is-written/stripe-the-best-way-to-encourage-proficient-writing-is-to-demonstrate-it/ and https://giacomofalcone.substack.com/p/the-importance-of-writing-in-business
- **Strategy offsites.** I found no strong primary source on offsite formats in this pass. [unverified]

---

## 5. Parallel tracks and when to invest in scaling the product

- Build and sell run together from the start. YC's "write code, talk to users" [verified, https://www.ycombinator.com/blog/ycs-essential-startup-advice/], Slack's friendly-team rounds [verified, https://review.firstround.com/from-0-to-1b-slacks-founder-shares-their-epic-launch-strategy/], and Balfour's rejection of product-first-then-channels [verified, https://brianbalfour.com/four-fits-growth-framework].
- Distribution before PMF is hand-made. Recruit users one at a time, then "gradually automate the bottlenecks". [verified] https://paulgraham.com/ds.html
- Scaling the product (고도화) is gated by evidence that a segment loves it. "Don't scale your team/product until you have built something people want." [verified] https://www.ycombinator.com/blog/ycs-essential-startup-advice/ Once a loving segment exists, Superhuman split product work half on deepening what that segment loves and half on removing blockers for the next ring of users. [verified] https://review.firstround.com/how-superhuman-built-an-engine-to-find-product-market-fit/
- Plan only for about 10x current scale. [verified] https://playbook.samaltman.com/
- Spend and team stay behind the stage. Overhiring is the biggest killer of funded startups and default-alive math is the money gate. [verified] https://paulgraham.com/aord.html

### 5.1 Signals people actually use to move on [compiled from the sources above]

| Move | Signal | Source |
|---|---|---|
| Keep going on an idea | Users commit something real (sign-up, waitlist, landing-page conversion, payment) | https://digmia.com/en/blog/running-lean-workshop/ [secondary], https://review.firstround.com/from-0-to-1b-slacks-founder-shares-their-epic-launch-strategy/ |
| Narrow to a segment | "Very disappointed" share by persona, 10-100 who love it | https://review.firstround.com/how-superhuman-built-an-engine-to-find-product-market-fit/, https://www.ycombinator.com/blog/ycs-essential-startup-advice/ |
| Declare PMF | Ellis score at or above 40% in the target segment, cohort retention flattening, unprompted word of mouth | Superhuman link above, https://www.lennysnewsletter.com/i/48314350/how-long-does-it-take-for-companies-to-find-pmf-in-bc, https://playbook.samaltman.com/ |
| Scale acquisition | Retention funds sustainable acquisition, channel matches model (ARPU vs CAC) | Lenny link above, https://brianbalfour.com/four-fits-growth-framework |
| Weekly health | 5-7% weekly growth good, 1% means not working | https://paulgraham.com/growth.html |
| Money gate | Default alive at current growth and burn | https://paulgraham.com/aord.html |

---

## 6. Design inputs for an AI-run company roadmap [inference]

1. **The roadmap is a list of bets, not a list of stages.** Each bet has a written pitch (PR/FAQ-lite: customer, problem, why now, "so what"), an appetite (calendar time and agent budget), a mode (R&D, production, cleanup), a falsifiable exit signal, and a circuit breaker. Stages are labels for the evidence level reached, not for elapsed time.
2. **Evidence levels as the stage spine.** Problem evidence, then commitment evidence, then a loving segment, then a repeatable channel with default-alive economics, then scale. A company can sit at one level while running bets on the next, but spend and team size stay behind the level reached.
3. **Two to three tracks from day one.** Product, Customers (hand-made distribution, interviews, outreach in outbox mode), and Money (default alive). Each track has its own bets on the same cycle.
4. **Cycles and gates.** Short cycles (AI agents can run 1 to 2 week cycles; the Shape Up six-week number is a human-team number) with a cool-down that produces a heartbeat and a betting table where the owner picks the next bets. A pivot-or-persevere review every few cycles, booked in advance.
5. **Decision classes.** Agents decide two-way doors and log them. Only one-way doors (money, customer-facing messages, legal, shutting a bet) go to the Approval Center.
6. **Reports.** Daily check-in, weekly update (metrics with trend and target first, health per bet, highlights, lowlights, asks), per-bet hill position, end-of-cycle heartbeat, gate memo for each stage move.

## 7. What to avoid

- Time-based stage templates. Every source above gates on evidence, and Figma, Notion, and Slack each ran stages in a different order.
- Percent-complete progress. Task lists grow (https://basecamp.com/shapeup/3.4-chapter-13).
- Vanity metrics such as sign-ups (https://playbook.samaltman.com/, https://www.entrepreneur.com/article/220302).
- Scaling team, spend, or product before a loving segment exists (https://www.ycombinator.com/blog/ycs-essential-startup-advice/, https://paulgraham.com/aord.html).
- Big launches, partnerships, and conferences as early growth (https://paulgraham.com/ds.html).
- A standing backlog (https://basecamp.com/shapeup/2.2-chapter-08).
- Escalating every decision. The heavy process on reversible decisions causes slowness (https://www.aboutamazon.com/news/company-news/2016-letter-to-shareholders).
- Over-optimistic ship dates with no circuit breaker (https://sequoiacap.com/article/dylan-field-figma-spotlight).

## 8. Not verified in this pass

- YC's own investor update template wording and Aaron Harris's original post (404).
- The Linear Method pages (JavaScript-rendered, only search snippets seen).
- Sean Ellis's original 2010 article and its data.
- Startup Genome's 70% / 74% figures.
- Bezos 2015 letter wording (seen only secondhand).
- Butterfield's "We Don't Sell Saddles Here" (403).
- Grove's exact 1:1 frequency wording.
- GitLab's current OKR practice (process may have been retired in 2025).
- Strategy offsite formats.
