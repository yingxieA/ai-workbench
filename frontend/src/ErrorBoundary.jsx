import { Component } from 'react';

class ErrorBoundary extends Component {
  state = { error: null };

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    console.error('ErrorBoundary:', error, info);
  }

  render() {
    if (this.state.error) {
      return (
        <div style={{ padding: 40, textAlign: 'center', fontFamily: 'sans-serif' }}>
          <h2 style={{ color: '#8b6f47' }}>页面出错了</h2>
          <p style={{ color: '#666' }}>{this.state.error.message}</p>
          <button
            onClick={() => window.location.reload()}
            style={{ padding: '8px 24px', background: '#8b6f47', color: '#fff', border: 'none', borderRadius: 6, cursor: 'pointer' }}
          >
            刷新重试
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

export default ErrorBoundary;
