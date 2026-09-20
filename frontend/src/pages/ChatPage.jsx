import React, { useState, useRef, useEffect } from 'react';
import { fetchEventSource } from '@microsoft/fetch-event-source';
import { Input, Button, message, Spin, Space, Tag } from 'antd';
import MemoMessageBubble from '../components/MemoMessageBubble';
import ChatInput from '../components/ChatInput';

const { TextArea } = Input;

function ChatPage({ t, messages, setMessages, sessionId, setSessionId, loadingSession, sessionsList, setSessionsList }) {
  const [question, setQuestion] = useState('');
  const [loading, setLoading] = useState(false);
  const [chatTitle, setChatTitle] = useState('');
  const [activeTopic, setActiveTopic] = useState(0);
  const scrollRef = useRef(null);
  const abortRef = useRef(null);
  const firstQuestionRef = useRef('');
  const topicRefs = useRef([]);

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
      await fetch(`${import.meta.env.VITE_API_BASE}/api/chat/sessions/${sid}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
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

    setQuestion('');
    setMessages(prev => [...(Array.isArray(prev) ? prev : []), { role: 'user', content: q }, { role: 'assistant', content: '', sources: [] }]);
    setLoading(true);
    const currentController = new AbortController();
    abortRef.current = currentController;

    let answer = '';
    let sources = [];
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
      setMessages(prev => {
        const arr = Array.isArray(prev) ? [...prev] : [];
        arr[arr.length - 1] = { role: 'assistant', content: answer, sources };
        return arr;
      });
    };

    const scheduleRender = () => {
      if (rafId) cancelAnimationFrame(rafId);
      rafId = requestAnimationFrame(render);
    };

    await fetchEventSource(`${import.meta.env.VITE_API_BASE}/api/chat/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question: q, session_id: sessionId }),
      signal: currentController.signal,
      onmessage(ev) {
        resetSilenceTimer();
        try {
          const data = JSON.parse(ev.data);
          if (data.type === 'token') {
            answer += data.content;
            scheduleRender();
          }
          else if (data.type === 'context') sources = data.contexts || [];
          else if (data.type === 'session') {
            setSessionId(data.session_id);
            // 首次对话，自动生成标题
            const title = generateTitle(q);
            setChatTitle(title);
            updateSessionTitle(data.session_id, title);
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
              </div>
            ))}
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
