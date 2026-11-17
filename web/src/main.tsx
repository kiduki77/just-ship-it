import { ReactNode, StrictMode, useCallback, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, Navigate, Route, RouterProvider, Routes, useLocation } from "react-router-dom";
import { useLoggedOut } from "./api";
import { BusinessProvider } from "./business";
import { Dock, DockProvider } from "./chat/Dock";
import { ChatSubjectProvider } from "./chat/subject";
import { Boundary, FrameError } from "./components/boundary";
import { FrameProvider } from "./components/frame";
import { NotFound } from "./components/kit";
import { t, tx, useLanguage } from "./i18n";
import { Nav, Topbar } from "./components/nav";
import { PanelHost } from "./components/panelhost";
import { CommandPalette, Toasts } from "./components/shell";
import { CompanyStoppedBanner } from "./components/stop";
import Agents, { AgentPage } from "./pages/Agents";
import Approvals from "./pages/Approvals";
import Audit from "./pages/Audit";
import BusinessPage from "./pages/Business";
import Engineering from "./pages/Engineering";
import Home from "./pages/Home";
import KickoffPage from "./pages/Kickoff";
import MyTasks from "./pages/MyTasks";
import NewBusiness from "./pages/NewBusiness";
import Operations from "./pages/Operations";
import Crews, { FromOrg } from "./pages/Crews";
import ProjectPage from "./pages/Project";
import Projects from "./pages/Projects";
import Settings from "./pages/Settings";
import Skills from "./pages/Skills";
import Tasks from "./pages/Tasks";
import { useServerLanguage } from "./settings/Language";
// self-hosted Pretendard (OFL-1.1): unicode-range subsets, so a page only downloads the glyphs it shows
import "pretendard/dist/web/variable/pretendardvariable-dynamic-subset.css";
import "./styles.css";
import "./console.css";
import "./kickoff/kickoff.css";
import "./cycle/cycle.css";
import "./owner.css";
import "./kit.css";
import { applyTheme } from "./theme";

applyTheme();

/** Old addresses keep working, with their `?b=` and the rest of the query: bookmarks and links agents wrote into documents. */
function Legacy({ to }: { to: string }) {
  const { search } = useLocation();
  return <Navigate to={`${to}${search}`} replace />;
}

/** A screen that fails to draw shows its error inside the frame; going to another address clears it. */
function ScreenBoundary({ children }: { children: ReactNode }) {
  const { pathname, search } = useLocation();
  return <Boundary reset={pathname + search}>{children}</Boundary>;
}

/** The console's own port, so the login command makes an address for this console and not the default one. */
const portFlag = () => (location.port && location.port !== "8000" ? ` --port ${location.port}` : "");

/** Every request needs the login cookie, and nothing on the console works without it. */
function LoginNeeded() {
  return (
    <main className="login-needed" id="main">
      <span className="login-logo" role="img" aria-label="Just Ship It" />
      <div className="card" role="alert">
        <h1>{t("shell.login.title")}</h1>
        <p>{tx("shell.login.how", { cmd: <code>ship supervise</code> })}</p>
        <p className="muted">{t("shell.login.expiry")}</p>
        <p className="muted">{t("shell.login.new_link")}</p>
        <pre>uv run ship api --login{portFlag()}</pre>
      </div>
    </main>
  );
}

/** The console in the owner's language: a change of language re-renders everything from here (see i18n/index.ts). */
function App() {
  const lang = useLanguage();
  const loggedOut = useLoggedOut();
  useServerLanguage(!loggedOut);
  if (loggedOut) return <LoginNeeded key={lang} />;
  return <Console key={lang} />;
}

/** The frame and every screen. A data router, so a screen can hold the owner on it (Settings with unsaved edits, `useBlocker`). */
function Frame() {
  const { pathname } = useLocation();
  // the narrow screen's menu is a modal drawer: the page behind it is inert while it is open, and closing it by Escape,
  // its close button or the scrim hands focus back to the button that opened it (a link in it moves to its page)
  const [menu, setMenu] = useState(false);
  const menuButton = useRef<HTMLButtonElement>(null);
  const open = useRef(menu);
  open.current = menu;
  const refocus = useRef<"button" | "main" | null>(null);
  const closeMenu = useCallback(() => {
    refocus.current = "button";
    setMenu(false);
  }, []);
  // a link in the drawer opened its page: focus goes to that page, once the page is no longer inert
  useEffect(() => {
    if (!open.current) return;
    refocus.current = "main";
    setMenu(false);
  }, [pathname]);
  useEffect(() => {
    if (menu) return;
    if (refocus.current === "button") menuButton.current?.focus();
    if (refocus.current === "main") document.getElementById("main")?.focus({ preventScroll: true });
    refocus.current = null;
  }, [menu]);
  useEffect(() => {
    const wide = window.matchMedia("(min-width: 901px)");
    const on = () => wide.matches && setMenu(false);
    wide.addEventListener("change", on);
    return () => wide.removeEventListener("change", on);
  }, []);
  return (
    <>
      <a className="skip" href="#main" inert={menu}>{t("shell.skip")}</a>
      <BusinessProvider>
       <FrameProvider>
        <ChatSubjectProvider>
        <DockProvider>
        <div className="shell">
          <Nav drawer={menu} onClose={closeMenu} />
          <div className="stage" inert={menu}>
            <Topbar menu={menu} onMenu={() => setMenu(true)} menuButton={menuButton} />
            <main className="main" id="main" tabIndex={-1}>
              <CompanyStoppedBanner />
              <ScreenBoundary>
              <Routes>
                <Route path="/" element={<Home />} />
                <Route path="/my-tasks" element={<MyTasks />} />
                <Route path="/business" element={<BusinessPage />} />
                <Route path="/businesses/new" element={<NewBusiness />} />
                <Route path="/kickoff/:id" element={<KickoffPage />} />
                <Route path="/inbox" element={<Approvals />} />
                <Route path="/approvals" element={<Legacy to="/inbox" />} />
                <Route path="/tasks" element={<Tasks />} />
                <Route path="/projects" element={<Projects />} />
                <Route path="/projects/:id/*" element={<ProjectPage />} />
                <Route path="/agents" element={<Agents />} />
                <Route path="/agents/:id" element={<AgentPage />} />
                <Route path="/audit/*" element={<Audit />} />
                <Route path="/usage" element={<Legacy to="/audit/costs" />} />
                <Route path="/operations" element={<Legacy to="/routines" />} />
                <Route path="/crews" element={<Crews />} />
                <Route path="/org" element={<FromOrg />} />
                <Route path="/engineering" element={<Engineering />} />
                <Route path="/skills" element={<Skills />} />
                <Route path="/routines" element={<Operations />} />
                <Route path="/settings" element={<Settings />} />
                <Route path="*" element={<NotFound />} />
              </Routes>
              </ScreenBoundary>
            </main>
          </div>
        </div>
        <PanelHost />
        <Dock />
        <CommandPalette />
        <Toasts />
        </DockProvider>
        </ChatSubjectProvider>
       </FrameProvider>
      </BusinessProvider>
    </>
  );
}

const router = createBrowserRouter([{ path: "*", element: <Frame />, errorElement: <FrameError /> }]);

function Console() {
  return <RouterProvider router={router} />;
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
