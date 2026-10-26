import React from 'react';
import { Button, message, Space, Tag } from 'antd';
import { CopyOutlined, ReloadOutlined, LikeOutlined, DislikeOutlined, FileTextOutlined, BulbOutlined, DownOutlined } from '@ant-design/icons';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeRaw from 'rehype-raw';
import rehypeKatex from 'rehype-katex';
import 'katex/dist/katex.min.css';
import { MarkdownComponents } from './MarkdownComponents';
import { authHeaders, authFetch } from '../utils/api';

// 路由来源标签映射（super_router 透传的 route）
const ROUTE_META = {
  direct: { label: '💬 直接回答', color: 'blue' },
  rag: { label: '📚 知识库', color: 'green' },
  tool: { label: '🔧 工具调用', color: 'orange' },
  agent_loop: { label: '🧠 深度推理', color: 'purple' },
};

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
  // LaTeX 括号公式 → KaTeX 美元符分隔符（remark-math 以 $ / $$ 识别，模型常输出 \(...\) / \[...\]）
  cleaned = cleaned.replace(/\\\[([\s\S]*?)\\\]/g, '$$\n$1\n$$'); // 块级 \[...\] → $$...$$
  cleaned = cleaned.replace(/\\\(([\s\S]*?)\\\)/g, '$$$1$$');        // 行内 \(...\) → $...$
  return cleaned;
}

// DeepSeek 同款：小节标题自动配图标（按标题语义关键词匹配，无命中用 📌）
const HEADING_ICON_RULES = [
  [/核心|本质|概念|定义|什么是|范式|原理/, '💡'],
  [/架构|流程|工作流|机制|环节|运作|链路/, '🏗️'],
  [/对比|区别|差异|一览|比较|维度|对照/, '📊'],
  [/优势|劣势|优缺点|局限|优点|缺点|优劣/, '⚖️'],
  [/适用场景|应用场景|场景|落地/, '🎯'],
  [/总结|小结|结论|收束/, '✅'],
  [/注意|提示|风险|警告|注意事项/, '⚠️'],
  [/示例|例子|实战|案例/, '🧪'],
  [/步骤|方法|操作|如何|怎么|指南/, '🧭'],
  [/代码|实现|工程|源码/, '💻'],
];
function headingIcon(text) {
  const t = String(text || '').replace(/^[一二三四五六七八九十]+[、.．\s]*/, '').trim();
  for (const [re, icon] of HEADING_ICON_RULES) {
    if (re.test(t)) return icon;
  }
  return '📌';
}
function Heading({ level, children, ...props }) {
  const text = (() => {
    let s = '';
    try { s = String(children || '').replace(/<[^>]+>/g, ''); } catch (e) { s = ''; }
    return s.trim();
  })();
  const icon = headingIcon(text);
  const size = level === 1 ? 19 : level === 2 ? 17 : 15;
  const Tag = `h${level}`;
  return (
    <Tag {...props} style={{
      display: 'flex', alignItems: 'center', gap: 8,
      fontSize: size, fontWeight: 700, lineHeight: 1.4,
      margin: level === 1 ? '20px 0 10px' : level === 2 ? '20px 0 10px' : '14px 0 8px',
      paddingBottom: level <= 2 ? 6 : 0,
      borderBottom: level <= 2 ? '1px solid var(--border-color)' : 'none',
    }}>
      <span style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 26, height: 26, borderRadius: 8, background: 'rgba(139,92,246,0.12)', fontSize: 14, flexShrink: 0 }}>{icon}</span>
      <span style={{ minWidth: 0 }}>{children}</span>
    </Tag>
  );
}

