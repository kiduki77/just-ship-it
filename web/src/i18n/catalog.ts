// Every area's messages. Each area is one file under ./areas, owned by one lane (docs/I18N.md); add keys there, not here.
import { attach } from "./areas/attach";
import { audit } from "./areas/audit";
import { build } from "./areas/build";
import { business } from "./areas/business";
import { businessBets } from "./areas/business-bets";
import { businessKit } from "./areas/business-kit";
import { businessLook } from "./areas/business-look";
import { businessRules } from "./areas/business-rules";
import { businessProfile } from "./areas/business-profile";
import { businessTracking } from "./areas/business-tracking";
import { chat } from "./areas/chat";
import { common } from "./areas/common";
import { company } from "./areas/company";
import { companyPeople } from "./areas/company-people";
import { cycle } from "./areas/cycle";
import { decide } from "./areas/decide";
import { home } from "./areas/home";
import { kickoff } from "./areas/kickoff";
import { label } from "./areas/label";
import { path } from "./areas/path";
import { settings } from "./areas/settings";
import { shell } from "./areas/shell";
import { term } from "./areas/term";
import { work } from "./areas/work";
import { workTasks } from "./areas/work-tasks";
import { workTodo } from "./areas/work-todo";

export const catalog = {
  ...common, ...shell, ...home, ...settings, ...label, ...term,
  ...decide, ...work, ...workTasks, ...workTodo, ...build, ...business, ...businessBets, ...businessKit, ...businessLook, ...businessRules, ...businessProfile, ...businessTracking, ...kickoff, ...company, ...companyPeople, ...attach, ...cycle, ...audit, ...chat, ...path,
};
