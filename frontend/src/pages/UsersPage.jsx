import React, { useState, useEffect } from 'react';
import { Table, Tag, Select, Button, Input, Space, message, Popconfirm, Tooltip, Avatar } from 'antd';
import { ReloadOutlined, UserOutlined, CrownOutlined, StarOutlined, TeamOutlined, StopOutlined } from '@ant-design/icons';
import { authFetch } from '../utils/api';

const API = import.meta.env.VITE_API_BASE;

/* 首字母彩色头像（与全局 accent 风格一致） */
const AVATAR_COLORS = ['#4D6BFE', '#7C3AED', '#0EA5E9', '#F59E0B', '#10B981', '#EF4444', '#EC4899', '#14B8A6'];
const colorOf = (name) => {
  let h = 0;
  for (const c of (name || '?')) h = (h * 31 + c.charCodeAt(0)) % 997;
  return AVATAR_COLORS[h % AVATAR_COLORS.length];
};

const levelMeta = {
  100: { label: '管理员', tag: 'gold', icon: <CrownOutlined />, badge: '管理员' },
  50: { label: '高级', tag: 'purple', icon: <StarOutlined />, badge: '高级用户' },
  10: { label: '普通', tag: 'default', icon: <TeamOutlined />, badge: '普通用户' },
};

function StatCard({ icon, label, value, color, bg }) {
  return (
    <div style={{
      flex: 1, minWidth: 140, background: 'var(--bg-secondary)', border: '1px solid var(--border-color)',
      borderRadius: 12, padding: '14px 16px', display: 'flex', alignItems: 'center', gap: 12,
      boxShadow: 'var(--card-shadow)', transition: 'box-shadow 0.2s, transform 0.2s',
    }} className="stat-card">
      <div style={{ width: 40, height: 40, borderRadius: 10, background: bg, color, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 18, flexShrink: 0 }}>
        {icon}
      </div>
      <div style={{ minWidth: 0 }}>
        <div style={{ fontSize: 20, fontWeight: 700, color: 'var(--text-primary)', lineHeight: 1.2 }}>{value}</div>
        <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>{label}</div>
      </div>
    </div>
  );
}

