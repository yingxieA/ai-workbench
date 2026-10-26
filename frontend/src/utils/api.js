/**
 * 统一请求封装：
 * - 自动附加 JWT Authorization header
 * - 401 时自动用 refresh_token 续期并重放原请求
 * - 续期失败派发 auth-expired 事件（App 监听后跳登录）
 */
export function authHeaders(extra = {}) {
  const token = localStorage.getItem('token');
  const headers = { ...extra };
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }
  return headers;
}

// refresh 单飞锁：并发 401 只发起一个 refresh，其余请求等待同一个 Promise（避免 token 轮换竞态 + 401 日志刷屏）
let _refreshInFlight = null;
/**
 * 用 refresh_token 换取新 token 对（轮换），写回 localStorage
 * @returns {Promise<boolean>} 是否成功
 */
export async function refreshAccessToken() {
  if (_refreshInFlight) return _refreshInFlight;
  const refreshToken = localStorage.getItem('refresh_token');
  if (!refreshToken) return false;
  _refreshInFlight = (async () => {
    try {
      const res = await fetch(`${import.meta.env.VITE_API_BASE}/api/auth/refresh`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: refreshToken }),
      });
      if (!res.ok) return false;
      const data = await res.json();
      if (!data.token || !data.refresh_token) return false;
      localStorage.setItem('token', data.token);
      localStorage.setItem('refresh_token', data.refresh_token);
      return true;
    } catch (e) {
      return false;
    } finally {
      _refreshInFlight = null;
    }
  })();
  return _refreshInFlight;
}

/** 清除登录态并通知 App 跳登录页（带单飞锁：并发 401 场景只执行一次） */
let _cleared = false;
export function clearAuthAndNotify() {
  if (_cleared) return;
  _cleared = true;
  localStorage.removeItem('token');
  localStorage.removeItem('refresh_token');
  localStorage.removeItem('user');
  window.dispatchEvent(new Event('auth-expired'));
  // 登出后复位，允许下次登录后再失效时再次触发
  setTimeout(() => { _cleared = false; }, 0);
}

/**
 * 带认证的 fetch：401 时自动续期重放一次
 */
export async function authFetch(url, options = {}) {
  const doFetch = (extraHeaders) => fetch(url, {
    ...options,
    headers: { ...(options.headers || {}), ...extraHeaders },
  });

  // 认证接口本身不做重放（避免 /refresh 死循环）
  const isAuthEndpoint = url.includes('/api/auth/');

  let res = await doFetch(authHeaders());
  if (res.status === 401 && !isAuthEndpoint) {
    const ok = await refreshAccessToken();
    if (ok) {
      res = await doFetch(authHeaders());
    } else {
      clearAuthAndNotify();
    }
  }
  return res;
}
