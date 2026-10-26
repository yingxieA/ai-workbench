import { create } from 'zustand';

// 当前用户 id 动态读取（不能模块级缓存：登录/切换用户后 localStorage 已变，缓存会导致
// sessionId 持久化到旧用户 key，刷新后读不到 → 每次提问新建会话 → 历史对话重复）
const getCurrentUserId = () => {
  try { return JSON.parse(localStorage.getItem('user') || 'null')?.id || null; } catch { return null; }
};
const getSIDKey = () => {
  const uid = getCurrentUserId();
  return uid ? `chat_session_id_${uid}` : 'chat_session_id';
};

const useAppStore = create((set) => ({
  // 侧边栏
  collapsed: localStorage.getItem('sidebar-collapsed') === 'true',
  setCollapsed: (v) => {
    localStorage.setItem('sidebar-collapsed', v);
    set({ collapsed: v });
  },
  // 当前页面
  page: 'dashboard',
  setPage: (p) => set({ page: p }),
  // 用户（持久化：登录 / /me 校准后写 localStorage，刷新直接恢复）
  user: (() => { try { return JSON.parse(localStorage.getItem('user') || 'null'); } catch { return null; } })(),
  setUser: (u) => {
    if (u) localStorage.setItem('user', JSON.stringify(u));
    else localStorage.removeItem('user');
    set({ user: u });
  },
  // token
  token: localStorage.getItem('token') || null,
  setToken: (t) => {
    if (t) localStorage.setItem('token', t);
    else localStorage.removeItem('token');
    set({ token: t });
  },
  // 会话列表
  sessionsList: [],
  // 支持两种调用方式：setSessionsList(newArray) 或 setSessionsList(prev => newArray)
  setSessionsList: (listOrUpdater) => set((state) => ({
    sessionsList: typeof listOrUpdater === 'function'
      ? listOrUpdater(Array.isArray(state.sessionsList) ? state.sessionsList : [])
      : (Array.isArray(listOrUpdater) ? listOrUpdater : [])
  })),
  // 当前会话（持久化：按用户动态 key 存 localStorage，刷新后恢复上次会话，避免重复建会话）
  sessionId: localStorage.getItem(getSIDKey()) || null,
  setSessionId: (id) => {
    const key = getSIDKey();
    if (id) localStorage.setItem(key, id);
    else localStorage.removeItem(key);
    set({ sessionId: id });
  },
  // 消息
  messages: [],
  // 支持两种调用方式：setMessages(newArray) 或 setMessages(prev => newArray)
  setMessages: (msgsOrUpdater) => set((state) => ({
    messages: typeof msgsOrUpdater === 'function'
      ? msgsOrUpdater(Array.isArray(state.messages) ? state.messages : [])
      : (Array.isArray(msgsOrUpdater) ? msgsOrUpdater : [])
  })),
  // 会话加载状态
  loadingSession: false,
  setLoadingSession: (v) => set({ loadingSession: v }),
}));

export default useAppStore;
