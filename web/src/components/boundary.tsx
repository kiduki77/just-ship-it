import { Component, ErrorInfo, ReactNode } from "react";
import { Link, useRouteError } from "react-router-dom";
import { t } from "../i18n";
import { PageHead } from "./kit";

/**
 * A render error stays inside the screen or panel that threw it: the sidebar, the top bar and the way to other
 * screens keep working. `reset` changes when the owner goes elsewhere (the address), which clears the error.
 */
export class Boundary extends Component<{ reset: string; panel?: { onClose: () => void }; children: ReactNode }, { error: Error | null; reset: string }> {
  state = { error: null as Error | null, reset: this.props.reset };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  static getDerivedStateFromProps(props: { reset: string }, state: { error: Error | null; reset: string }) {
    return props.reset === state.reset ? null : { error: null, reset: props.reset };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("render error", error, info.componentStack);
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    const detail = (
      <details className="crash-detail">
        <summary>{t("shell.crash.detail")}</summary>
        <pre className="mono small">{String(error.message ?? error)}</pre>
      </details>
    );
    const reload = <button type="button" className="btn-primary" onClick={() => location.reload()}>{t("shell.crash.reload")}</button>;
    if (this.props.panel) {
      return (
        <aside className="side-panel" role="dialog" aria-modal="false" aria-labelledby="sp-crash">
          <div className="sp-scroll crash" role="alert">
            <h2 id="sp-crash">{t("shell.crash.title")}</h2>
            <p>{t("shell.crash.panel")}</p>
            <div className="row wrap">{reload}<button type="button" className="btn-ghost" onClick={this.props.panel.onClose}>{t("shell.crash.close")}</button></div>
            {detail}
          </div>
        </aside>
      );
    }
    return (
      <div className="crash" role="alert">
        <PageHead title={t("shell.crash.title")} status={{ tone: "bad", text: t("shell.crash.body") }} />
        <div className="row wrap">{reload}<Link className="btn-ghost" to="/">{t("common.err.back_home")}</Link></div>
        {detail}
      </div>
    );
  }
}

/** The router's own catch for an error outside every screen (the sidebar, the top bar): the frame is gone, so this
 * stands alone with the way back. */
export function FrameError() {
  const error = useRouteError();
  console.error("frame render error", error);
  return (
    <main className="login-needed" id="main">
      <div className="card crash" role="alert">
        <h1>{t("shell.crash.title")}</h1>
        <p>{t("shell.crash.frame")}</p>
        <button type="button" className="btn-primary" onClick={() => location.reload()}>{t("shell.crash.reload")}</button>
      </div>
    </main>
  );
}
