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

/**
 * 用 refresh_token 换取新 token 对（轮换），写回 localStorage
 * @returns {Promise<boolean>} 是否成功
 */
export async function refreshAccessToken() {
  const refreshToken = localStorage.getItem('refresh_token');
  if (!refreshToken) return false;
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
  }
}

/** 清除登录态并通知 App 跳登录页 */
export function clearAuthAndNotify() {
  localStorage.removeItem('token');
  localStorage.removeItem('refresh_token');
  localStorage.removeItem('user');
  window.dispatchEvent(new Event('auth-expired'));
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
