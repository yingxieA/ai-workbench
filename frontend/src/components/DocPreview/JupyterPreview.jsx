import { useEffect, useMemo, useState, useRef, memo } from 'react';
import { Spin, message, Tooltip } from 'antd';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { oneLight } from 'react-syntax-highlighter/dist/esm/styles/prism';
import { CopyOutlined, CheckOutlined, MenuFoldOutlined, MenuUnfoldOutlined } from '@ant-design/icons';
import { authFetch } from '../../utils/api';

// markdown 单元格里的代码块组件
function MdCodeBlock({ code, language }) {
  const [copied, setCopied] = useState(false);
  const lang = language === 'text' ? 'python' : language;

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
    <div style={{ margin: '12px 0' }}>
      <div style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        background: '#F8F9FA', border: '1px solid #E5E7EB', borderBottom: 'none',
        borderRadius: '8px 8px 0 0', padding: '6px 14px', fontSize: 12, color: '#6B7280'
      }}>
        <span>{lang}</span>
        <Tooltip title="复制代码">
          <span onClick={handleCopy} style={{ cursor: 'pointer' }}>
            {copied ? <CheckOutlined style={{ color: '#10B981' }} /> : <CopyOutlined />}
          </span>
        </Tooltip>
      </div>
      <SyntaxHighlighter
        language={lang}
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
}

// code 单元格组件
function CodeCell({ code, executionCount }) {
  const [copied, setCopied] = useState(false);

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
    <div style={{ margin: '12px 0' }}>
      <div style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        background: '#F8F9FA', border: '1px solid #E5E7EB', borderBottom: 'none',
        borderRadius: '8px 8px 0 0', padding: '6px 14px', fontSize: 12, color: '#6B7280'
      }}>
        <span>{executionCount ? `In [${executionCount}]:` : 'python'}</span>
        <Tooltip title="复制代码">
          <span onClick={handleCopy} style={{ cursor: 'pointer' }}>
            {copied ? <CheckOutlined style={{ color: '#10B981' }} /> : <CopyOutlined />}
          </span>
        </Tooltip>
      </div>
      <SyntaxHighlighter
        language="python"
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
}

// 渲染输出
function CellOutput({ output }) {
  if (output.output_type === 'stream') {
    const text = Array.isArray(output.text) ? output.text.join('') : output.text;
    return (
      <pre style={{ background: '#F9FAFB', border: '1px solid #E5E7EB', borderRadius: 6, padding: '10px 14px', overflow: 'auto', fontSize: 12, margin: '6px 0 0', color: '#374151', lineHeight: 1.5 }}>
        <code>{text}</code>
      </pre>
    );
  }
  if (output.output_type === 'execute_result' || output.output_type === 'display_data') {
    const imageData = output.data?.['image/png'] || output.data?.['image/jpeg'];
    if (imageData) {
      const src = typeof imageData === 'string' ? `data:image/png;base64,${imageData}` : URL.createObjectURL(new Blob([imageData]));
      return <img src={src} alt="output" style={{ maxWidth: '100%', borderRadius: 6, border: '1px solid #E5E7EB', margin: '6px 0 0' }} />;
    }
    const text = output.data?.['text/plain'];
    if (text) {
      const textStr = Array.isArray(text) ? text.join('') : text;
      return (
        <pre style={{ background: '#F9FAFB', border: '1px solid #E5E7EB', borderRadius: 6, padding: '10px 14px', overflow: 'auto', fontSize: 12, margin: '6px 0 0', color: '#374151', lineHeight: 1.5 }}>
          <code>{textStr}</code>
        </pre>
      );
    }
  }
  if (output.output_type === 'error') {
    const traceback = Array.isArray(output.traceback) ? output.traceback.join('\n') : output.traceback;
    return (
      <pre style={{ background: '#FEF2F2', border: '1px solid #FECACA', borderRadius: 6, padding: '10px 14px', overflow: 'auto', fontSize: 12, margin: '6px 0 0', color: '#DC2626', lineHeight: 1.5 }}>
        <code>{output.ename}: {output.evalue}\n{traceback}</code>
      </pre>
    );
  }
  return null;
}

