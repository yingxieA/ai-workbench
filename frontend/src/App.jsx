import React, { useState, useEffect } from 'react';
import { Button, Avatar, Dropdown, Tooltip, Input, message, Popconfirm } from 'antd';
import {
  MenuFoldOutlined, MenuUnfoldOutlined, PlusOutlined,
  HomeOutlined, MessageOutlined, NotificationOutlined,
  GithubOutlined, BulbOutlined, RocketOutlined,
  UserOutlined, LogoutOutlined, FileTextOutlined, MoreOutlined, EnvironmentOutlined, AuditOutlined, AppstoreOutlined,
  DatabaseOutlined, BarChartOutlined, BugOutlined
} from '@ant-design/icons';
import { BrowserRouter, Routes, Route, Navigate, useNavigate, useLocation } from 'react-router-dom';
import useAppStore from './store/useAppStore';
import ChatPage from './pages/ChatPage';
import DashboardPage from './pages/DashboardPage';
import NewsPage from './pages/NewsPage';
import DocsPage from './pages/DocsPage';
import SkillsPage from './pages/SkillsPage';
import LearningPage from './pages/LearningPage';
import GitHubPage from './pages/GitHubPage';
import TravelPage from './pages/TravelPage';
import UsersPage from './pages/UsersPage';
import AuditPage from './pages/AuditPage';
import MemoryPage from './pages/MemoryPage';
import QualityCenterPage from './pages/QualityCenterPage';
import ToolsPage from './pages/ToolsPage';
import IntentRulesPage from './pages/IntentRulesPage';
import DebuggerPage from './pages/DebuggerPage';
import { authHeaders, authFetch } from './utils/api';
import './App.css';

const i18n = {
  zh: {
    'nav.dashboard': '工作台总览', 'nav.chat': '智能问答', 'nav.news': 'AI 日报', 'nav.github': 'GitHub 周榜',
    'nav.skills': '技能管理', 'nav.learning': '学习路径', 'nav.docs': '文档管理', 'nav.travel': '旅游规划',
    'nav.users': '用户管理', 'nav.audit': '审计日志', 'nav.tools': '工具管理', 'nav.intents': '固定话术', 'nav.memories': '记忆管理', 'nav.quality': '质量中心', 'nav.debug': '调试面板',
    'app.title': 'AI 工作台', 'app.subtitle': '学习 · 问答 · 资讯',
    'chat.placeholder': '输入问题，回车发送', 'chat.send': '发送', 'chat.title': '智能问答',
    'chat.empty': '开始对话吧', 'chat.thinking': '思考中...',
    'news.title': 'AI 日报', 'news.subtitle': '每日 AI 资讯摘要', 'news.generate': '生成今日日报', 'news.summary': '今日摘要',
    'github.title': 'GitHub 周榜', 'github.subtitle': '近一周热门 AI 项目', 'github.search': '搜索项目名或语言...',
    'docs.title': '文档管理', 'docs.subtitle': '上传文档，构建你的知识库',
  },
  en: {
    'nav.dashboard': 'Dashboard', 'nav.chat': 'Chat', 'nav.news': 'AI News', 'nav.github': 'GitHub Trending',
    'nav.skills': 'Skills', 'nav.learning': 'Learning Path', 'nav.docs': 'Documents',
    'nav.users': 'Users', 'nav.audit': 'Audit Logs', 'nav.tools': 'Tools', 'nav.intents': 'Intent Rules', 'nav.memories': 'Memories', 'nav.quality': 'Quality Center', 'nav.debug': 'Debugger',
    'app.title': 'AI Workbench', 'app.subtitle': 'Learn · Chat · News',
    'chat.placeholder': 'Type your question...', 'chat.send': 'Send', 'chat.title': 'AI Chat',
    'chat.empty': 'Start a conversation', 'chat.thinking': 'Thinking...',
    'news.title': 'AI News', 'news.subtitle': 'Daily AI Briefing', 'news.generate': 'Generate Today', 'news.summary': 'Summary',
    'github.title': 'GitHub Trending', 'github.subtitle': 'Hot AI Projects This Week', 'github.search': 'Search by name or language...',
    'docs.title': 'Documents', 'docs.subtitle': 'Upload docs to build your KB',
  }
};

