import React from 'react';
import { Button, message, Space } from 'antd';
import { CopyOutlined, ReloadOutlined, LikeOutlined, DislikeOutlined } from '@ant-design/icons';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeRaw from 'rehype-raw';
import { MarkdownComponents } from './MarkdownComponents';
import { authHeaders, authFetch } from '../utils/api';

export function splitCommitted(text) {
  const fenceCount = (text.match(/```/g) || []).length;
  if (fenceCount % 2 === 0) {
    return { committed: text, uncommitted: '' };
  }
  const lastFence = text.lastIndexOf('```');
  return {
    committed: text.slice(0, lastFence),
    uncommitted: text.slice(lastFence)
  };
}

function cleanMarkdown(text) {
  if (!text) return '';
  let cleaned = text;
  cleaned = cleaned.replace(/<br\s*\/?>/gi, '\n');
  cleaned = cleaned.replace(/^(\s*)[\*\-\+]\s+/gm, '$1- ');
  return cleaned;
}

const MemoMessageBubble = React.memo(({ m, onRegenerate, index, onFeedback, isStreaming }) => {
  const { committed, uncommitted } = splitCommitted(m.content || '');
  return (
    <div style={{ display: 'flex', justifyContent: m.role === 'user' ? 'flex-end' : 'flex-start', marginBottom: 16 }}>
      <div className={m.role === 'assistant' ? 'markdown-body assistant-bubble' : ''} style={{
        maxWidth: m.role === 'user' ? '70%' : '100%',
        padding: '12px 16px',
        borderRadius: m.role === 'user' ? '16px 16px 4px 16px' : '16px 16px 16px 4px',
        background: m.role === 'user' ? 'var(--user-bubble-bg)' : 'var(--assistant-bubble-bg)',
        color: m.role === 'user' ? 'var(--user-bubble-text)' : 'var(--text-primary)',
        border: 'none',
        lineHeight: 1.6,
        fontSize: 14
      }}>
        {m.role === 'user' ? (
          m.content
        ) : (
          <>
            <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeRaw]} components={MarkdownComponents}>
              {cleanMarkdown(committed).replace(/\[\[(\d+)\]\]/g, '<sup class="cite">[$1]</sup>')}
            </ReactMarkdown>
            {uncommitted && (
              <pre style={{ whiteSpace: 'pre-wrap', background: '#282c34', color: '#abb2bf', padding: 12, borderRadius: 8, fontSize: 13, margin: '8px 0' }}>{uncommitted}</pre>
            )}
          </>
        )}
        {m.role === 'assistant' && m.sources && m.sources.length > 0 && (
          <div style={{ marginTop: 8, fontSize: 13 }}>
            <details>
              <summary style={{ cursor: 'pointer', color: 'var(--accent-color)', fontWeight: 600 }}>
                📎 引用来源（{m.sources.length} 条）
              </summary>
              <div style={{ marginTop: 8, background: 'var(--bg-secondary)', padding: 12, borderRadius: 8 }}>
                {m.sources.map((s, idx) => (
                  <div key={idx} style={{ marginBottom: 8, borderBottom: '1px solid var(--border-color)', paddingBottom: 8 }}>
                    <strong>[{s.index}] {s.title}</strong>
                    <p style={{ color: 'var(--text-secondary)', margin: '4px 0 0 0' }}>{s.content}</p>
                  </div>
                ))}
              </div>
            </details>
          </div>
        )}
        {m.role === 'assistant' && !isStreaming && (
          <div style={{ marginTop: 8, display: 'flex', gap: 4 }} className="action-bar">
            <Button type="text" size="small" icon={<CopyOutlined />} onClick={() => { navigator.clipboard.writeText(m.content); message.success('已复制'); }} />
            <Button type="text" size="small" icon={<ReloadOutlined />} onClick={() => onRegenerate(index)} />
            <Button type="text" size="small" icon={<LikeOutlined />} style={{ color: m.feedback === 'like' ? '#52c41a' : undefined }} onClick={() => {
              authFetch(`${import.meta.env.VITE_API_BASE}/api/chat/messages/${m.id}/feedback`, { method: 'POST', headers: authHeaders({ 'Content-Type': 'application/json' }), body: JSON.stringify({ type: 'like' }) }).then(() => {
                message.success('已点赞');
                onFeedback(index, 'like');
              });
            }} />
            <Button type="text" size="small" icon={<DislikeOutlined />} style={{ color: m.feedback === 'dislike' ? '#ff4d4f' : undefined }} onClick={() => {
              authFetch(`${import.meta.env.VITE_API_BASE}/api/chat/messages/${m.id}/feedback`, { method: 'POST', headers: authHeaders({ 'Content-Type': 'application/json' }), body: JSON.stringify({ type: 'dislike' }) }).then(() => {
                message.info('已点踩');
                onFeedback(index, 'dislike');
              });
            }} />
          </div>
        )}
      </div>
    </div>
  );
});

export default MemoMessageBubble;
