import React, { useState, useRef, useEffect } from 'react';
import { fetchEventSource } from '@microsoft/fetch-event-source';
import { Input, Button, message, Spin, Space, Tag, Alert, Tooltip, Drawer } from 'antd';
import { CheckCircleOutlined, CloseCircleOutlined, ToolOutlined, WarningOutlined, SafetyCertificateOutlined, LoadingOutlined, DownOutlined, FileTextOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import MemoMessageBubble from '../components/MemoMessageBubble';
import ChatInput from '../components/ChatInput';
import { authHeaders, authFetch, refreshAccessToken, clearAuthAndNotify } from '../utils/api';

const { TextArea } = Input;

// 引用来源站点名：优先后端 site 字段，历史数据（无 site）从 url 兜底提取
function extractSite(s) {
  if (s.site) return s.site;
  const url = s.url || '';
  return url.replace(/^https?:\/\//, '').replace(/\/.*$/, '').replace(/^www\./, '') || '';
}

function ChatPage({ t, messages, setMessages, sessionId, setSessionId, loadingSession, sessionsList, setSessionsList }) {
  const [question, setQuestion] = useState('');
  const [loading, setLoading] = useState(false);
  const [chatTitle, setChatTitle] = useState('');
  const [activeTopic, setActiveTopic] = useState(0);
  const [confirmCard, setConfirmCard] = useState(null); // 高风险工具人工确认卡
  const [webSearch, setWebSearch] = useState(true);   // 联网搜索开关（默认开，保持现有能力）
  const [deepThink, setDeepThink] = useState(true);   // 深度思考开关（默认开，保持现有能力）
  const [showDownArrow, setShowDownArrow] = useState(false); // 「回到最新」箭头：离开底部时显示
  const [refDrawer, setRefDrawer] = useState(null); // 引用来源右侧栏：{open, sources}
  const navigate = useNavigate();
  const scrollRef = useRef(null);
  const abortRef = useRef(null);
  // 发送防重入锁（ref 级，避免同 tick 双触发 Enter+Click 导致重复 POST）
  const sendingRef = useRef(false);
  // 401 续期重发防重入（避免 token 反复过期导致无限重发）
  const retrying401Ref = useRef(false);
  // P2：断线单次自动重试防重入（非 abort/401 错误只自动重发一次，避免网络抖动无限循环）
  const retryCountRef = useRef(0); // 断线/silence 假死的有限退避重连计数（≤3 次）
  // P1 幂等键：每次新发送生成一个 client_request_id，401/断线重试复用同 ID（后端唯一约束原子去重）
  const requestIdRef = useRef(null);
  const firstQuestionRef = useRef('');
  const topicRefs = useRef([]);
  // 用 ref 存 sessionId，避免闭包陷阱
  const sessionIdRef = useRef(sessionId);
  // 本次请求待提交的 sessionId：收到 session 事件先只写 ref，流结束/中止再统一 setSessionId
  // （避免流式中途 setSessionId → App effect loadSession → setMessages 覆盖正在流式渲染的占位气泡，
  //   导致"同一问题多次出现 / 连接异常中断 / 一直思考中"）
  const pendingSessionIdRef = useRef(null);
  // 流式状态：answer / toolSteps / sources 跨 handleAsk 与 handleConfirm 共享
  const lastAskRef = useRef(null); // 防重复触发：同问题 1s 内二次触发丢弃
  const streamStateRef = useRef({ answer: '', toolSteps: [], sources: [], t0: Date.now() });

  // 流结束（onclose / onerror / catch / 手动停止）统一提交 sessionId，避免中途触发 App loadSession
  const commitPendingSessionId = () => {
    if (pendingSessionIdRef.current) {
      const sid = pendingSessionIdRef.current;
      pendingSessionIdRef.current = null;
      setSessionId(sid);
    }
  };

  // 同步 ref 和 state：sessionId 有值 → 写 ref；null（newChat / loadSession 404 降级）→
  // 清 ref + 待提交 id + localStorage 归属，统一"新对话"语义。
  // 之前"null 不覆盖 ref"是为了防打断竞态丢归属；现在后端 _qsess 归属兜底 + is_new_chat
  // 标记可防碎片化，此处改为彻底清空，根治"新对话框带老 session"。
  useEffect(() => {
    if (sessionId) {
      sessionIdRef.current = sessionId;
    } else {
      sessionIdRef.current = null;
      pendingSessionIdRef.current = null;
      try {
        const _uid = JSON.parse(localStorage.getItem('user') || 'null')?.id || null;
        const _key = _uid ? `chat_session_id_${_uid}` : 'chat_session_id';
        localStorage.removeItem(_key);
      } catch (e) {}
    }
  }, [sessionId]);

  // 安全保护：确保 messages 始终是数组
  const safeMessages = Array.isArray(messages) ? messages : [];

  // 提取所有用户问题作为对话目录
  const topics = safeMessages
    .map((m, i) => ({ role: m.role, content: m.content, index: i }))
    .filter(m => m.role === 'user');

  // 滚动时更新当前高亮的话题
  useEffect(() => {
    const handleScroll = () => {
      if (!scrollRef.current || topics.length === 0) return;
      const scrollTop = scrollRef.current.scrollTop;
      const viewH = scrollRef.current.clientHeight;

      // 找到当前可见区域中间位置对应的话题
      let current = 0;
      topics.forEach((topic, idx) => {
        const el = topicRefs.current[topic.index];
        if (el) {
          const rect = el.getBoundingClientRect();
          const containerRect = scrollRef.current.getBoundingClientRect();
          const relativeTop = rect.top - containerRect.top;
          if (relativeTop < viewH / 2) {
            current = idx;
          }
        }
      });
      setActiveTopic(current);
    };

    const container = scrollRef.current;
    if (container) {
      container.addEventListener('scroll', handleScroll);
      return () => container.removeEventListener('scroll', handleScroll);
    }
  }, [safeMessages, topics.length]);

  // 点击跳转到对应话题
  const scrollToTopic = (topicIndex) => {
    const topic = topics[topicIndex];
    if (!topic) return;
    const el = topicRefs.current[topic.index];
    if (el && scrollRef.current) {
      const containerRect = scrollRef.current.getBoundingClientRect();
      const elRect = el.getBoundingClientRect();
      const offsetTop = elRect.top - containerRect.top + scrollRef.current.scrollTop - 80;
      scrollRef.current.scrollTo({ top: offsetTop, behavior: 'smooth' });
    }
  };

  // 从用户问题生成简短标题（截取前 20 个字符）
  const generateTitle = (q) => {
    if (!q) return '新对话';
    const trimmed = q.trim().replace(/\s+/g, ' ');
    return trimmed.length > 20 ? trimmed.slice(0, 20) + '...' : trimmed;
  };

  // 更新会话标题到后端和侧边栏
  const updateSessionTitle = async (sid, title) => {
    if (!sid || !title) return;
    try {
      await authFetch(`${import.meta.env.VITE_API_BASE}/api/chat/sessions/${sid}`, {
        method: 'PUT',
        headers: authHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ title })
      });
      // 更新侧边栏会话列表
      setSessionsList(prev => {
        const list = Array.isArray(prev) ? prev : [];
        const idx = list.findIndex(s => s.id === sid);
        if (idx >= 0) {
          const newList = [...list];
          newList[idx] = { ...newList[idx], title };
          return newList;
        }
        // 新会话，加到最前面
        return [{ id: sid, title }, ...list];
      });
    } catch (e) {
      console.error('更新会话标题失败', e);
    }
  };

  useEffect(() => {
    scrollRef.current?.scrollTo(0, scrollRef.current.scrollHeight);
  }, [safeMessages, loading]);

  // 「回到最新」箭头：监听滚动，离开底部 80px 时显示，贴底时隐藏
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const onScroll = () => {
      setShowDownArrow(el.scrollHeight - el.scrollTop - el.clientHeight > 80);
    };
    el.addEventListener('scroll', onScroll);
    onScroll();
    return () => el.removeEventListener('scroll', onScroll);
  }, []);

  const scrollToBottom = () => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' });
  };

  // 加载已有会话时，从会话列表获取标题
  useEffect(() => {
    if (sessionId && sessionsList) {
      const session = sessionsList.find(s => s.id === sessionId);
      if (session?.title) {
        setChatTitle(session.title);
      }
    }
  }, [sessionId, sessionsList]);

  const handleAsk = async (overrideQuestion, isRetry = false) => {
    const q = typeof overrideQuestion === 'string' ? overrideQuestion : question;
    if (!q.trim()) return;
    // 防重复触发：同一问题 1 秒内重复触发（回车+发送按钮双事件 / 连按回车）→ 直接丢弃，
    // 避免对刚发出的请求再补一发"打断"，形成打断循环最后卡死
    const _askNow = Date.now();
    if (lastAskRef.current && lastAskRef.current.q === q && _askNow - lastAskRef.current.t < 1000) return;
    lastAskRef.current = { q, t: _askNow };
    // P1 幂等键：新提问生成新 ID；重试（401 续期/断线单次重发）复用同一 ID → 后端 ON CONFLICT 原子去重
    if (!isRetry) {
      requestIdRef.current = (typeof crypto !== 'undefined' && crypto.randomUUID)
        ? crypto.randomUUID()
        : 'rid-' + Date.now() + '-' + Math.random().toString(36).slice(2);
      retryCountRef.current = 0; // 新提问重置断线重连计数
    }
    // 打断模式：回答中发送新问题 → 自动中断当前回答（已生成内容正常落库），
    // 稍候等旧 SSE 迭代器 finally 释放后端会话锁，再发新问题（避免并发被拒）
    if (sendingRef.current || loading) {
      // 只 abort 不置空 abortRef：旧流 catch/onclose 靠 `abortRef.current !== currentController`
      // 判断是否收尾（commitPendingSessionId 提交 sessionId）。若提前置 null，收尾被跳过 →
      // store sessionId 永不提交 → 下次请求 session=None → 每问都建新会话（历史碎片化重答）。
      // 新请求 line221 会覆盖 abortRef.current，旧流后续事件自然被代际门+render 检查拦住。
      if (abortRef.current) abortRef.current.abort();
      await new Promise(r => setTimeout(r, 300));
    }
    sendingRef.current = true;

    // P1 修复：发送前从 localStorage 兜底恢复会话归属。store/ref 初始化失败、
    // HMR 重挂载或刷新后恢复链路异常时 sessionIdRef 可能为空 → 每问都成新会话
    // → 后端历史为空 → 追问（如"会下雨吗？"）被 prefilter 固定话术顶掉。
    if (!sessionIdRef.current) {
      try {
        const _uid = JSON.parse(localStorage.getItem('user') || 'null')?.id || null;
        const _key = _uid ? `chat_session_id_${_uid}` : 'chat_session_id';
        const _sid = localStorage.getItem(_key);
        if (_sid) sessionIdRef.current = _sid;
      } catch { /* localStorage 不可用时忽略，按新会话处理 */ }
    }
    // 记录发请求前是不是新会话（sessionId 为 null 说明是新对话）
    const isNewSession = !sessionIdRef.current;

    setQuestion('');
    // 断线重试（isRetry=true）不追加气泡：气泡已存在，续流 append（resume 语义）
    if (!isRetry) {
      setMessages(prev => [...(Array.isArray(prev) ? prev : []), { role: 'user', content: q }, { role: 'assistant', content: '', sources: [], toolSteps: [] }]);
    }
    setLoading(true);
    setConfirmCard(null);
    const currentController = new AbortController();
    abortRef.current = currentController;

    // 每次新提问重置流式状态（含工具过程），记录开始时间用于「已思考（用时X秒）」
    // 断线重试不重置：保留已收 answer/toolSteps/sources，续流继续 append（resume 语义）
    if (!isRetry) {
      streamStateRef.current = { answer: '', toolSteps: [], sources: [], t0: Date.now(), gen: undefined }; // P1: gen=undefined 代际未锁定
    }
    pendingSessionIdRef.current = null; // 本次请求的 sessionId 待流结束提交
    let rafId = null;
    let silenceTimer = null;
    const resetSilenceTimer = () => {
      if (silenceTimer) clearTimeout(silenceTimer);
      silenceTimer = setTimeout(() => {
        if (abortRef.current === currentController) {
          currentController.abort();
          commitPendingSessionId();
          if (retryCountRef.current < 3) {
            // 链路假死 → 走断线退避重连（复用同幂等键 + is_retry=true，后端 _dup_guard 放行）
            const attempt = retryCountRef.current + 1;
            retryCountRef.current = attempt;
            const delay = Math.min(3000 * Math.pow(2, attempt - 1), 12000);
            console.warn(`响应超时（120 秒无事件），第 ${attempt} 次退避重连（${delay}ms）`);
            // 断线重连保留占位气泡（已渲染前半段），续流 append 不丢内容
            setLoading(false);
            sendingRef.current = false;
            setTimeout(() => { handleAsk(q, true); }, delay);
          } else {
            message.warning('响应超时（120 秒无响应），已停止');
            setLoading(false);
            sendingRef.current = false;
          }
        }
      }, 120000); // 联网/深度思考链路可能 20~90s，60s 太紧会误杀
    };
    resetSilenceTimer();

    const render = () => {
      if (abortRef.current !== currentController) return;
      const st = streamStateRef.current;
      setMessages(prev => {
        const arr = Array.isArray(prev) ? [...prev] : [];
        arr[arr.length - 1] = { role: 'assistant', content: st.answer, sources: st.sources, toolSteps: [...st.toolSteps], route: st.route, routeDetail: st.routeDetail, confidence: st.confidence, elapsedSec: Math.max(1, Math.round((Date.now() - (st.t0 || Date.now())) / 1000)) };
        return arr;
      });
    };

    const scheduleRender = () => {
      if (rafId) cancelAnimationFrame(rafId);
      rafId = requestAnimationFrame(render);
    };

    await fetchEventSource(`${import.meta.env.VITE_API_BASE}/api/chat/stream`, {
      method: 'POST',
      headers: authHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ question: q, session_id: sessionIdRef.current, client_request_id: requestIdRef.current, web_search: webSearch, deep_think: deepThink, is_retry: isRetry, is_new_chat: isNewSession }),
      signal: currentController.signal,
      retry: () => null, // 彻底禁用自动重连：流中断不重发请求，避免后端重复入库
      onopen(response) {
        // 401 登录过期：抛标记错误（fetch-event-source 对非 SSE 响应进 onerror，且其错误对象无 status 字段）
        if (response.status === 401) throw new Error('AUTH_401');
        // 429 限流：抛标记错误 → onerror 分支提示稍后再试，不重发、不登出
        if (response.status === 429) throw new Error('RATE_LIMITED');
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
      },
      onmessage(ev) {
        resetSilenceTimer();
        try {
          const data = JSON.parse(ev.data);
          const st = streamStateRef.current;
          // P1 代际门：session 事件是权威代际来源，锁定 st.gen；未锁定前丢弃其余事件
          // （防旧流残余 token 污染新流）；锁定后丢弃 generation 不匹配的旧代际残余。
          // 老后端事件无 generation 时退化为不过滤（st.gen=0 且事件 generation 均 null）。
          if (data.type === 'session') {
            if (data.generation != null) st.gen = data.generation;
            else st.gen = 0;
          } else {
            if (st.gen === undefined) return;
            if (data.generation != null && data.generation !== st.gen) return;
          }
          if (data.type === 'token') {
            st.answer += data.content;
            scheduleRender();
          }
          else if (data.type === 'content_blocked') {
            // 输出内容审核拦截：覆盖已累积的流式内容
            st.answer = '该回答未通过内容安全审核，已拦截显示。';
            scheduleRender();
          }
          else if (data.type === 'context') st.sources = data.contexts || [];
          else if (data.type === 'route') {
            // 路由结果透传：来源标签 + 置信度（super_router 节点发出）
            st.route = data.route;
            st.routeDetail = data.route_detail;
            st.confidence = data.confidence;
            scheduleRender();
          }
          else if (data.type === 'thinking') {
            if (data.step_kind === 'reasoning') {
              // 推理全文：流式累积到同一条 reasoning 步骤（展开显示整段思考），避免碎行
              const last = st.toolSteps[st.toolSteps.length - 1];
              if (last && last.kind === 'thinking' && last.reason) {
                last.summary = (last.summary || '') + (data.content || '');
              } else {
                st.toolSteps.push({ kind: 'thinking', reason: true, tool: '思考', summary: data.content || '', error: null });
              }
            } else {
              st.toolSteps.push({ kind: 'thinking', step_kind: data.step_kind || '', tool: data.tool || '思考', url: data.url || null, summary: data.content || '', error: null });
            }
            scheduleRender();
          }
          else if (data.type === 'tool_result') {
            st.toolSteps.push({
              tool: data.tool,
              success: data.success,
              summary: data.summary,
              error: data.error,
            });
            scheduleRender();
          }
          else if (data.type === 'tool_confirm') {
            // 高风险工具：弹出确认卡，等待用户在卡上确认/拒绝
            setConfirmCard({
              tool: data.tool,
              args: data.args,
              description: data.description,
              risk_level: data.risk_level,
            });
          }
          else if (data.type === 'error') {
            message.error(data.error || '服务异常');
            // error 事件后流可能立即关闭：同步复位状态，避免 loading 卡死导致后续提问无响应
            setLoading(false);
            sendingRef.current = false;
            if (rafId2) cancelAnimationFrame(rafId2);
          }
          else if (data.type === 'session') {
            // 只写 ref + 记录待提交 id；setSessionId 延迟到流结束（避免中途触发 App loadSession 覆盖流式占位）
            sessionIdRef.current = data.session_id;
            pendingSessionIdRef.current = data.session_id;
            retryCountRef.current = 0; // 收到会话事件 = 重连成功，重置计数
            // 静默持久化（与 useAppStore 同一 key 规则）：流可能异常中断（刷新/断连/超时），
            // 先落 localStorage → 刷新后恢复原会话归属，避免"每次提问都 session=None 新建会话 → 历史碎片化"
            try {
              const _uid = JSON.parse(localStorage.getItem('user') || 'null')?.id || null;
              const _key = _uid ? `chat_session_id_${_uid}` : 'chat_session_id';
              localStorage.setItem(_key, data.session_id);
            } catch (e) {}
            // 只有新会话才生成标题，后续对话不更新标题
            if (isNewSession) {
              const title = generateTitle(q);
              setChatTitle(title);
              updateSessionTitle(data.session_id, title);
            }
          }
        } catch (e) {}
      },
      onclose() {
        if (abortRef.current !== currentController) return;
        clearTimeout(silenceTimer);
        if (rafId) cancelAnimationFrame(rafId);
        render();
        commitPendingSessionId();
        setLoading(false);
        sendingRef.current = false;
        retryCountRef.current = 0; // 流正常结束 → 重置重连计数
      },
      onerror(err) {
        if (abortRef.current !== currentController) return;
        clearTimeout(silenceTimer);
        // 429：限流 → 提示稍后再试，不重发、不登出（后端带 Retry-After，前端不自动重试）
        if (err && err.message === 'RATE_LIMITED') {
          message.error('请求过于频繁，请稍后再试');
          if (rafId) cancelAnimationFrame(rafId);
          render();
          commitPendingSessionId();
          setLoading(false);
          sendingRef.current = false;
          return;
        }
        // 401：token 过期 → 静默续期后重发整个请求（防重入，避免无限重发）
        if (err && err.message === 'AUTH_401') {
          if (retrying401Ref.current) return;
          retrying401Ref.current = true;
          refreshAccessToken().then(ok => {
            retrying401Ref.current = false;
            if (ok) {
              // 401 续期重连保留占位气泡，续流 append 不丢内容
              setLoading(false);
              sendingRef.current = false;
              handleAsk(q, true); // 带新 token 重连（复用同幂等键，后端不重复入库）
            } else {
              clearAuthAndNotify();
              setLoading(false);
              sendingRef.current = false;
              // 双保险：不依赖 auth-expired 事件链（旧 JS/监听丢失时事件可能不到达），
              // token 已清 → App 重新渲染为 LoginPage；location.href 整页刷新彻底兜底
              window.location.href = '/';
            }
          }).catch(() => { retrying401Ref.current = false; setLoading(false); sendingRef.current = false; });
          throw err; // 阻止自动重试（重发由续期成功后的 handleAsk 负责）
        }
        // 其他错误：不抛错（return）→ 禁止 fetchEventSource 自动重连重发同一请求（否则后端重复入库）
        // P2：非 abort/401/429 断线 → 有限退避重连（≤3 次：3s/6s/12s，网络抖动自愈）；
        // 复用同幂等键 + is_retry=true → 后端 _dup_guard 放行（入库幂等兜底不重复）
        if (err && err.name !== 'AbortError' && retryCountRef.current < 3) {
          const attempt = retryCountRef.current + 1;
          retryCountRef.current = attempt;
          const delay = Math.min(3000 * Math.pow(2, attempt - 1), 12000);
          console.warn(`SSE 连接异常，第 ${attempt} 次退避重连（${delay}ms）`, err);
          // 断线重连保留占位气泡，续流 append 不丢内容
          setLoading(false);
          sendingRef.current = false;
          setTimeout(() => { handleAsk(q, true); }, delay); // 断线重发（复用同幂等键 + is_retry）
          return;
        }
        console.error(err);
        if (rafId) cancelAnimationFrame(rafId);
        render();
        commitPendingSessionId();
        setLoading(false);
        sendingRef.current = false;
        throw err; // 重试后仍失败/abort：不再自动重发，由用户手动操作（后端入库幂等去重兜底）
      },
    }).catch((err) => {
      if (abortRef.current !== currentController) return;
      // 401 已在 onopen/onerror 处理（续期重发或跳转登录），这里静默收尾
      if (err && err.message === 'AUTH_401') {
        if (rafId) cancelAnimationFrame(rafId);
        commitPendingSessionId();
        setLoading(false);
        sendingRef.current = false;
        return;
      }
      if (err.name !== 'AbortError') {
        console.error(err);
        message.error('连接中断，请重试');
      }
      if (rafId) cancelAnimationFrame(rafId);
      commitPendingSessionId();
      setLoading(false);
      sendingRef.current = false;
    });
  };

  const handleFeedback = (index, type) => {
    setMessages(prev => (Array.isArray(prev) ? prev.map((msg, i) => i === index ? { ...msg, feedback: type } : msg) : prev));
  };

  const handleRegenerate = (index) => {
    if (loading) { message.warning('正在生成回答，请稍候...'); return; }
    const userMsg = safeMessages[index - 1];
    if (!userMsg || userMsg.role !== 'user') return;
    const question = userMsg.content;
    setMessages(prev => (Array.isArray(prev) ? prev.slice(0, index - 1) : []));
    setTimeout(() => handleAsk(question), 100);
  };

  const handleStop = () => {
    if (abortRef.current) { abortRef.current.abort(); abortRef.current = null; }
    commitPendingSessionId(); // 手动停止也要把本次会话 id 提交（否则刷新/再次提问会丢失会话归属）
    setLoading(false);
    sendingRef.current = false;
  };

  // 高风险工具人工确认：批准/拒绝后从断点续流
  const handleConfirm = async (approved) => {
    const sid = sessionIdRef.current;
    if (!sid || !confirmCard) return;
    setConfirmCard(null);
    const controller = new AbortController();
    abortRef.current = controller;
    const st = streamStateRef.current;
    let rafId2 = null;

    const render2 = () => {
      if (abortRef.current !== controller) return;
      setMessages(prev => {
        const arr = Array.isArray(prev) ? [...prev] : [];
        const last = arr.length - 1;
        if (last >= 0 && arr[last].role === 'assistant') {
          arr[last] = { ...arr[last], content: st.answer, sources: st.sources, toolSteps: [...st.toolSteps], route: st.route, routeDetail: st.routeDetail, confidence: st.confidence };
        }
        return arr;
      });
    };
    const scheduleRender2 = () => {
      if (rafId2) cancelAnimationFrame(rafId2);
      rafId2 = requestAnimationFrame(render2);
    };

    try {
      await fetchEventSource(`${import.meta.env.VITE_API_BASE}/api/chat/confirm`, {
        method: 'POST',
        headers: authHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ session_id: sid, approved }),
        signal: controller.signal,
        onmessage(ev) {
          try {
            const data = JSON.parse(ev.data);
            // P1 代际门（续流）：st.gen 由主流 session 锁定；代际不匹配的残余事件丢弃
            if (st.gen === undefined) return;
            if (data.generation != null && data.generation !== st.gen) return;
            if (data.type === 'token') {
              st.answer += data.content;
              scheduleRender2();
            } else if (data.type === 'content_blocked') {
              st.answer = '该回答未通过内容安全审核，已拦截显示。';
              scheduleRender2();
            } else if (data.type === 'thinking') {
              if (data.step_kind === 'reasoning') {
                const last = st.toolSteps[st.toolSteps.length - 1];
                if (last && last.kind === 'thinking' && last.reason) {
                  last.summary = (last.summary || '') + (data.content || '');
                } else {
                  st.toolSteps.push({ kind: 'thinking', reason: true, tool: '思考', summary: data.content || '', error: null });
                }
              } else {
                st.toolSteps.push({ kind: 'thinking', step_kind: data.step_kind || '', tool: data.tool || '思考', url: data.url || null, summary: data.content || '', error: null });
              }
              scheduleRender2();
            } else if (data.type === 'tool_result') {
              st.toolSteps.push({ tool: data.tool, success: data.success, summary: data.summary, error: data.error });
              scheduleRender2();
            } else if (data.type === 'context') {
              st.sources = data.contexts || [];
            } else if (data.type === 'error') {
              message.error(data.error || '续流失败');
              setLoading(false);
            }
          } catch (e) {}
        },
        onclose() {
          if (abortRef.current !== controller) return;
          if (rafId2) cancelAnimationFrame(rafId2);
          render2();
          setLoading(false);
        },
        onerror(err) {
          if (abortRef.current !== controller) return;
          console.error(err);
          if (rafId2) cancelAnimationFrame(rafId2);
          render2();
          setLoading(false);
        },
      });
    } catch (e) {
      if (e.name !== 'AbortError') console.error(e);
      setLoading(false);
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', width: '100%', overflow: 'hidden', position: 'relative' }}>
      {/* 顶部会话标题栏 */}
      {chatTitle && (
        <div style={{
          flexShrink: 0,
          textAlign: 'center',
          padding: '12px 16px 8px',
          borderBottom: '1px solid var(--border-color)',
        }}>
          <div style={{ fontSize: 15, fontWeight: 600, color: 'var(--text-primary)' }}>{chatTitle}</div>
          <div style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 2 }}>AI 生成可能有误 请核实</div>
        </div>
      )}

      <div ref={scrollRef} style={{ flex: 1, overflowY: 'auto', overflowX: 'hidden' }}>
        {loadingSession && (
          <div style={{ textAlign: 'center', padding: 80 }}>
            <Spin size="large" />
            <div style={{ marginTop: 16, color: 'var(--text-tertiary)' }}>正在加载对话...</div>
          </div>
        )}
        {!loadingSession && safeMessages.length === 0 && (
          <div style={{
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
            minHeight: '60vh',
            color: 'var(--text-tertiary)',
          }}>
            <div style={{ fontSize: 24, fontWeight: 600, color: 'var(--text-primary)', marginBottom: 8 }}>
              {t('chat.empty')}
            </div>
            <div style={{ fontSize: 14, color: 'var(--text-tertiary)', marginBottom: 32 }}>
              智能问答助手 · 知识库 · 联网 · 工具 · 多步任务
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'center', gap: 12, maxWidth: 880 }}>
              {[
                { key: 'rag', label: '知识库问答', icon: '📚', q: 'RAG 和 LLM Wiki 的区别' },
                { key: 'tool', label: '联网搜索', icon: '🌐', q: '今天珠海天气怎么样' },
                { key: 'direct', label: '直接对话', icon: '💬', q: '天空为什么是蓝色的' },
                { key: 'loop', label: '深度任务', icon: '🧠', q: '帮我规划周末珠海两日游并对比酒店' },
              ].map(g => (
                <div key={g.key} style={{
                  width: 190, padding: '14px 16px', borderRadius: 14, cursor: 'pointer',
                  background: 'var(--bg-secondary)', border: '1px solid var(--border-color)',
                  transition: 'all 0.2s ease',
                }} onClick={() => handleAsk(g.q)} className="quick-card">
                  <div style={{ fontSize: 18, marginBottom: 6 }}>{g.icon}</div>
                  <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)', marginBottom: 4 }}>{g.label}</div>
                  <div style={{ fontSize: 12, color: 'var(--text-tertiary)', lineHeight: 1.5 }}>{g.q}</div>
                </div>
              ))}
            </div>
          </div>
        )}
        {safeMessages.length > 0 && (
          <div style={{ paddingTop: 24, paddingBottom: 16, paddingRight: 40 }}>
            {safeMessages.map((m, i) => (
              <div key={i} ref={m.role === 'user' ? (el) => (topicRefs.current[i] = el) : null}>
                <MemoMessageBubble m={m} onRegenerate={handleRegenerate} onFeedback={handleFeedback} index={i} isStreaming={loading && i === safeMessages.length - 1} onOpenSources={(sources) => setRefDrawer({ open: true, sources })} />
              </div>
            ))}
          </div>
        )}
        {/* 高风险工具人工确认卡 */}
        {confirmCard && (
          <div style={{ maxWidth: 720, margin: '0 auto 12px', padding: '0 16px' }}>
            <Alert
              type="warning"
              showIcon
              icon={<SafetyCertificateOutlined />}
              message={
                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <WarningOutlined style={{ color: '#d97706' }} />
                  <span>高风险工具确认：{confirmCard.tool}</span>
                  <Tag color="orange" style={{ marginLeft: 4 }}>需人工确认</Tag>
                </div>
              }
              description={
                <div>
                  <div style={{ marginBottom: 6, color: 'var(--text-secondary)' }}>{confirmCard.description}</div>
                  {confirmCard.args && Object.keys(confirmCard.args).length > 0 && (
                    <div style={{ marginBottom: 8 }}>
                      <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginBottom: 4 }}>参数预览</div>
                      <pre style={{ margin: 0, padding: 8, background: 'var(--bg-secondary)', borderRadius: 8, fontSize: 12, overflowX: 'auto' }}>{JSON.stringify(confirmCard.args, null, 2)}</pre>
                    </div>
                  )}
                  <Space>
                    <Button type="primary" size="small" icon={<CheckCircleOutlined />} onClick={() => handleConfirm(true)}>
                      批准执行
                    </Button>
                    <Button size="small" danger icon={<CloseCircleOutlined />} onClick={() => handleConfirm(false)}>
                      拒绝
                    </Button>
                  </Space>
                </div>
              }
            />
          </div>
        )}
        {loading && safeMessages.length > 0 && (
          <div style={{ color: 'var(--text-tertiary)', fontSize: 14, paddingLeft: 4 }}>
            <Spin size="small" style={{ marginRight: 8 }} />
            {t('chat.thinking')}
          </div>
        )}
      </div>

      {/* 引用来源右侧栏（不折叠，点开即看） */}
      <Drawer
        title={`引用来源（${refDrawer?.sources?.length || 0} 条）`}
        placement="right"
        size={460}
        open={!!refDrawer?.open}
        onClose={() => setRefDrawer(null)}
      >
        {(refDrawer?.sources || []).map((s, idx) => (
          <div key={idx} style={{ marginBottom: 14, padding: '12px 14px', borderRadius: 10, background: 'rgba(127, 127, 127, 0.05)', border: '1px solid var(--border-color)' }}>
            <div style={{ display: 'flex', gap: 10, alignItems: 'flex-start' }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                  <span style={{ fontSize: 13, color: 'var(--accent-color)', fontWeight: 600 }}>{extractSite(s) || '来源'}</span>
                  {s.date && <span style={{ fontSize: 12, color: 'var(--text-tertiary)', flexShrink: 0 }}>{s.date}</span>}
                </div>
                {s.url ? (
                  <a href={s.url} target="_blank" rel="noopener noreferrer" style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-primary)', textDecoration: 'none', lineHeight: 1.5, display: 'block' }}>
                    {s.title}
                  </a>
                ) : (
                  <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-primary)', lineHeight: 1.5 }}>{s.title}</div>
                )}
                {s.content && (
                  <div style={{ fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.6, marginTop: 4, display: '-webkit-box', WebkitLineClamp: 3, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>{s.content}</div>
                )}
                <div style={{ marginTop: 6, display: 'flex', gap: 12, alignItems: 'center' }}>
                  {s.url && (
                    <a href={s.url} target="_blank" rel="noopener noreferrer" style={{ fontSize: 12, color: 'var(--accent-color)', textDecoration: 'none' }}>
                      打开原文 ↗
                    </a>
                  )}
                  {s.doc_id && (
                    <Button size="small" type="link" icon={<FileTextOutlined />} style={{ paddingLeft: 0, height: 'auto', fontSize: 12 }} onClick={() => { setRefDrawer(null); navigate(`/docs?doc=${s.doc_id}`); }}>
                      在知识库中查看
                    </Button>
                  )}
                </div>
              </div>
            </div>
          </div>
        ))}
        {(refDrawer?.sources || []).length === 0 && (
          <div style={{ color: 'var(--text-tertiary)', textAlign: 'center', paddingTop: 40 }}>该回答没有引用来源</div>
        )}
      </Drawer>

      {/* 回到最新：向上滚动离开底部时右下角悬浮箭头 */}
      {showDownArrow && safeMessages.length > 0 && (
        <div style={{
          position: 'absolute',
          right: 28,
          bottom: 100,
          zIndex: 9,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
        }}>
          <Button
            shape="circle"
            icon={<DownOutlined />}
            onClick={scrollToBottom}
            style={{
              width: 36, height: 36,
              background: 'var(--bg-primary)',
              border: '1px solid #7aa6ff',
              boxShadow: '0 2px 10px rgba(122,166,255,0.25)',
              color: '#4d8dff',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}
            title="回到最新"
          />
        </div>
      )}

      {/* 右侧对话目录（悬浮） */}
      {topics.length > 1 && (
        <div style={{
          position: 'absolute',
          right: 8,
          top: '50%',
          transform: 'translateY(-50%)',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          gap: 8,
          zIndex: 10,
        }}
        className="chat-topics"
        >
          {topics.map((topic, idx) => (
            <div
              key={idx}
              style={{
                width: idx === activeTopic ? 8 : 6,
                height: idx === activeTopic ? 8 : 6,
                borderRadius: '50%',
                background: idx === activeTopic ? 'var(--accent-color)' : 'var(--border-color)',
                cursor: 'pointer',
                transition: 'all 0.2s ease',
                position: 'relative',
              }}
              onClick={() => scrollToTopic(idx)}
              title={topic.content.slice(0, 30)}
            >
              {/* 悬停展开的文字提示 */}
              <div style={{
                position: 'absolute',
                right: 16,
                top: '50%',
                transform: 'translateY(-50%)',
                background: 'var(--bg-secondary)',
                color: 'var(--text-primary)',
                padding: '6px 12px',
                borderRadius: 8,
                fontSize: 12,
                whiteSpace: 'nowrap',
                maxWidth: 200,
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                opacity: 0,
                pointerEvents: 'none',
                transition: 'opacity 0.2s ease',
                boxShadow: 'var(--input-shadow)',
              }}
              className="topic-tooltip"
              >
                {topic.content.slice(0, 30)}
              </div>
            </div>
          ))}
        </div>
      )}

      <div style={{ flexShrink: 0, paddingTop: 8 }}>
        <ChatInput
          onSend={(q) => handleAsk(q)}
          loading={loading}
          canSend={!loading}
          onStop={handleStop}
          placeholder={t('chat.placeholder')}
          webSearch={webSearch}
          setWebSearch={setWebSearch}
          deepThink={deepThink}
          setDeepThink={setDeepThink}
        />
        <div style={{ textAlign: 'center', fontSize: 12, color: 'var(--text-tertiary)', paddingBottom: 8, paddingTop: 4 }}>
          内容由 AI 生成，请仔细甄别
        </div>
      </div>
    </div>
  );
}

export default ChatPage;
