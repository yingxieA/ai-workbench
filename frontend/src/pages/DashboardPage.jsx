import React, { useState, useEffect } from 'react';
import { Button, Tag, Progress, Timeline, message } from 'antd';
import { FileTextOutlined, MessageOutlined, NotificationOutlined, BulbOutlined, UploadOutlined, GithubOutlined } from '@ant-design/icons';
import { authHeaders, authFetch } from '../utils/api';

function DashboardPage({ t, onNavigate }) {
  const [stats, setStats] = useState({ docs: 0, sessions: 0, skills: 0, news: false });
  const [reviews, setReviews] = useState([]);
  const [recommend, setRecommend] = useState([]);
  const [activities, setActivities] = useState([]);

  useEffect(() => {
    authFetch(`${import.meta.env.VITE_API_BASE}/api/documents/list?skip=0&limit=1`).then(r => r.json()).then(d => setStats(s => ({ ...s, docs: d.total || 0 })));
    authFetch(`${import.meta.env.VITE_API_BASE}/api/chat/sessions`, { headers: authHeaders() }).then(r => r.json()).then(d => setStats(s => ({ ...s, sessions: d.length || 0 })));
    authFetch(`${import.meta.env.VITE_API_BASE}/api/news/today`).then(r => r.json()).then(d => setStats(s => ({ ...s, news: !!d.summary })));
    authFetch(`${import.meta.env.VITE_API_BASE}/api/chat/sessions/activity`, { headers: authHeaders() }).then(r => r.json()).then(setActivities).catch(() => {});
    authFetch(`${import.meta.env.VITE_API_BASE}/api/skills`).then(r => r.json()).then(d => setStats(s => ({ ...s, skills: d.length || 0 }))).catch(() => {});
    authFetch(`${import.meta.env.VITE_API_BASE}/api/review/today`).then(r => r.json()).then(setReviews).catch(() => {});
    authFetch(`${import.meta.env.VITE_API_BASE}/api/recommend/continue`).then(r => r.json()).then(setRecommend).catch(() => {});
  }, []);

  const cards = [
    { title: '文档总数', value: stats.docs, icon: <FileTextOutlined />, tag: null },
    { title: '对话总数', value: stats.sessions, icon: <MessageOutlined />, tag: null },
    { title: '待办技能', value: stats.skills, icon: <BulbOutlined />, tag: null },
    { title: '今日日报', value: '', icon: <NotificationOutlined />, tag: stats.news ? '已生成' : '未生成' },
  ];

  const quickActions = [
    { title: '开启新对话', desc: '智能问答', icon: <MessageOutlined />, action: () => onNavigate('chat') },
    { title: '上传文档', desc: '知识库管理', icon: <UploadOutlined />, action: () => onNavigate('docs') },
    { title: '生成日报', desc: 'AI 新闻', icon: <NotificationOutlined />, action: () => onNavigate('news') },
    { title: 'GitHub 周榜', desc: '热门项目', icon: <GithubOutlined />, action: () => onNavigate('github') },
  ];

  return (
    <div style={{ paddingTop: 32 }}>
      <div style={{ textAlign: 'center', marginBottom: 24 }}>
        <h1 className="page-title">工作台总览</h1>
        <p className="page-subtitle">你的 AI 学习概览</p>
      </div>
      {recommend.length > 0 && (
        <div style={{ marginTop: 16, padding: 16, background: 'var(--accent-bg)', borderRadius: 8, border: '1px solid var(--border-color)' }}>
          <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--accent-color)', marginBottom: 12 }}>📖 推荐继续学习</div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: 12 }}>
            {recommend.slice(0, 2).map(r => (
              <div key={r.skill_id} onClick={() => onNavigate('skills')} style={{ padding: 16, background: 'var(--bg-secondary)', borderRadius: 8, border: '1px solid var(--border-color)', cursor: 'pointer', transition: 'all 0.2s', height: '100%' }} onMouseEnter={e => e.currentTarget.style.boxShadow = 'var(--card-hover-shadow)'} onMouseLeave={e => e.currentTarget.style.boxShadow = 'none'}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                  <div style={{ flex: 1 }}>
                    <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 4, color: 'var(--text-primary)' }}>{r.title}</div>
                    <Tag style={{ fontSize: 11 }}>学习路径</Tag>
                  </div>
                  <Progress type="circle" percent={r.progress} size={48} strokeColor="var(--accent-color)" />
                </div>
                <div style={{ marginTop: 12, fontSize: 12, color: 'var(--text-tertiary)' }}>{r.done_tasks}/{r.total_tasks} 任务已完成</div>
                <Button type="primary" size="small" style={{ marginTop: 8 }}>
                  {r.progress === 0 ? '开始学习' : '继续学习 →'}
                </Button>
              </div>
            ))}
          </div>
        </div>
      )}
      <div style={{ display: 'flex', gap: 12, marginTop: 16 }}>
        {cards.map(c => (
          <div key={c.title} className="card" style={{ flex: 1 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
              <div>
                <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>{c.title}</div>
                {c.tag ? (
                  <Tag color={stats.news ? 'green' : 'default'} style={{ marginTop: 6 }}>{c.tag}</Tag>
                ) : (
                  <div style={{ fontSize: 20, fontWeight: 600, color: 'var(--text-primary)', marginTop: 6, letterSpacing: '-0.02em' }}>{c.value}</div>
                )}
              </div>
              <div style={{ fontSize: 20, color: 'var(--accent-color)' }}>{c.icon}</div>
            </div>
          </div>
        ))}
      </div>
      {reviews.length > 0 && (
        <div className="card" style={{ marginTop: 16 }}>
          <h3 className="card-title" style={{ color: 'var(--accent-color)' }}>今日复习 ({reviews.length})</h3>
          {reviews.map(r => (
            <div key={r.id} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '8px 0', borderBottom: '1px solid var(--border-color)' }}>
              <div>
                <div style={{ fontSize: 14, color: 'var(--text-primary)' }}>{r.title}</div>
                <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginTop: 2 }}>已复习 {r.review_count} 次</div>
              </div>
              <Button size="small" type="primary" onClick={async () => {
                await authFetch(`${import.meta.env.VITE_API_BASE}/api/review/${r.id}/done`, { method: 'POST' });
                setReviews(reviews.filter(x => x.id !== r.id));
                message.success('复习完成');
              }}>已复习</Button>
            </div>
          ))}
        </div>
      )}
      <div style={{ marginTop: 16 }}>
        <h3 className="card-title">快捷操作</h3>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12 }}>
          {quickActions.map(a => (
            <div key={a.title} className="card" style={{ cursor: 'pointer' }} onClick={a.action}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                <div style={{ fontSize: 20, color: 'var(--accent-color)' }}>{a.icon}</div>
                <div>
                  <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-primary)' }}>{a.title}</div>
                  <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginTop: 2 }}>{a.desc}</div>
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>
      {activities.length > 0 && (
        <div style={{ marginTop: 16 }}>
          <h3 className="card-title">最近动态</h3>
          <div className="card">
            <Timeline
              items={activities.map(a => ({
                color: 'var(--accent-color)',
                children: `${new Date(a.time).toLocaleString()}，${a.text}`
              }))}
            />
          </div>
        </div>
      )}
    </div>
  );
}

export default DashboardPage;
