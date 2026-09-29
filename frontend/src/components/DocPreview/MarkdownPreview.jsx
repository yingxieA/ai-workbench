import { useEffect, useMemo, useState, useRef, memo } from 'react';
import { Spin, message, Tooltip } from 'antd';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { oneLight } from 'react-syntax-highlighter/dist/esm/styles/prism';
import { CopyOutlined, CheckOutlined, MenuFoldOutlined, MenuUnfoldOutlined } from '@ant-design/icons';
import { authFetch } from '../../utils/api';

// 语言自动检测
function detectLanguage(code) {
  if (/^\s*(import |from |def |class |print\(|async |await )/m.test(code)) return 'python';
  if (/^\s*(SELECT|INSERT|UPDATE|DELETE|CREATE TABLE)/i.test(code)) return 'sql';
  if (/^\s*(GET |POST |PUT |DELETE |HTTP\/)/.test(code)) return 'http';
  if (/^\s*(pip install|npm install|docker |kubectl |git |curl |wget )/m.test(code)) return 'bash';
  if (/^\s*(version:|services:|ports:|image:|environment:)/.test(code)) return 'yaml';
  if (/^\s*[{[].*[}\]]/s.test(code) && /"[^"]+":/.test(code)) return 'json';
  return 'plaintext';
}

// 代码块组件
const CodeBlock = memo(function CodeBlock({ language, code }) {
  const [copied, setCopied] = useState(false);
  const displayLang = language === 'text' ? detectLanguage(code) : language;

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      message.success('已复制');
      setTimeout(() => setCopied(false), 2000);
    } catch {
      message.error('复制失败');
    }
  };

  return (
    <div style={{ margin: '16px 0' }}>
      <div style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        background: '#F8F9FA', border: '1px solid #E5E7EB', borderBottom: 'none',
        borderRadius: '8px 8px 0 0', padding: '8px 14px', fontSize: 12, color: '#6B7280'
      }}>
        <span>{displayLang}</span>
        <Tooltip title="复制代码">
          <span onClick={handleCopy} style={{ cursor: 'pointer' }}>
            {copied ? <CheckOutlined style={{ color: '#10B981' }} /> : <CopyOutlined />}
          </span>
        </Tooltip>
      </div>
      <SyntaxHighlighter
        language={displayLang === 'plaintext' ? 'text' : displayLang}
        style={oneLight}
        customStyle={{
          margin: 0, borderRadius: '0 0 8px 8px', border: '1px solid #E5E7EB',
          fontSize: 13, padding: '16px', background: '#FDFDFD', lineHeight: 1.6
        }}
      >
        {code}
      </SyntaxHighlighter>
    </div>
  );
});

// 提取目录
function extractToc(content) {
  const lines = content.split('\n');
  const toc = [];
  for (const line of lines) {
    const h2 = /^##\s+(.+)/.exec(line);
    const h3 = /^###\s+(.+)/.exec(line);
    if (h2) toc.push({ id: h2[1].trim(), level: 2, text: h2[1].trim() });
    else if (h3) toc.push({ id: h3[1].trim(), level: 3, text: h3[1].trim() });
  }
  return toc;
}

// 目录栏组件（memo 避免内容区重渲染）
const TocSidebar = memo(function TocSidebar({ toc, activeId, onSelect, onClose }) {
  return (
    <div style={{
      width: 220, flexShrink: 0, borderRight: '1px solid #E5E7EB',
      background: '#FAFBFC', overflowY: 'auto', padding: '16px 0'
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '0 16px 12px' }}>
        <span style={{ fontSize: 12, color: '#9CA3AF', fontWeight: 600, letterSpacing: 1 }}>目录</span>
        <Tooltip title="收起目录">
          <MenuFoldOutlined onClick={onClose} style={{ cursor: 'pointer', color: '#9CA3AF', fontSize: 14 }} />
        </Tooltip>
      </div>
      {toc.map(item => (
        <div
          key={item.id}
          onClick={() => onSelect(item.id)}
          style={{
            padding: item.level === 2 ? '8px 16px' : '6px 16px 6px 32px',
            fontSize: item.level === 2 ? 13 : 12,
            color: activeId === item.id ? '#4D6BFE' : item.level === 2 ? '#374151' : '#9CA3AF',
            fontWeight: activeId === item.id ? 600 : item.level === 2 ? 500 : 400,
            cursor: 'pointer',
            borderLeft: activeId === item.id ? '3px solid #4D6BFE' : '3px solid transparent',
            background: activeId === item.id ? '#EEF2FF' : 'transparent'
          }}
        >
          {item.text}
        </div>
      ))}
    </div>
  );
});