export default function UsersPage() {
  const [items, setItems] = useState([]);
  const [stats, setStats] = useState({ total: 0, admin: 0, senior: 0, normal: 0, disabled: 0 });
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState('');
  const [loading, setLoading] = useState(false);
  const pageSize = 20;

  const fetchUsers = async (p = 1, kw) => {
    setLoading(true);
    try {
      const params = new URLSearchParams({ skip: (p - 1) * pageSize, limit: pageSize });
      if (kw) params.append('search', kw);
      const res = await authFetch(`${API}/api/users?${params}`);
      const data = await res.json();
      setItems(data.items || []);
      setTotal(data.total || 0);
      if (data.stats) setStats(data.stats);
    } finally { setLoading(false); }
  };

  useEffect(() => { fetchUsers(1); }, []);

  const changeRole = async (u, role_level) => {
    const res = await authFetch(`${API}/api/users/${u.id}/role_level`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ role_level }),
    });
    const data = await res.json();
    if (res.ok) {
      message.success(`${u.username} 已调整为${levelMeta[role_level].label}用户`);
      fetchUsers(page, search);
    } else message.error(data.detail || '更新失败');
  };

  const changeStatus = async (u, status) => {
    const res = await authFetch(`${API}/api/users/${u.id}/status`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status }),
    });
    const data = await res.json();
    if (res.ok) {
      message.success(status === 'active' ? `已启用 ${u.username}` : `已禁用 ${u.username}`);
      fetchUsers(page, search);
    } else message.error(data.detail || '操作失败');
  };

  const isSelf = (u) => {
    try { return u.id === JSON.parse(localStorage.getItem('user') || '{}').id; } catch { return false; }
  };

  const columns = [
    {
      title: '用户', dataIndex: 'username', width: 200,
      render: (v, r) => (
        <Space size={10}>
          <Avatar size={34} style={{ background: colorOf(v), fontWeight: 600, fontSize: 14, flexShrink: 0 }}>{v.slice(0, 1).toUpperCase()}</Avatar>
          <div style={{ lineHeight: 1.3 }}>
            <div style={{ fontWeight: 600, color: 'var(--text-primary)', fontSize: 13 }}>
              {v}
              {isSelf(r) && <span style={{ marginLeft: 6, fontSize: 11, color: 'var(--accent-color)', background: 'var(--accent-bg)', padding: '1px 6px', borderRadius: 4 }}>当前账号</span>}
            </div>
            <div style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>{r.nickname || '—'}{r.email ? ` · ${r.email}` : ''}</div>
          </div>
        </Space>
      ),
    },
    {
      title: '角色', dataIndex: 'role_level', width: 150,
      render: (v, r) => {
        const m = levelMeta[v] || levelMeta[10];
        return (
          <Tag color={m.tag} style={{ margin: 0, fontSize: 12, padding: '1px 10px', borderRadius: 6, fontWeight: 500 }}>
            {m.icon} {v} · {m.label}
          </Tag>
        );
      },
    },
    { title: '等级调整', dataIndex: 'role_level', width: 150, render: (v, r) => (
      <Select size="small" value={v} disabled={isSelf(r)} style={{ width: 140 }}
        onChange={(nv) => changeRole(r, nv)}
        options={[
          { value: 10, label: '10 · 普通' },
          { value: 50, label: '50 · 高级' },
          { value: 100, label: '100 · 管理员' },
        ]} />
    ) },
    {
      title: '状态', dataIndex: 'status', width: 110,
      render: (v) => (
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 13, color: 'var(--text-secondary)' }}>
          <span style={{ width: 7, height: 7, borderRadius: '50%', background: v === 'active' ? '#10B981' : '#EF4444', boxShadow: v === 'active' ? '0 0 0 3px rgba(16,185,129,0.15)' : '0 0 0 3px rgba(239,68,68,0.12)' }} />
          {v === 'active' ? '正常' : '已禁用'}
        </span>
      ),
    },
    { title: '最近登录', dataIndex: 'last_login_at', width: 150, render: v => v ? v.slice(0, 16).replace('T', ' ') : <span style={{ color: 'var(--text-tertiary)' }}>从未登录</span> },
    { title: '注册时间', dataIndex: 'created_at', width: 150, render: v => v ? v.slice(0, 16).replace('T', ' ') : '-' },
    {
      title: '操作', width: 90, align: 'center',
      render: (_, r) => isSelf(r) ? (
        <span style={{ color: 'var(--text-tertiary)', fontSize: 12 }}>—</span>
      ) : (
        r.status === 'active'
          ? <Popconfirm title={`禁用 ${r.username}？禁用后无法登录与提问`} okText="禁用" cancelText="取消" okButtonProps={{ danger: true }} onConfirm={() => changeStatus(r, 'disabled')}>
              <Button type="link" danger size="small">禁用</Button>
            </Popconfirm>
          : <Button type="link" size="small" style={{ color: '#10B981' }} onClick={() => changeStatus(r, 'active')}>启用</Button>
      ),
    },
  ];

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end', marginBottom: 20 }}>
        <div>
          <h1 className="page-title" style={{ fontSize: 22 }}>用户管理</h1>
          <p className="page-subtitle">管理账号角色等级与启用状态 · 操作全程留痕可审计</p>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <Input.Search
            placeholder="搜索用户名 / 昵称 / 邮箱..."
            style={{ width: 260 }} allowClear
            onSearch={(val) => { setPage(1); fetchUsers(1, val); }}
          />
          <Tooltip title="刷新">
            <Button icon={<ReloadOutlined />} onClick={() => fetchUsers(page, search)} />
          </Tooltip>
        </div>
      </div>

      {/* 统计卡片 */}
      <div style={{ display: 'flex', gap: 12, marginBottom: 16, flexWrap: 'wrap' }}>
        <StatCard icon={<TeamOutlined />} label="用户总数" value={stats.total} color="#4D6BFE" bg="rgba(77,107,254,0.12)" />
        <StatCard icon={<CrownOutlined />} label="管理员 (100)" value={stats.admin} color="#D48806" bg="rgba(212,136,6,0.12)" />
        <StatCard icon={<StarOutlined />} label="高级用户 (50)" value={stats.senior} color="#7C3AED" bg="rgba(124,58,237,0.12)" />
        <StatCard icon={<UserOutlined />} label="普通用户 (10)" value={stats.normal} color="#0EA5E9" bg="rgba(14,165,233,0.12)" />
        <StatCard icon={<StopOutlined />} label="已禁用" value={stats.disabled} color="#EF4444" bg="rgba(239,68,68,0.12)" />
      </div>

      <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
        <Table rowKey="id" size="middle" loading={loading} dataSource={items} columns={columns}
          pagination={{ current: page, pageSize, total, showSizeChanger: false, showTotal: (t) => `共 ${t} 个用户`,
            onChange: (p) => { setPage(p); fetchUsers(p, search); } }}
          locale={{ emptyText: '暂无用户' }}
          rowClassName={() => 'user-row'}
        />
      </div>
    </div>
  );
}
