import React, { useState, useEffect } from 'react';
import { Input, Button, Modal, Drawer, Tag, Space, Spin, Tooltip } from 'antd';
import { LinkOutlined, FileTextOutlined, RobotOutlined } from '@ant-design/icons';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { MarkdownComponents } from '../components/MarkdownComponents';
import { authFetch } from '../utils/api';

function GitHubPage({ t }) {
  const [trending, setTrending] = useState([]);
  const [loading, setLoading] = useState(true);
  const [searchKeyword, setSearchKeyword] = useState('');
  const [isSearching, setIsSearching] = useState(false);
  const [detailModal, setDetailModal] = useState({ open: false, loading: false, content: '', name: '' });
  const [teardownDrawer, setTeardownDrawer] = useState({ open: false, loading: false, content: '', name: '' });

  useEffect(() => {
    // 默认从数据库加载（不实时抓取）
    loadTrending(false);
  }, []);

  // 加载周榜数据
  const loadTrending = async (forceRefresh = false) => {
    setLoading(true);
    try {
      const url = forceRefresh
        ? `${import.meta.env.VITE_API_BASE}/api/news/github-trending?refresh=true`
        : `${import.meta.env.VITE_API_BASE}/api/news/github-trending`;
      const res = await authFetch(url);
      const data = await res.json();
      setTrending(data.items || []);
    } catch (e) {
      console.error('加载失败', e);
    } finally {
      setLoading(false);
    }
  };

  const handleSearch = async (value) => {
    if (!value.trim()) return;
    setLoading(true); setSearchKeyword(value); setIsSearching(true);
    const res = await authFetch(`${import.meta.env.VITE_API_BASE}/api/news/github-search?q=${value}`);
    const data = await res.json();
    setTrending(data.items || []); setLoading(false);
  };

  const backToTrending = async () => {
    setSearchKeyword(''); setIsSearching(false);
    await loadTrending(false);
  };

  const showDetail = async (item) => {
    setDetailModal({ open: true, loading: true, content: '', name: item.name });
    const res = await authFetch(`${import.meta.env.VITE_API_BASE}/api/news/github-project-detail?name=${item.name}`);
    const data = await res.json();
    setDetailModal({ open: true, loading: false, content: data.detail, name: item.name });
  };

  const showTeardown = async (item) => {
    setTeardownDrawer({ open: true, loading: true, content: '', name: item.name });
    const res = await authFetch(`${import.meta.env.VITE_API_BASE}/api/news/github-project-teardown?name=${item.name}`);
    const data = await res.json();
    setTeardownDrawer({ open: true, loading: false, content: data.teardown, name: item.name });
  };

  const langColor = (lang) => {
    return 'default';
  };

  const filtered = trending.filter(item =>
    item.name.toLowerCase().includes(searchKeyword.toLowerCase()) ||
    (item.language && item.language.toLowerCase().includes(searchKeyword.toLowerCase()))
  );

  return (
    <div style={{ paddingTop: 32 }}>
      <div style={{ textAlign: 'center', marginBottom: 24 }}>
        <h1 className="page-title">{t('github.title')}</h1>
        <p className="page-subtitle">{t('github.subtitle')}</p>
      </div>
      <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
        <Input.Search placeholder={t('github.search')} enterButton="搜索" onSearch={handleSearch} style={{ flex: 1 }} />
        <Button onClick={() => loadTrending(true)} loading={loading}>刷新</Button>
      </div>
      {isSearching && (
        <div style={{ marginBottom: 12, color: 'var(--text-secondary)', fontSize: 13 }}>
          搜索：{searchKeyword}
          <Button type="link" size="small" onClick={backToTrending}>返回周榜</Button>
        </div>
      )}
      <div className="card" style={{ padding: 8 }}>
        {loading ? (<div style={{ textAlign: 'center', padding: 40 }}><Spin size="large" /><p style={{ color: 'var(--text-tertiary)', marginTop: 16 }}>加载中...</p></div>) : filtered.length === 0 ? (<p style={{ color: 'var(--text-tertiary)', padding: 24, textAlign: 'center' }}>暂无数据</p>) : (
          filtered.map((item, i) => {
            // 排名徽章颜色
            const rankColors = [
              { bg: '#F59E0B', text: '#fff' },   // 第1名 金色
              { bg: '#9CA3AF', text: '#fff' },   // 第2名 银色
              { bg: '#B45309', text: '#fff' },   // 第3名 铜色
            ];
            const rankStyle = i < 3
              ? { background: rankColors[i].bg, color: rankColors[i].text }
              : { background: '#F3F4F6', color: '#4B5563' };

            return (
              <div key={i} className="github-card" style={{
                padding: 16,
                marginBottom: 12,
                borderRadius: 8,
                border: '1px solid #E5E7EB',
                transition: 'all 0.2s ease',
              }}
              onMouseEnter={e => {
                e.currentTarget.style.boxShadow = '0 4px 12px rgba(0, 0, 0, 0.05)';
                e.currentTarget.style.transform = 'translateY(-2px)';
              }}
              onMouseLeave={e => {
                e.currentTarget.style.boxShadow = 'none';
                e.currentTarget.style.transform = 'translateY(0)';
              }}
              >
                {/* 第一行：排名 + 项目名 + 星标数 */}
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 12, flex: 1 }}>
                    {/* 排名徽章 */}
                    <div style={{
                      width: 24,
                      height: 24,
                      borderRadius: 6,
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      fontSize: 13,
                      fontWeight: 600,
                      ...rankStyle,
                      flexShrink: 0,
                    }}>
                      {i + 1}
                    </div>
                    {/* 项目名 */}
                    <a href={item.url} target="_blank" rel="noreferrer" style={{
                      color: '#111827',
                      fontWeight: 600,
                      fontSize: 15,
                      textDecoration: 'none',
                    }}>
                      {item.name}
                    </a>
                    {/* 语言标签 */}
                    {item.language && (
                      <Tag style={{
                        background: '#F3F4F6',
                        border: 'none',
                        color: '#4B5563',
                        fontSize: 12,
                        margin: 0,
                      }}>
                        {item.language}
                      </Tag>
                    )}
                  </div>
                  {/* 星标数 */}
                  <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexShrink: 0 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                      <span style={{ color: '#9CA3AF', fontSize: 13 }}>⭐</span>
                      <span style={{ fontSize: 15, fontWeight: 600, color: '#111827' }}>{item.stars?.toLocaleString()}</span>
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                      <span style={{ color: '#9CA3AF', fontSize: 13 }}>🍴</span>
                      <span style={{ fontSize: 13, fontWeight: 500, color: '#4B5563' }}>{(item.forks || 0)?.toLocaleString()}</span>
                    </div>
                  </div>
                </div>

                {/* 第二行：项目描述 */}
                <p style={{
                  margin: '0 0 12px 0',
                  color: '#4B5563',
                  fontSize: 13,
                  lineHeight: 1.6,
                  display: '-webkit-box',
                  WebkitLineClamp: 2,
                  WebkitBoxOrient: 'vertical',
                  overflow: 'hidden',
                }}>
                  {item.description || '暂无描述'}
                </p>

                {/* 第三行：操作按钮 */}
                <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 16 }}>
                  <Tooltip title="访问 GitHub 仓库">
                    <Button
                      type="text"
                      size="small"
                      icon={<LinkOutlined />}
                      href={item.url}
                      target="_blank"
                      style={{ color: '#4D6BFE', fontSize: 13 }}
                    >
                      访问
                    </Button>
                  </Tooltip>
                  <Tooltip title="查看项目详细介绍">
                    <Button
                      type="text"
                      size="small"
                      icon={<FileTextOutlined />}
                      onClick={() => showDetail(item)}
                      style={{ color: '#4D6BFE', fontSize: 13 }}
                    >
                      介绍
                    </Button>
                  </Tooltip>
                  <Tooltip title="AI 拆解学习路线">
                    <Button
                      type="text"
                      size="small"
                      icon={<RobotOutlined />}
                      onClick={() => showTeardown(item)}
                      style={{ color: '#4D6BFE', fontSize: 13 }}
                    >
                      AI 拆解
                    </Button>
                  </Tooltip>
                </div>
              </div>
            );
          })
        )}
      </div>
      <Modal title={detailModal.name} open={detailModal.open} onCancel={() => setDetailModal({ ...detailModal, open: false })} footer={null} width={700}>
        {detailModal.loading ? (
          <div style={{ textAlign: 'center', padding: 40 }}><Spin size="large" /><p style={{ color: 'var(--text-tertiary)', marginTop: 16 }}>AI 正在生成项目介绍，请稍候...</p></div>
        ) : (
          <div className="markdown-body">
            <ReactMarkdown remarkPlugins={[remarkGfm]} components={MarkdownComponents}>
              {detailModal.content}
            </ReactMarkdown>
          </div>
        )}
      </Modal>
      <Drawer title={teardownDrawer.name} open={teardownDrawer.open} onClose={() => setTeardownDrawer({ ...teardownDrawer, open: false })} width={700}>
        {teardownDrawer.loading ? (
          <div style={{ textAlign: 'center', padding: 40 }}><Spin size="large" /><p style={{ color: 'var(--text-tertiary)', marginTop: 16 }}>AI 正在拆解项目，请稍候...</p></div>
        ) : (
          <div className="markdown-body">
            <ReactMarkdown remarkPlugins={[remarkGfm]} components={MarkdownComponents}>
              {teardownDrawer.content}
            </ReactMarkdown>
          </div>
        )}
      </Drawer>
    </div>
  );
}

export default GitHubPage;