const MemoMessageBubble = React.memo(({ m, onRegenerate, index, onFeedback, isStreaming, onOpenSources }) => {
  // 聊天专用 markdown 组件：引用上标 [n] 可点击 → 打开右侧引用来源栏
  const chatComponents = {
    ...MarkdownComponents,
    h1: (p) => <Heading level={1} {...p} />,
    h2: (p) => <Heading level={2} {...p} />,
    h3: (p) => <Heading level={3} {...p} />,
    sup({ node, children }) {
      const txt = String(children || '').trim();
      const mm = txt.match(/^\[(\d+)\]$/);
      if (mm) {
        return (
          <sup className="cite" data-ref={mm[1]} style={{ cursor: 'pointer', fontWeight: 600, fontSize: '0.82em' }}
            onClick={(e) => { e.preventDefault(); e.stopPropagation(); onOpenSources?.(m.sources); }}
            title="查看引用来源">[{mm[1]}]</sup>
        );
      }
      return <sup>{children}</sup>;
    }
  };
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
            {/* 路由来源标签（super_router 结果透传） */}
            {m.route && ROUTE_META[m.route] && (
              <div style={{ marginBottom: 8, display: 'flex', alignItems: 'center', gap: 6 }}>
                <Tag color={ROUTE_META[m.route].color} style={{ borderRadius: 10, marginRight: 0, fontSize: 11, lineHeight: '18px' }}>
                  {ROUTE_META[m.route].label}
                </Tag>
                {m.confidence != null && (
                  <span style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>
                    置信度 {Math.round(m.confidence * 100)}%
                  </span>
                )}
              </div>
            )}
            {/* DeepSeek 风格「已思考」折叠卡：与气泡对齐，生成时展开，完成后收缩为耗时一行，点击可展开 */}
            {Array.isArray(m.toolSteps) && m.toolSteps.length > 0 && (
              <details className="think-card" open={isStreaming} style={{ margin: '2px 0 10px', borderRadius: 10, background: 'var(--bg-secondary)', overflow: 'hidden' }}>
                <summary className="think-summary" style={{ cursor: 'pointer', padding: '8px 14px', fontSize: 13, display: 'flex', alignItems: 'center', gap: 8, userSelect: 'none' }}>
                  <BulbOutlined style={{ color: '#8b5cf6' }} />
                  <span style={{ fontWeight: 600, color: 'var(--text-primary)' }}>已思考</span>
                  {m.elapsedSec != null && <span style={{ color: 'var(--text-tertiary)' }}>（用时 {m.elapsedSec} 秒）</span>}
                  <span style={{ flex: 1 }} />
                  <span style={{ color: 'var(--text-tertiary)', fontSize: 11 }}>{m.toolSteps.filter(s => s.kind !== 'thinking').length} 次工具调用</span>
                  <DownOutlined style={{ fontSize: 10, color: 'var(--text-tertiary)' }} />
                </summary>
                <div style={{ padding: '4px 14px 12px', borderTop: '1px solid var(--border-color)', background: 'var(--bg-primary)' }}>
                  {(() => {
                    const ICON_MAP = { web_search: '🔍', web_fetch: '📄', http_request: '🌐', amap_maps_weather: '🌤️', get_current_time: '🕐' };
                    // 去重：连续完全相同的 thinking（如多个请求各自推送的"正在打断上一条回答"）只显示一条
                    const _steps = [];
                    for (const s of m.toolSteps) {
                      const last = _steps[_steps.length - 1];
                      if (last && s.kind === 'thinking' && last.kind === 'thinking'
                        && last.summary === s.summary && last.step_kind === s.step_kind && last.tool === s.tool) continue;
                      _steps.push(s);
                    }
                    return _steps.map((s, si) => {
                      const isThinking = s.kind === 'thinking';
                      const isAction = s.step_kind === 'action';
                      // 推理行（认知轨迹）：纯 thinking 且非动作事件
                      if (isThinking && !isAction) {
                        return (
                          <div key={si} style={{ display: 'flex', alignItems: 'flex-start', gap: 8, padding: '7px 0', fontSize: 13, borderBottom: si < _steps.length - 1 ? '1px dashed var(--border-color)' : 'none' }}>
                            <BulbOutlined style={{ color: '#8b5cf6', marginTop: 3, fontSize: 12 }} />
                            <div style={{ minWidth: 0, flex: 1 }}>
                              <div style={{ color: 'var(--text-primary)', fontWeight: 500 }}>推理过程</div>
                              <div style={{ color: 'var(--text-secondary)', marginTop: 2, wordBreak: 'break-all', whiteSpace: 'pre-wrap', lineHeight: 1.6 }}>{s.summary}</div>
                            </div>
                          </div>
                        );
                      }
                      // 动作行（动作轨迹）：工具调用/搜索/浏览/结果，带状态与溯源链接
                      const tool = s.tool || '';
                      const ok = isThinking ? true : !!s.success;
                      const text = s.summary || s.content || '';
                      const icon = ICON_MAP[tool] || (tool ? '🔧' : '⚙️');
                      return (
                        <div key={si} style={{ display: 'flex', alignItems: 'flex-start', gap: 8, padding: '7px 0', fontSize: 13, borderBottom: si < _steps.length - 1 ? '1px dashed var(--border-color)' : 'none' }}>
                          <span style={{ marginTop: 2, fontSize: 13, lineHeight: 1 }}>{icon}</span>
                          <div style={{ minWidth: 0, flex: 1 }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                              <span style={{ color: 'var(--text-primary)', fontWeight: 500 }}>{tool ? `工具调用 · ${tool}` : '执行动作'}</span>
                              {isThinking ? (
                                <span style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>⏳ 进行中</span>
                              ) : ok ? (
                                <span style={{ fontSize: 11, color: '#16a34a' }}>✅ 成功</span>
                              ) : (
                                <span style={{ fontSize: 11, color: '#dc2626' }}>⚠️ 失败</span>
                              )}
                            </div>
                            <div style={{ color: 'var(--text-secondary)', marginTop: 2, wordBreak: 'break-all', whiteSpace: 'pre-wrap', lineHeight: 1.6 }}>{text}</div>
                            {s.url && (
                              <a href={s.url} target="_blank" rel="noopener noreferrer" style={{ color: 'var(--accent-color)', fontSize: 12, marginTop: 2, display: 'inline-block' }}>查看来源 ↗</a>
                            )}
                          </div>
                        </div>
                      );
                    });
                  })()}
                </div>
              </details>
            )}
            <ReactMarkdown remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[rehypeRaw, rehypeKatex]} components={chatComponents}>
              {(() => {
                let md = cleanMarkdown(committed);
                // 连体引用 [[1][2][3]] → 逐条上标
                md = md.replace(/\[\[(\d+)(?:\]\[\d+)*\]\]/g, (mm) => {
                  const nums = mm.replace(/[\[\]]/g, ' ').trim().split(/\s+/);
                  return nums.map((nn) => `<sup class="cite" data-ref="${nn}">[${nn}]</sup>`).join('');
                });
                // 单个引用 [n] → 上标（DeepSeek 同款交互）
                md = md.replace(/\[(\d+)\]/g, '<sup class="cite" data-ref="$1">[$1]</sup>');
                return md;
              })()}
            </ReactMarkdown>
            {uncommitted && (
              <pre style={{ whiteSpace: 'pre-wrap', background: '#282c34', color: '#abb2bf', padding: 12, borderRadius: 8, fontSize: 13, margin: '8px 0' }}>{uncommitted}</pre>
            )}
          </>
        )}
        {m.role === 'assistant' && m.sources && m.sources.length > 0 && (
          <div style={{ marginTop: 8 }}>
            <Button type="link" size="small" style={{ padding: 0, fontSize: 13, fontWeight: 600, color: 'var(--accent-color)' }} icon={<FileTextOutlined />} onClick={() => onOpenSources?.(m.sources)}>
              引用来源（{m.sources.length} 条）
            </Button>
          </div>
        )}
        {m.role === 'assistant' && !isStreaming && (
          <div style={{ marginTop: 8, display: 'flex', gap: 4 }} className="action-bar">
            <Button type="text" size="small" icon={<CopyOutlined />} onClick={() => { navigator.clipboard.writeText(m.content); message.success('已复制'); }} />
            <Button type="text" size="small" icon={<ReloadOutlined />} title="重新生成" onClick={() => onRegenerate(index)} />
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