// 从所有 markdown 单元格提取标题
function extractToc(cells) {
  const toc = [];
  for (const cell of cells) {
    if (cell.cell_type !== 'markdown') continue;
    const source = Array.isArray(cell.source) ? cell.source.join('') : cell.source;
    for (const line of source.split('\n')) {
      const h2 = /^##\s+(.+)/.exec(line);
      const h3 = /^###\s+(.+)/.exec(line);
      if (h2) toc.push({ id: h2[1].trim(), level: 2, text: h2[1].trim() });
      else if (h3) toc.push({ id: h3[1].trim(), level: 3, text: h3[1].trim() });
    }
  }
  return toc;
}

// 目录栏
const TocSidebar = memo(function TocSidebar({ toc, activeId, onSelect, onClose }) {
  return (
    <div style={{ width: 220, flexShrink: 0, borderRight: '1px solid #E5E7EB', background: '#FAFBFC', overflowY: 'auto', padding: '16px 0' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '0 16px 12px' }}>
        <span style={{ fontSize: 12, color: '#9CA3AF', fontWeight: 600, letterSpacing: 1 }}>目录</span>
        <Tooltip title="收起目录">
          <MenuFoldOutlined onClick={onClose} style={{ cursor: 'pointer', color: '#9CA3AF', fontSize: 14 }} />
        </Tooltip>
      </div>
      {toc.map((item, i) => (
        <div key={i} onClick={() => onSelect(item.id)} style={{
          padding: item.level === 2 ? '8px 16px' : '6px 16px 6px 32px',
          fontSize: item.level === 2 ? 13 : 12,
          color: activeId === item.id ? '#4D6BFE' : item.level === 2 ? '#374151' : '#9CA3AF',
          fontWeight: activeId === item.id ? 600 : item.level === 2 ? 500 : 400,
          cursor: 'pointer',
          borderLeft: activeId === item.id ? '3px solid #4D6BFE' : '3px solid transparent',
          background: activeId === item.id ? '#EEF2FF' : 'transparent'
        }}>
          {item.text}
        </div>
      ))}
    </div>
  );
});

