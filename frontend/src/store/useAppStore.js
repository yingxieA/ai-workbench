import { create } from 'zustand';

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
  // 用户
  user: null,
  setUser: (u) => set({ user: u }),
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
  // 当前会话
  sessionId: null,
  setSessionId: (id) => set({ sessionId: id }),
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
