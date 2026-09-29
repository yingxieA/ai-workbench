import React, { useState, useRef, useEffect } from 'react';
import { fetchEventSource } from '@microsoft/fetch-event-source';
import { Input, Button, message, Spin, Space, Tag, Alert, Tooltip } from 'antd';
import { CheckCircleOutlined, CloseCircleOutlined, ToolOutlined, WarningOutlined, SafetyCertificateOutlined, BulbOutlined, LoadingOutlined } from '@ant-design/icons';
import MemoMessageBubble from '../components/MemoMessageBubble';
import ChatInput from '../components/ChatInput';
import { authHeaders, authFetch, refreshAccessToken, clearAuthAndNotify } from '../utils/api';

const { TextArea } = Input;

function ChatPage({ t, messages, setMessages, sessionId, setSessionId, loadingSession, sessionsList, setSessionsList }) {
  const [question, setQuestion] = useState('');
  const [loading, setLoading] = useState(false);
  const [chatTitle, setChatTitle] = useState('');
  const [activeTopic, setActiveTopic] = useState(0);
  const [confirmCard, setConfirmCard] = useState(null); // 高风险工具人工确认卡
  const scrollRef = useRef(null);
  const abortRef = useRef(null);
  const firstQuestionRef = useRef('');
  const topicRefs = useRef([]);
  // 用 ref 存 sessionId，避免闭包陷阱
  const sessionIdRef = useRef(sessionId);
  // 流式状态：answer / toolSteps / sources 跨 handleAsk 与 handleConfirm 共享
  const streamStateRef = useRef({ answer: '', toolSteps: [], sources: [] });

  // 同步 ref 和 state
  useEffect(() => {
    sessionIdRef.current = sessionId;
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

  // 加载已有会话时，从会话列表获取标题
  useEffect(() => {
    if (sessionId && sessionsList) {
      const session = sessionsList.find(s => s.id === sessionId);
      if (session?.title) {
        setChatTitle(session.title);
      }
    }
  }, [sessionId, sessionsList]);

  const handleAsk = async (overrideQuestion) => {
    const q = typeof overrideQuestion === 'string' ? overrideQuestion : question;
    if (loading || !q.trim()) return;
    if (abortRef.current) { abortRef.current.abort(); abortRef.current = null; }

    // 记录发请求前是不是新会话（sessionId 为 null 说明是新对话）
    const isNewSession = !sessionIdRef.current;

    setQuestion('');
    setMessages(prev => [...(Array.isArray(prev) ? prev : []), { role: 'user', content: q }, { role: 'assistant', content: '', sources: [], toolSteps: [] }]);
    setLoading(true);
    setConfirmCard(null);
    const currentController = new AbortController();
    abortRef.current = currentController;

    // 每次新提问重置流式状态（含工具过程）
    streamStateRef.current = { answer: '', toolSteps: [], sources: [] };
    let rafId = null;
    let silenceTimer = null;
    const resetSilenceTimer = () => {
      if (silenceTimer) clearTimeout(silenceTimer);
      silenceTimer = setTimeout(() => {
        if (abortRef.current === currentController) {
          currentController.abort();
          message.warning('响应超时，请重试');
          setLoading(false);
        }
      }, 60000);
    };
    resetSilenceTimer();

    const render = () => {
      if (abortRef.current !== currentController) return;
      const st = streamStateRef.current;
      setMessages(prev => {
        const arr = Array.isArray(prev) ? [...prev] : [];
        arr[arr.length - 1] = { role: 'assistant', content: st.answer, sources: st.sources, toolSteps: [...st.toolSteps] };
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
      body: JSON.stringify({ question: q, session_id: sessionIdRef.current }),
      signal: currentController.signal,
      onmessage(ev) {
        resetSilenceTimer();
        try {
          const data = JSON.parse(ev.data);
          const st = streamStateRef.current;
          if (data.type === 'token') {
            st.answer += data.content;
            scheduleRender();
          }
          else if (data.type === 'context') st.sources = data.contexts || [];
          else if (data.type === 'thinking') {
            st.toolSteps.push({ kind: 'thinking', tool: '思考', summary: data.content || '', error: null });
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
          }
          else if (data.type === 'session') {
            // 同步更新 ref 和 state
            sessionIdRef.current = data.session_id;
            setSessionId(data.session_id);
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
        setLoading(false);
      },
      onerror(err) {
        if (abortRef.current !== currentController) throw err;
        clearTimeout(silenceTimer);
        // 401：token 过期 → 静默续期后重发整个请求
        if (err && err.status === 401) {
          refreshAccessToken().then(ok => {
            if (ok) {
              setMessages(prev => (Array.isArray(prev) ? prev.slice(0, -1) : [])); // 去掉占位气泡
              setLoading(false);
              handleAsk(q); // 带新 token 重连
            } else {
              clearAuthAndNotify();
              setLoading(false);
            }
          }).catch(() => { setLoading(false); });
          return; // 不抛错，避免 fetchEventSource 自动重试
        }
        console.error(err);
        if (rafId) cancelAnimationFrame(rafId);
        render();
        setLoading(false);
        throw err;
      },
    }).catch((err) => {
      if (abortRef.current !== currentController) return;
      if (err.name !== 'AbortError') console.error(err);
      if (rafId) cancelAnimationFrame(rafId);
      setLoading(false);
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
    setLoading(false);
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
          arr[last] = { ...arr[last], content: st.answer, sources: st.sources, toolSteps: [...st.toolSteps] };
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
            if (data.type === 'token') {
              st.answer += data.content;
              scheduleRender2();
            } else if (data.type === 'thinking') {
              st.toolSteps.push({ kind: 'thinking', tool: '思考', summary: data.content || '', error: null });
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
              基于知识库的 AI 问答助手
            </div>
            <Space wrap style={{ justifyContent: 'center' }}>
              {['RAG 和 LLM Wiki 的区别', '帮我定制 AI 学习路径', '解释 Transformer 架构', '什么是 Agent？'].map(q => (
                <Tag key={q} style={{ cursor: 'pointer', padding: '6px 14px', fontSize: 13, borderRadius: 16 }} onClick={() => handleAsk(q)}>
                  {q}
                </Tag>
              ))}
            </Space>
          </div>
        )}
        {safeMessages.length > 0 && (
          <div style={{ paddingTop: 24, paddingBottom: 16, paddingRight: 40 }}>
            {safeMessages.map((m, i) => (
              <div key={i} ref={m.role === 'user' ? (el) => (topicRefs.current[i] = el) : null}>
                <MemoMessageBubble m={m} onRegenerate={handleRegenerate} onFeedback={handleFeedback} index={i} isStreaming={loading && i === safeMessages.length - 1} />
                {/* 工具过程展示（本次回答调用过工具时） */}
                {m.role === 'assistant' && Array.isArray(m.toolSteps) && m.toolSteps.length > 0 && (
                  <div style={{ maxWidth: 720, margin: '4px 0 12px 96px', display: 'flex', flexDirection: 'column', gap: 6 }}>
                    {m.toolSteps.map((s, si) => {
                      const isThinking = s.kind === 'thinking';
                      const bg = isThinking ? 'rgba(139,92,246,0.07)' : (s.success ? 'rgba(22,163,74,0.06)' : 'rgba(239,68,68,0.06)');
                      const bd = isThinking ? 'rgba(139,92,246,0.3)' : (s.success ? 'rgba(22,163,74,0.25)' : 'rgba(239,68,68,0.25)');
                      return (
                        <div key={si} style={{ display: 'flex', alignItems: 'flex-start', gap: 8, background: bg, border: `1px solid ${bd}`, borderRadius: 10, padding: '8px 12px', fontSize: 13 }}>
                          {isThinking
                            ? <LoadingOutlined style={{ color: '#8b5cf6', marginTop: 3 }} />
                            : (s.success ? <CheckCircleOutlined style={{ color: '#16a34a', marginTop: 3 }} /> : <CloseCircleOutlined style={{ color: '#ef4444', marginTop: 3 }} />)}
                          <div style={{ minWidth: 0 }}>
                            <div style={{ color: 'var(--text-primary)', fontWeight: 500, display: 'flex', alignItems: 'center', gap: 6 }}>
                              {isThinking ? <><BulbOutlined /> 思考过程</> : <><ToolOutlined /> 工具 · {s.tool}</>}
                              {!isThinking && (
                                <Tag style={{ marginLeft: 4, fontSize: 11, lineHeight: '16px' }} color={s.success ? 'green' : 'red'}>
                                  {s.success ? '成功' : '失败'}
                                </Tag>
                              )}
                            </div>
                            {s.error ? (
                              <div style={{ color: '#ef4444', marginTop: 2, wordBreak: 'break-all' }}>{s.error}</div>
                            ) : (
                              <div style={{ color: 'var(--text-secondary)', marginTop: 2, wordBreak: 'break-all', whiteSpace: 'pre-wrap' }}>{s.summary}</div>
                            )}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
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
        />
        <div style={{ textAlign: 'center', fontSize: 12, color: 'var(--text-tertiary)', paddingBottom: 8, paddingTop: 4 }}>
          内容由 AI 生成，请仔细甄别
        </div>
      </div>
    </div>
  );
}

export default ChatPage;
