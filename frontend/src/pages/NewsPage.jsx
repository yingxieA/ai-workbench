import React, { useState, useEffect } from 'react';
import { Button, Space, message } from 'antd';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { MarkdownComponents } from '../components/MarkdownComponents';
import { authFetch } from '../utils/api';

function cleanMarkdown(text) {
  if (!text) return '';
  let cleaned = text;
  cleaned = cleaned.replace(/<br\s*\/?>/gi, '\n');
  cleaned = cleaned.replace(/^(\s*)[\*\-\+]\s+/gm, '$1- ');
  return cleaned;
}

function NewsPage({ t }) {
  const [summary, setSummary] = useState('');
  const [loading, setLoading] = useState(false);
  const [readDone, setReadDone] = useState(localStorage.getItem('news_read_' + new Date().toISOString().slice(0, 10)) === '1');

  useEffect(() => {
    authFetch(`${import.meta.env.VITE_API_BASE}/api/news/today`).then(r => r.json()).then(d => setSummary(d.summary));
  }, []);

  const handleGenerate = async () => {
    setLoading(true);
    try {
      const res = await authFetch(`${import.meta.env.VITE_API_BASE}/api/news/generate`, { method: 'POST' });
      const data = await res.json();
      if (!data.task_id) throw new Error('任务创建失败');
      const poll = setInterval(async () => {
        const r = await authFetch(`${import.meta.env.VITE_API_BASE}/api/news/task/${data.task_id}`);
        const t = await r.json();
        if (t.status === 'done') {
          clearInterval(poll); setSummary(t.summary); setLoading(false); message.success('日报生成完成！');
        } else if (t.status === 'failed') {
          clearInterval(poll); message.error('生成失败：' + (t.error || '未知错误')); setLoading(false);
        }
      }, 2000);
      setTimeout(() => { clearInterval(poll); if (loading) { setLoading(false); message.warning('任务仍在后台运行，请稍后刷新'); } }, 120000);
    } catch (e) {
      message.error('请求失败：' + e.message); setLoading(false);
    }
  };

  return (
    <div style={{ paddingTop: 32 }}>
      {/* 顶部标题区：居中显示，和智能问答一致 */}
      <div style={{ textAlign: 'center', marginBottom: 24 }}>
        <h1 className="page-title">{t('news.title')}</h1>
        <p className="page-subtitle">{t('news.subtitle')}</p>
      </div>

      {/* 操作按钮区：内容卡片上方，左对齐 */}
      <div style={{ maxWidth: 800, margin: '0 auto 16px', display: 'flex', justifyContent: 'flex-start' }}>
        <Space size={8}>
          <Button type="primary" onClick={handleGenerate} loading={loading}>
            {t('news.generate')}
          </Button>
          <Button onClick={() => {
            localStorage.setItem('news_read_' + new Date().toISOString().slice(0, 10), '1');
            setReadDone(true); message.success('打卡成功，继续加油！');
          }}>
            {readDone ? '✓ 今日已阅读' : '标记已阅读'}
          </Button>
          <Button onClick={() => { navigator.clipboard.writeText(summary); message.success('已复制'); }}>复制</Button>
          <Button onClick={() => {
            const blob = new Blob([summary], { type: 'text/markdown' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `AI日报_${new Date().toISOString().slice(0, 10)}.md`;
            a.click();
            URL.revokeObjectURL(url);
          }}>导出</Button>
        </Space>
      </div>

      {/* 正文卡片：限宽居中 + 悬浮质感 */}
      <div style={{
        maxWidth: 800,
        margin: '0 auto',
        background: 'var(--bg-secondary)',
        borderRadius: 12,
        border: '1px solid var(--border-color)',
        boxShadow: '0 4px 16px rgba(0, 0, 0, 0.03)',
        padding: '32px 40px',
      }}>
        <div className="news-content markdown-body">
          <ReactMarkdown remarkPlugins={[remarkGfm]} components={MarkdownComponents}>
            {cleanMarkdown(summary) || '点击按钮生成'}
          </ReactMarkdown>
        </div>
      </div>
    </div>
  );
}

export default NewsPage;