function LoginPage() {
  const { setToken, setUser } = useAppStore();
  const navigate = useNavigate();
  const [mode, setMode] = useState('login'); // login | register
  const [loginForm, setLoginForm] = useState({ username: '', password: '' });
  const [regForm, setRegForm] = useState({ username: '', password: '', nickname: '', email: '' });
  const [loading, setLoading] = useState(false);

  const handleLogin = async () => {
    if (!loginForm.username || !loginForm.password) {
      message.error('请输入用户名和密码');
      return;
    }
    setLoading(true);
    try {
      const res = await fetch(`${import.meta.env.VITE_API_BASE}/api/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(loginForm)
      });
      const data = await res.json();
      if (data.token) {
        setToken(data.token);
        localStorage.setItem('refresh_token', data.refresh_token || '');
        localStorage.setItem('user', JSON.stringify(data.user || {}));
        setUser(data.user);
        message.success('登录成功');
        navigate('/chat');
      } else {
        message.error(data.detail || '登录失败');
      }
    } catch (e) {
      message.error('登录失败：' + e.message);
    }
    setLoading(false);
  };

  const handleRegister = async () => {
    if (!regForm.username || !regForm.password) {
      message.error('请输入用户名和密码');
      return;
    }
    setLoading(true);
    try {
      const res = await fetch(`${import.meta.env.VITE_API_BASE}/api/auth/register`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(regForm)
      });
      const data = await res.json();
      if (data.token) {
        setToken(data.token);
        localStorage.setItem('refresh_token', data.refresh_token || '');
        localStorage.setItem('user', JSON.stringify(data.user || {}));
        setUser(data.user);
        message.success('注册成功，已自动登录');
        navigate('/chat');
      } else {
        message.error(data.detail || '注册失败');
      }
    } catch (e) {
      message.error('注册失败：' + e.message);
    }
    setLoading(false);
  };

  return (
    <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--bg-primary)' }}>
      <div style={{ width: 360, padding: 40, background: 'var(--bg-secondary)', borderRadius: 16, boxShadow: 'var(--input-shadow)' }}>
        <div style={{ textAlign: 'center', marginBottom: 24 }}>
          <div style={{ width: 48, height: 48, borderRadius: 12, background: 'var(--accent-color)', color: '#fff', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontSize: 18, fontWeight: 700, marginBottom: 12 }}>AW</div>
          <h2 style={{ margin: 0, fontSize: 20, color: 'var(--text-primary)' }}>AI 工作台</h2>
          <p style={{ margin: '8px 0 0', fontSize: 13, color: 'var(--text-tertiary)' }}>{mode === 'login' ? '请登录后使用' : '注册新账号'}</p>
        </div>
        {mode === 'login' ? (
          <>
            <Input
              placeholder="用户名"
              value={loginForm.username}
              onChange={e => setLoginForm({ ...loginForm, username: e.target.value })}
              autoComplete="off"
              style={{ marginBottom: 12, borderRadius: 8, background: 'var(--bg-primary)', color: 'var(--text-primary)' }}
            />
            <Input.Password
              placeholder="密码"
              value={loginForm.password}
              onChange={e => setLoginForm({ ...loginForm, password: e.target.value })}
              onPressEnter={handleLogin}
              autoComplete="new-password"
              style={{ marginBottom: 20, borderRadius: 8, background: 'var(--bg-primary)', color: 'var(--text-primary)' }}
            />
            <Button
              type="primary"
              block
              loading={loading}
              onClick={handleLogin}
              style={{ background: 'var(--accent-color)', borderColor: 'var(--accent-color)', borderRadius: 8, height: 40 }}
            >
              登录
            </Button>
            <div style={{ textAlign: 'center', marginTop: 16 }}>
              <Button type="link" onClick={() => setMode('register')}>没有账号？注册</Button>
            </div>
          </>
        ) : (
          <>
            <Input
              placeholder="用户名（3-20 字符）"
              value={regForm.username}
              onChange={e => setRegForm({ ...regForm, username: e.target.value })}
              autoComplete="off"
              style={{ marginBottom: 12, borderRadius: 8, background: 'var(--bg-primary)', color: 'var(--text-primary)' }}
            />
            <Input.Password
              placeholder="密码（至少 8 位，含字母和数字）"
              value={regForm.password}
              onChange={e => setRegForm({ ...regForm, password: e.target.value })}
              autoComplete="new-password"
              style={{ marginBottom: 12, borderRadius: 8, background: 'var(--bg-primary)', color: 'var(--text-primary)' }}
            />
            <Input
              placeholder="昵称（可选）"
              value={regForm.nickname}
              onChange={e => setRegForm({ ...regForm, nickname: e.target.value })}
              autoComplete="off"
              style={{ marginBottom: 12, borderRadius: 8, background: 'var(--bg-primary)', color: 'var(--text-primary)' }}
            />
            <Input
              placeholder="邮箱（可选）"
              value={regForm.email}
              onChange={e => setRegForm({ ...regForm, email: e.target.value })}
              onPressEnter={handleRegister}
              autoComplete="off"
              style={{ marginBottom: 20, borderRadius: 8, background: 'var(--bg-primary)', color: 'var(--text-primary)' }}
            />
            <Button
              type="primary"
              block
              loading={loading}
              onClick={handleRegister}
              style={{ background: 'var(--accent-color)', borderColor: 'var(--accent-color)', borderRadius: 8, height: 40 }}
            >
              注册并登录
            </Button>
            <div style={{ textAlign: 'center', marginTop: 16 }}>
              <Button type="link" onClick={() => setMode('login')}>已有账号？去登录</Button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function App() {
  const { collapsed, setCollapsed, page, setPage, user, setUser, token, setToken, sessionsList, setSessionsList, sessionId, setSessionId, messages, setMessages, loadingSession, setLoadingSession } = useAppStore();
  const navigate = useNavigate();
  const location = useLocation();
  const [editingId, setEditingId] = useState(null);
  const [editingValue, setEditingValue] = useState('');
  const [lang, setLang] = useState(localStorage.getItem('lang') || 'zh');
  const [theme, setTheme] = useState(localStorage.getItem('theme') || 'light');
  const t = (key) => i18n[lang][key] || key;

  useEffect(() => {
    if (token) {
      authFetch(`${import.meta.env.VITE_API_BASE}/api/chat/sessions`).then(r => r.json()).then(setSessionsList).catch(() => {});
      // 刷新用户信息（确保 role_level/is_admin 最新，兼容旧 localStorage 缓存）
      authFetch(`${import.meta.env.VITE_API_BASE}/api/auth/me`)
        .then(r => r.json())
        .then(data => {
          if (data && data.id) {
            setUser(data);
            localStorage.setItem('user', JSON.stringify(data));
          }
        })
        .catch(() => {});
    }
  }, [token]);

  // 监听 auth-expired：refresh 失败时清登录态回登录页
  useEffect(() => {
    const onExpired = () => { setToken(null); setUser(null); };
    window.addEventListener('auth-expired', onExpired);
    return () => window.removeEventListener('auth-expired', onExpired);
  }, []);

  useEffect(() => {
    document.body.className = theme === 'dark' ? 'dark' : '';
    localStorage.setItem('theme', theme);
  }, [theme]);

  useEffect(() => {
    const p = location.pathname.slice(1) || 'dashboard';
    setPage(p);
  }, [location]);

  const handleNavigate = (key) => {
    setPage(key);
    navigate('/' + key);
  };

  const loadSession = async (sid) => {
    setLoadingSession(true);
    try {
      const res = await authFetch(`${import.meta.env.VITE_API_BASE}/api/chat/sessions/${sid}/messages`);
      const data = await res.json();
      // 兼容后端返回数组或 { messages: [...] } 两种格式
      const msgs = Array.isArray(data) ? data : (data.messages || []);
      setMessages(msgs);
      setSessionId(sid);
    } catch (e) { console.error(e); setMessages([]); }
    setLoadingSession(false);
  };

  const newChat = () => {
    setMessages([]);
    setSessionId(null);
    setLoadingSession(false);
  };

  if (!token) {
    return <LoginPage />;
  }

  const isAdmin = !!(user?.is_admin || (user?.role_level ?? 0) >= 100);

  const navItems = [
    { key: 'dashboard', icon: <HomeOutlined />, label: t('nav.dashboard') },
    { key: 'chat', icon: <MessageOutlined />, label: t('nav.chat') },
    { key: 'news', icon: <NotificationOutlined />, label: t('nav.news') },
    { key: 'github', icon: <GithubOutlined />, label: t('nav.github') },
    { key: 'skills', icon: <BulbOutlined />, label: t('nav.skills') },
    { key: 'learning', icon: <RocketOutlined />, label: t('nav.learning') },
    { key: 'travel', icon: <EnvironmentOutlined />, label: t('nav.travel') },
    // 管理后台（文档管理 / 用户管理 / 审计日志 / 工具管理）仅 admin 可见
    ...(isAdmin ? [
      { key: 'docs', icon: <FileTextOutlined />, label: t('nav.docs') },
      { key: 'users', icon: <UserOutlined />, label: t('nav.users') },
      { key: 'audit', icon: <AuditOutlined />, label: t('nav.audit') },
      { key: 'tools', icon: <AppstoreOutlined />, label: t('nav.tools') },
      { key: 'intents', icon: <MessageOutlined />, label: t('nav.intents') },
      { key: 'memories', icon: <DatabaseOutlined />, label: t('nav.memories') },
      { key: 'quality', icon: <BarChartOutlined />, label: t('nav.quality') },
      { key: 'debug', icon: <BugOutlined />, label: t('nav.debug') },
    ] : []),
  ];

  const isChatPage = page === 'chat';

  return (
    <div style={{ display: 'flex', minHeight: '100vh', height: '100vh', background: 'var(--bg-primary)' }}>
      <div style={{
        width: collapsed ? 64 : 260,
        background: 'var(--bg-primary)',
        borderRight: '1px solid var(--border-color)',
        transition: 'width 0.2s ease',
        flexShrink: 0,
      }}>
        <div style={{ height: '100%', display: 'flex', flexDirection: 'column', position: 'relative' }}>
          <div style={{ padding: '16px 16px 8px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexShrink: 0 }}>
            {!collapsed && (
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <div className="logo-icon">AW</div>
                <div>
                  <div className="logo-title">AI 工作台</div>
                  <div className="logo-sub">学习·问答·资讯</div>
                </div>
              </div>
            )}
            <Button type="text" icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />} onClick={() => setCollapsed(!collapsed)} />
          </div>
          <nav className="nav">
            {navItems.map(item => (
              <Tooltip key={item.key} title={item.label} placement="right" trigger={collapsed ? 'hover' : []}>
                <div className={`nav-item ${page === item.key ? 'active' : ''}`} onClick={() => handleNavigate(item.key)} style={{ justifyContent: collapsed ? 'center' : 'flex-start' }}>
                  {item.icon}
                  {!collapsed && <span>{item.label}</span>}
                </div>
              </Tooltip>
            ))}
            {collapsed && (
              <Tooltip title="新对话" placement="right">
                <div className="nav-item" onClick={newChat} style={{ justifyContent: 'center' }}><PlusOutlined /></div>
              </Tooltip>
            )}
          </nav>
          {!collapsed && page === 'chat' && (
            <div style={{ flex: 1, overflowY: 'auto', padding: '0 8px', borderTop: '1px solid var(--border-color)' }}>
              <Button block style={{ margin: '8px 0', textAlign: 'left' }} onClick={newChat}>+ 新对话</Button>
              {sessionsList.length === 0 && <div style={{ padding: 24, textAlign: 'center', color: 'var(--text-tertiary)', fontSize: 13 }}>暂无历史记录</div>}
              {sessionsList.map(s => (
                <div key={s.id} className="session-item" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', height: 36, padding: '0 12px', borderRadius: 8, cursor: 'pointer', marginBottom: 4, background: sessionId === s.id ? 'var(--accent-bg)' : 'transparent', fontSize: 13, color: sessionId === s.id ? 'var(--accent-color)' : 'var(--text-secondary)' }}>
                  {editingId === s.id ? (
                    <Input size="small" value={editingValue} onChange={e => setEditingValue(e.target.value)} autoFocus onFocus={e => e.target.select()}
                      onPressEnter={async () => {
                        await authFetch(`${import.meta.env.VITE_API_BASE}/api/chat/sessions/${s.id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title: editingValue }) });
                        setSessionsList(sessionsList.map(x => x.id === s.id ? { ...x, title: editingValue } : x));
                        setEditingId(null);
                      }}
                      onBlur={async () => {
                        await authFetch(`${import.meta.env.VITE_API_BASE}/api/chat/sessions/${s.id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title: editingValue }) });
                        setSessionsList(sessionsList.map(x => x.id === s.id ? { ...x, title: editingValue } : x));
                        setEditingId(null);
                      }}
                      onKeyDown={e => { if (e.key === 'Escape') setEditingId(null); }} />
                  ) : (
                    <Tooltip title={s.title} placement="right">
                      <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} onClick={() => loadSession(s.id)}>{s.title}</span>
                    </Tooltip>
                  )}
                  <Dropdown trigger={['hover']} menu={{
                    items: [
                      { key: 'rename', label: '重命名', onClick: () => { setEditingId(s.id); setEditingValue(s.title); } },
                      { key: 'delete', label: <Popconfirm title="确定要删除这条对话吗？" okText="删除" cancelText="取消" okButtonProps={{ danger: true }} onConfirm={async () => {
                        await authFetch(`${import.meta.env.VITE_API_BASE}/api/chat/sessions/${s.id}`, { method: 'DELETE' });
                        setSessionsList(sessionsList.filter(x => x.id !== s.id));
                      }}><span style={{color:'#ff4d4f'}}>删除</span></Popconfirm> }
                    ]
                  }}>
                    <MoreOutlined className="more-icon" style={{ color: '#999', opacity: 0 }} />
                  </Dropdown>
                </div>
              ))}
            </div>
          )}
          <div style={{ position: 'absolute', bottom: 0, left: 0, right: 0, padding: 16, borderTop: '1px solid var(--border-color)', background: 'var(--bg-primary)', zIndex: 1 }}>
            <Dropdown menu={{
              items: [
                { key: 'lang', label: lang === 'zh' ? 'Switch to English' : '切换到中文' },
                { key: 'theme', label: theme === 'dark' ? '切换到浅色' : '切换到深色' },
                { type: 'divider' },
                { key: 'logout', label: '退出登录', danger: true }
              ],
              onClick: ({ key }) => {
                if (key === 'lang') {
                  const newLang = lang === 'zh' ? 'en' : 'zh';
                  setLang(newLang);
                  localStorage.setItem('lang', newLang);
                }
                if (key === 'theme') setTheme(theme === 'dark' ? 'light' : 'dark');
                if (key === 'logout') { setToken(null); setUser(null); localStorage.removeItem('refresh_token'); }
              }
            }} placement="topLeft">
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, cursor: 'pointer', justifyContent: collapsed ? 'center' : 'flex-start' }}>
                <Avatar size={32} style={{ background: 'var(--accent-color)', flexShrink: 0 }} icon={<UserOutlined />} />
                {!collapsed && <span style={{ fontSize: 13, color: 'var(--text-secondary)' }}>{user?.nickname || user?.username || 'admin'}</span>}
              </div>
            </Dropdown>
          </div>
        </div>
      </div>
      <div style={{
        flex: 1,
        overflowY: isChatPage ? 'hidden' : 'auto',
        overflowX: 'hidden',
        paddingTop: isChatPage ? 0 : 48,
        paddingBottom: isChatPage ? 0 : 48,
        display: isChatPage ? 'flex' : 'block',
        flexDirection: isChatPage ? 'column' : 'row',
        alignItems: isChatPage ? 'center' : 'stretch',
        minWidth: 0,
      }}>
        <div style={{
          maxWidth: isChatPage ? 900 : 1000,
          width: isChatPage ? '100%' : '100%',
          margin: isChatPage ? 0 : '0 auto',
          padding: '0 24px',
          flex: isChatPage ? 1 : 'none',
          display: isChatPage ? 'flex' : 'block',
          flexDirection: isChatPage ? 'column' : 'row',
          overflow: isChatPage ? 'hidden' : 'visible',
          minWidth: 0,
        }}>
          <Routes>
            <Route path="/" element={<DashboardPage t={t} onNavigate={handleNavigate} />} />
            <Route path="/dashboard" element={<DashboardPage t={t} onNavigate={handleNavigate} />} />
            <Route path="/chat" element={<ChatPage t={t} messages={messages} setMessages={setMessages} sessionId={sessionId} setSessionId={setSessionId} loadingSession={loadingSession} sessionsList={sessionsList} setSessionsList={setSessionsList} />} />
            <Route path="/news" element={<NewsPage t={t} />} />
            <Route path="/docs" element={isAdmin ? <DocsPage t={t} /> : <Navigate to="/chat" replace />} />
            <Route path="/users" element={isAdmin ? <UsersPage /> : <Navigate to="/chat" replace />} />
            <Route path="/audit" element={isAdmin ? <AuditPage /> : <Navigate to="/chat" replace />} />
            <Route path="/tools" element={isAdmin ? <ToolsPage /> : <Navigate to="/chat" replace />} />
            <Route path="/intents" element={isAdmin ? <IntentRulesPage /> : <Navigate to="/chat" replace />} />
            <Route path="/memories" element={isAdmin ? <MemoryPage /> : <Navigate to="/chat" replace />} />
            <Route path="/quality" element={isAdmin ? <QualityCenterPage /> : <Navigate to="/chat" replace />} />
            <Route path="/debug" element={isAdmin ? <DebuggerPage /> : <Navigate to="/chat" replace />} />
            <Route path="/skills" element={<SkillsPage t={t} />} />
            <Route path="/learning" element={<LearningPage />} />
            <Route path="/github" element={<GitHubPage t={t} />} />
            <Route path="/travel" element={<TravelPage />} />
          </Routes>
        </div>
      </div>
    </div>
  );
}

export default function AppWrapper() {
  return (
    <BrowserRouter>
      <App />
    </BrowserRouter>
  );
}
