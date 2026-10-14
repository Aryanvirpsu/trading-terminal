import { Component, type ErrorInfo, type ReactNode } from 'react';

/** A render error in one page must not blank the whole terminal. Reset by navigating (keyed on the route). */
export default class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) { return { error }; }
  componentDidCatch(error: Error, info: ErrorInfo) { console.error('page crashed:', error, info.componentStack); }
  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="card" style={{ marginTop: 24 }}>
        <div className="state">
          <div className="ico">⚠</div>
          <div className="msg">This page hit an error</div>
          <div className="sub">{String(this.state.error.message || this.state.error)}<br />The rest of the terminal is unaffected — pick another page, or reload.</div>
          <button className="btn" onClick={() => location.reload()}>Reload</button>
        </div>
      </div>
    );
  }
}
