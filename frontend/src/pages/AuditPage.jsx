import React, { useState, useEffect } from 'react';
import { Table, Tag, Select, Button, Space, Input, Tooltip, message } from 'antd';
import { ReloadOutlined, FileSearchOutlined } from '@ant-design/icons';
import { authFetch } from '../utils/api';

const API = import.meta.env.VITE_API_BASE;

const actionLabel = {
  upload_document: ['上传文档', 'blue'],
  delete_document: ['删除文档', 'red'],
  rename_document: ['重命名', 'geekblue'],
  set_permission: ['设置权限', 'purple'],
  review_chunk: ['片段复核', 'magenta'],
  relabel_document: ['重新打标', 'cyan'],
  add_keyword: ['新增敏感词', 'volcano'],
  delete_keyword: ['删除敏感词', 'red'],
  set_role: ['调整角色', 'gold'],
  set_status: ['启停账号', 'orange'],
};

export default function AuditPage() {
  const [items, setItems] = useState([]);
  const [actions, setActions] = useState([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [action, setAction] = useState('');
  const [username, setUsername] = useState('');
  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');
  const [loading, setLoading] = useState(false);
  const pageSize = 30;

  const fetchLogs = async (p = 1, override) => {
    setLoading(true);
    try {
      const o = override || {};
      const params = new URLSearchParams({ skip: (p - 1) * pageSize, limit: pageSize });
      const act = o.action !== undefined ? o.action : action;
      const uid = o.username !== undefined ? o.username : username;
      const st = o.start !== undefined ? o.start : start;
      const ed = o.end !== undefined ? o.end : end;
      if (act) params.append('action', act);
      if (uid) params.append('user_id', uid);
      if (st) params.append('start', st);
      if (ed) params.append('end', ed);
      const res = await authFetch(`${API}/api/audit-logs?${params}`);
      const data = await res.json();
      setItems(data.items || []);
      setTotal(data.total || 0);
      if (data.actions) setActions(data.actions);
    } catch (e) { message.error('查询失败：' + e.message); }
    setLoading(false);
  };

  useEffect(() => { fetchLogs(1); }, []);

  const reset = () => { setAction(''); setUsername(''); setStart(''); setEnd(''); setPage(1); fetchLogs(1, { action: '', username: '', start: '', end: '' }); };

  const columns = [
    { title: '时间', dataIndex: 'created_at', width: 165, render: v => v ? v.slice(0, 19).replace('T', ' ') : '-' },
    { title: '操作人', dataIndex: 'username', width: 130, render: (v, r) => (
      <Tooltip title={`用户ID: ${r.user_id}`}>
        <span style={{ fontWeight: 500, color: 'var(--text-primary)' }}>{v}</span>
      </Tooltip>
    ) },
    { title: '操作类型', dataIndex: 'action', width: 120, render: v => {
      const [label, color] = actionLabel[v] || [v, 'default'];
      return <Tag color={color} style={{ margin: 0, fontSize: 12, padding: '1px 10px', borderRadius: 6 }}>{label}</Tag>;
    } },
    { title: '目标对象', dataIndex: 'target_id', width: 200, ellipsis: true, render: v => v
        ? <Tooltip title={v}><span style={{ fontFamily: 'Consolas, monospace', fontSize: 11, color: 'var(--text-secondary)' }}>{v.slice(0, 8)}…</span></Tooltip>
        : '-' },
    { title: '详情', dataIndex: 'detail', render: v => (
      <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
        {v && typeof v === 'object'
          ? Object.entries(v).map(([k, val]) => (
              <span key={k} style={{ marginRight: 12 }}>
                <span style={{ color: 'var(--text-tertiary)' }}>{k}:</span> {typeof val === 'object' ? JSON.stringify(val) : val}
              </span>
            ))
          : (v || '-')}
      </span>
    ) },
  ];

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end', marginBottom: 20 }}>
        <div>
          <h1 className="page-title" style={{ fontSize: 22 }}>审计日志</h1>
          <p className="page-subtitle">谁、在什么时候、做了什么操作 —— 全链路留痕，可追溯</p>
        </div>
        <Button type="primary" ghost icon={<FileSearchOutlined />} onClick={() => { setPage(1); fetchLogs(1, { action, username, start, end }); }}>
          查询
        </Button>
      </div>

      <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center', padding: '14px 16px', borderBottom: '1px solid var(--border-color)', background: 'var(--bg-primary)' }}>
          <Select placeholder="操作类型" allowClear style={{ width: 150 }} value={action || undefined}
            onChange={(v) => { setAction(v || ''); setPage(1); fetchLogs(1, { action: v || '', username, start, end }); }}
            options={actions.map(a => ({ value: a, label: (actionLabel[a] || [a])[0] }))} />
          <Input placeholder="操作人用户名" style={{ width: 150 }} allowClear value={username}
            onChange={e => setUsername(e.target.value)}
            onPressEnter={() => { setPage(1); fetchLogs(1, { action, username, start, end }); }} />
          <Input placeholder="开始日期 2026-09-01" style={{ width: 160 }} value={start} onChange={e => setStart(e.target.value)} />
          <span style={{ color: 'var(--text-tertiary)' }}>至</span>
          <Input placeholder="结束日期 2026-09-30" style={{ width: 160 }} value={end} onChange={e => setEnd(e.target.value)} />
          <Button onClick={reset}>重置</Button>
          <Tooltip title="刷新">
            <Button icon={<ReloadOutlined />} onClick={() => fetchLogs(page)} />
          </Tooltip>
          <span style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--text-tertiary)' }}>共 {total} 条记录</span>
        </div>
        <Table rowKey="id" size="middle" loading={loading} dataSource={items} columns={columns}
          rowClassName={() => 'audit-row'}
          pagination={{ current: page, pageSize, total, showSizeChanger: false, showTotal: (t) => `共 ${t} 条记录`,
            onChange: (p) => { setPage(p); fetchLogs(p); } }} />
      </div>
    </div>
  );
}