function JupyterPreview({ docId }) {
  const [notebook, setNotebook] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [tocOpen, setTocOpen] = useState(true);
  const [activeId, setActiveId] = useState('');
  const scrollRef = useRef(null);
  const headingRefs = useRef({});

  useEffect(() => {
    authFetch(`${import.meta.env.VITE_API_BASE}/api/documents/${docId}/raw-content`)
      .then(res => { if (!res.ok) throw new Error('加载失败'); return res.json(); })
      .then(json => { setNotebook(json); setLoading(false); })
      .catch(e => { setError(e.message); setLoading(false); });
  }, [docId]);

  const toc = useMemo(() => notebook?.cells ? extractToc(notebook.cells) : [], [notebook]);

  // 滚动高亮
  useEffect(() => {
    const container = scrollRef.current;
    if (!container || !toc.length) return;
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

  const scrollTo = (id) => {
    const el = headingRefs.current[id];
    const container = scrollRef.current;
    if (el && container) {
      const top = el.getBoundingClientRect().top - container.getBoundingClientRect().top + container.scrollTop - 20;
      container.scrollTo({ top, behavior: 'auto' });
      setActiveId(id);
    }
  };

  if (loading) return <Spin tip="加载中..." style={{ display: 'block', textAlign: 'center', padding: 100 }} />;
  if (error) return <div style={{ padding: 40, textAlign: 'center', color: '#EF4444' }}>加载失败：{error}</div>;
  if (!notebook || !notebook.cells) return <div style={{ padding: 40, textAlign: 'center' }}>无法解析 Notebook</div>;

  const renderCell = (cell, idx) => {
    const source = Array.isArray(cell.source) ? cell.source.join('') : cell.source;

    if (cell.cell_type === 'markdown') {
      return (
        <div key={idx} style={{ padding: '4px 0' }}>
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            components={{
              pre: ({ children }) => {
                const codeEl = children;
                const className = codeEl?.props?.className || '';
                const match = /language-(\w+)/.exec(className);
                const language = match ? match[1] : 'text';
                const code = String(codeEl?.props?.children || '').replace(/\n$/, '');
                return <MdCodeBlock code={code} language={language} />;
              },
              code: ({ children }) => (
                <code style={{ background: '#F3F4F6', color: '#DB2777', padding: '2px 6px', borderRadius: 4, fontSize: 13, fontFamily: 'Consolas, Monaco, monospace' }}>{children}</code>
              ),
              h2: ({ children }) => {
                const key = String(children).trim();
                return <h2 ref={el => headingRefs.current[key] = el} style={{ fontSize: 20, fontWeight: 700, margin: '20px 0 10px', paddingBottom: 6, borderBottom: '1px solid #E5E7EB', color: '#111827', scrollMarginTop: 16 }}>{children}</h2>;
              },
              h3: ({ children }) => {
                const key = String(children).trim();
                return <h3 ref={el => headingRefs.current[key] = el} style={{ fontSize: 16, fontWeight: 600, margin: '16px 0 8px', color: '#1F2937', scrollMarginTop: 16 }}>{children}</h3>;
              },
              h1: ({ children }) => <h1 style={{ fontSize: 24, fontWeight: 700, margin: '24px 0 12px', color: '#111827' }}>{children}</h1>,
              p: ({ children }) => <p style={{ margin: '8px 0', lineHeight: 1.7 }}>{children}</p>,
              table: ({ children }) => (
                <div style={{ overflowX: 'auto', margin: '12px 0' }}>
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13, border: '1px solid #E5E7EB' }}>{children}</table>
                </div>
              ),
              thead: ({ children }) => <thead style={{ background: '#F3F4F6' }}>{children}</thead>,
              th: ({ children }) => <th style={{ border: '1px solid #E5E7EB', padding: '8px 12px', textAlign: 'left', fontWeight: 600, color: '#374151' }}>{children}</th>,
              td: ({ children }) => <td style={{ border: '1px solid #E5E7EB', padding: '8px 12px', color: '#1F2937' }}>{children}</td>,
              ul: ({ children }) => <ul style={{ paddingLeft: 24, margin: '8px 0' }}>{children}</ul>,
              ol: ({ children }) => <ol style={{ paddingLeft: 24, margin: '8px 0' }}>{children}</ol>,
              li: ({ children }) => <li style={{ margin: '4px 0' }}>{children}</li>,
              blockquote: ({ children }) => (
                <blockquote style={{ borderLeft: '4px solid #3B82F6', background: '#F8FAFC', padding: '10px 14px', margin: '12px 0', borderRadius: '0 6px 6px 0', color: '#4B5563' }}>{children}</blockquote>
              ),
            }}
          >
            {source}
          </ReactMarkdown>
          <div style={{ borderTop: '1px solid #F3F4F6', margin: '16px 0' }} />
        </div>
      );
    }

    if (cell.cell_type === 'code') {
      return (
        <div key={idx} style={{ padding: '4px 0' }}>
          <CodeCell code={source} executionCount={cell.execution_count} />
          {cell.outputs && cell.outputs.map((output, oIdx) => <CellOutput key={oIdx} output={output} />)}
          <div style={{ borderTop: '1px solid #F3F4F6', margin: '16px 0' }} />
        </div>
      );
    }
    return null;
  };

  return (
    <div style={{ display: 'flex', height: '80vh', overflow: 'hidden' }}>
      {tocOpen && <TocSidebar toc={toc} activeId={activeId} onSelect={scrollTo} onClose={() => setTocOpen(false)} />}
      <div style={{ flex: 1, overflowY: 'auto', position: 'relative' }} ref={scrollRef}>
        {!tocOpen && (
          <Tooltip title="展开目录">
            <MenuUnfoldOutlined onClick={() => setTocOpen(true)} style={{ position: 'sticky', top: 16, left: 16, cursor: 'pointer', color: '#9CA3AF', fontSize: 18, zIndex: 10, background: '#fff', padding: 8, borderRadius: 6, boxShadow: '0 1px 4px rgba(0,0,0,0.08)' }} />
          </Tooltip>
        )}
        <div style={{ padding: '24px 40px', maxWidth: '860px', margin: '0 auto', lineHeight: 1.75, color: '#1F2937', fontSize: 15 }}>
          {notebook.cells.map(renderCell)}
        </div>
      </div>
    </div>
  );
}

export default JupyterPreview;