// 内容区组件（memo，只在 content 变化时重渲染）
const MarkdownContent = memo(function MarkdownContent({ content, headingRefs }) {
  // 局部变量计数，每次渲染从 0 开始，和 extractToc 编号一致
  let h2Count = 0;
  let h3Count = 0;

  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        pre: ({ children }) => {
          const codeEl = children;
          const className = codeEl?.props?.className || '';
          const match = /language-(\w+)/.exec(className);
          const language = match ? match[1] : 'text';
          const code = String(codeEl?.props?.children || '').replace(/\n$/, '');
          return <CodeBlock language={language} code={code} />;
        },
        code: ({ children }) => (
          <code style={{
            background: '#F3F4F6', color: '#DB2777', padding: '2px 6px',
            borderRadius: 4, fontSize: 13, fontFamily: 'Consolas, Monaco, monospace'
          }}>{children}</code>
        ),
        h1: ({ children }) => <h1 style={{ fontSize: 26, fontWeight: 700, margin: '32px 0 16px', color: '#111827' }}>{children}</h1>,
        h2: ({ children }) => {
          const key = String(children).trim();
          return (
            <h2 ref={el => headingRefs.current[key] = el}
              style={{ fontSize: 22, fontWeight: 700, margin: '28px 0 14px', paddingBottom: 8, borderBottom: '1px solid #E5E7EB', color: '#111827', scrollMarginTop: 16 }}>
              {children}
            </h2>
          );
        },
        h3: ({ children }) => {
          const key = String(children).trim();
          return (
            <h3 ref={el => headingRefs.current[key] = el}
              style={{ fontSize: 18, fontWeight: 600, margin: '22px 0 10px', color: '#1F2937', scrollMarginTop: 16 }}>
              {children}
            </h3>
          );
        },
        p: ({ children }) => <p style={{ margin: '10px 0' }}>{children}</p>,
        blockquote: ({ children }) => (
          <blockquote style={{ borderLeft: '4px solid #3B82F6', background: '#F8FAFC', padding: '12px 16px', margin: '14px 0', borderRadius: '0 8px 8px 0', color: '#4B5563' }}>{children}</blockquote>
        ),
        table: ({ children }) => (
          <div style={{ overflowX: 'auto', margin: '14px 0' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 14, border: '1px solid #E5E7EB' }}>{children}</table>
          </div>
        ),
        thead: ({ children }) => <thead style={{ background: '#F3F4F6' }}>{children}</thead>,
        th: ({ children }) => <th style={{ border: '1px solid #E5E7EB', padding: '10px 14px', textAlign: 'left', fontWeight: 600, color: '#374151' }}>{children}</th>,
        td: ({ children }) => <td style={{ border: '1px solid #E5E7EB', padding: '10px 14px', color: '#1F2937' }}>{children}</td>,
        ul: ({ children }) => <ul style={{ paddingLeft: 24, margin: '10px 0' }}>{children}</ul>,
        ol: ({ children }) => <ol style={{ paddingLeft: 24, margin: '10px 0' }}>{children}</ol>,
        li: ({ children }) => <li style={{ margin: '4px 0' }}>{children}</li>,
        a: ({ href, children }) => <a href={href} target="_blank" rel="noopener noreferrer" style={{ color: '#3B82F6', textDecoration: 'none' }}>{children}</a>,
        hr: () => <hr style={{ border: 'none', borderTop: '1px solid #E5E7EB', margin: '28px 0' }} />,
      }}
    >
      {content}
    </ReactMarkdown>
  );
});

function MarkdownPreview({ docId }) {
  const [content, setContent] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [tocOpen, setTocOpen] = useState(true);
  const [activeId, setActiveId] = useState('');
  const scrollRef = useRef(null);
  const headingRefs = useRef({});

  useEffect(() => {
    authFetch(`${import.meta.env.VITE_API_BASE}/api/documents/${docId}/raw-content`)
      .then(res => { if (!res.ok) throw new Error('加载失败'); return res.text(); })
      .then(text => { setContent(text); setLoading(false); })
      .catch(e => { setError(e.message); setLoading(false); });
  }, [docId]);

  const toc = useMemo(() => extractToc(content), [content]);

  // content 变化时清空旧的标题引用
  useEffect(() => {
    headingRefs.current = {};
  }, [content]);

  // 滚动高亮
  useEffect(() => {
    const container = scrollRef.current;
    if (!container) return;
    const onScroll = () => {
      let current = '';
      const containerTop = container.getBoundingClientRect().top;
      for (const item of toc) {
        const el = headingRefs.current[item.id];
        if (el) {
          const elTop = el.getBoundingClientRect().top - containerTop;
          if (elTop < 180) current = item.id;
        }
      }
      if (current) setActiveId(current);
    };
    container.addEventListener('scroll', onScroll);
    return () => container.removeEventListener('scroll', onScroll);
  }, [toc]);

  // 点击目录跳转：用容器级 scrollTo，不用 scrollIntoView
  const scrollTo = (id) => {
    const el = headingRefs.current[id];
    const container = scrollRef.current;
    if (el && container) {
      // 用 getBoundingClientRect 计算相对位置，避免 offsetParent 不准
      const top = el.getBoundingClientRect().top - container.getBoundingClientRect().top + container.scrollTop - 20;
      container.scrollTo({ top, behavior: 'auto' });
      setActiveId(id);
    }
  };

  if (loading) return <Spin tip="加载中..." style={{ display: 'block', textAlign: 'center', padding: 100 }} />;
  if (error) return <div style={{ padding: 40, textAlign: 'center', color: '#EF4444' }}>加载失败：{error}</div>;

  return (
    <div style={{ display: 'flex', height: '80vh', overflow: 'hidden' }}>
      {tocOpen && <TocSidebar toc={toc} activeId={activeId} onSelect={scrollTo} onClose={() => setTocOpen(false)} />}

      <div style={{ flex: 1, overflowY: 'auto', position: 'relative' }} ref={scrollRef}>
        {!tocOpen && (
          <Tooltip title="展开目录">
            <MenuUnfoldOutlined
              onClick={() => setTocOpen(true)}
              style={{ position: 'sticky', top: 16, left: 16, cursor: 'pointer', color: '#9CA3AF', fontSize: 18, zIndex: 10, background: '#fff', padding: 8, borderRadius: 6, boxShadow: '0 1px 4px rgba(0,0,0,0.08)' }}
            />
          </Tooltip>
        )}
        <div style={{ padding: '24px 40px', maxWidth: '860px', margin: '0 auto', lineHeight: 1.75, color: '#1F2937', fontSize: 15 }}>
          <MarkdownContent content={content} headingRefs={headingRefs} />
        </div>
      </div>
    </div>
  );
}

export default MarkdownPreview;
