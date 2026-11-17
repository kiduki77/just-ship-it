import type { PlainKey } from "../i18n";

export interface Step {
  text: PlainKey;
  link?: { href: string; label: PlainKey };
}

export interface Guide {
  /** The credential this guide ends in, a key of the server's secret list. */
  secret: string | null;
  name: string;
  why: PlainKey;
  minutes: number | null;
  steps: Step[];
  note?: PlainKey;
  /** Shown instead of a token field while there is nothing to connect yet. */
  soon?: PlainKey;
}

/**
 * The connections, in the order a first-time open-source owner needs them. A row only offers its guide when the owner
 * opens it, and a connection that is not set up is shown as plainly "Not connected", never as a problem.
 * The steps come from docs/ux/research/integrations.md; a value the research could not confirm is not stated here.
 */
export const GUIDES: Guide[] = [
  {
    secret: "github_token",
    name: "GitHub",
    why: "settings.guide.github.why",
    minutes: 5,
    steps: [
      { text: "settings.guide.github.1", link: { href: "https://github.com/settings/personal-access-tokens/new", label: "settings.guide.github.1.link" } },
      { text: "settings.guide.github.2" },
      { text: "settings.guide.github.3" },
      { text: "settings.guide.github.4" },
      { text: "settings.guide.github.5" },
    ],
    note: "settings.guide.github.note",
  },
  {
    secret: "slack_bot_token",
    name: "Slack",
    why: "settings.guide.slack.why",
    minutes: 10,
    steps: [
      { text: "settings.guide.slack.1", link: { href: "https://api.slack.com/apps", label: "settings.guide.slack.1.link" } },
      { text: "settings.guide.slack.2" },
      { text: "settings.guide.slack.3" },
      { text: "settings.guide.slack.4" },
      { text: "settings.guide.slack.5", link: { href: "#set-notify", label: "settings.guide.slack.5.link" } },
    ],
    note: "settings.guide.slack.note",
  },
  {
    secret: "cloudflare_api_token",
    name: "Cloudflare",
    why: "settings.guide.cloudflare.why",
    minutes: 10,
    steps: [
      { text: "settings.guide.cloudflare.1", link: { href: "https://dash.cloudflare.com/profile/api-tokens", label: "settings.guide.cloudflare.1.link" } },
      { text: "settings.guide.cloudflare.2" },
      { text: "settings.guide.cloudflare.3" },
      { text: "settings.guide.cloudflare.4" },
    ],
    note: "settings.guide.cloudflare.note",
  },
  {
    secret: null,
    name: "Gmail",
    why: "settings.guide.gmail.why",
    minutes: null,
    steps: [],
    soon: "settings.guide.gmail.soon",
  },
];
