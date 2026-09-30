import React, { useState, useEffect } from 'react';
import { Modal, Select, InputNumber, Table, Tag, Button, Space, message, Input, Popconfirm, Empty } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import { authFetch } from '../utils/api';

const API_BASE = import.meta.env.VITE_API_BASE;

/* ---------- 文档级权限设置 ---------- */
export function PermissionModal({ doc, onClose, onUpdated }) {
  const [visibility, setVisibility] = useState(doc?.visibility || 'internal');
  const [minLevel, setMinLevel] = useState(doc?.min_level || 10);
  const [saving, setSaving] = useState(false);

  const save = async () => {
    setSaving(true);
    try {
      const res = await authFetch(`${API_BASE}/api/documents/${doc.id}/permission`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ visibility, min_level: minLevel }),
      });
      if (res.ok) {
        message.success('权限已更新');
        onUpdated?.();
        onClose();
      } else {
        const d = await res.json();
        message.error(d.detail || '更新失败');
      }
    } finally { setSaving(false); }
  };

  return (
    <Modal title={`文档权限：${doc?.title || ''}`} open={!!doc} onCancel={onClose} onOk={save}
      confirmLoading={saving} okText="保存" cancelText="取消" width={460}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        <div>
          <div style={{ marginBottom: 6, fontWeight: 500 }}>可见范围</div>
          <Select value={visibility} onChange={setVisibility} style={{ width: '100%' }}
            options={[
              { value: 'public', label: '公开（所有用户可见）' },
              { value: 'internal', label: '内部（按等级可见）' },
              { value: 'restricted', label: '受限（仅本人/管理员）' },
            ]} />
        </div>
        <div>
          <div style={{ marginBottom: 6, fontWeight: 500 }}>最低可见等级（用户 role_level ≥ 此值才可见）</div>
          <InputNumber min={10} max={100} step={10} value={minLevel} onChange={setMinLevel} style={{ width: '100%' }} />
          <div style={{ fontSize: 12, color: '#9CA3AF', marginTop: 4 }}>10=普通用户 · 50=高级用户 · 100=仅管理员</div>
        </div>
      </div>
    </Modal>
  );
}

/* ---------- 敏感片段复核（chunks 列表 + 改标） ---------- */
export function ReviewModal({ doc, onClose, onUpdated }) {
  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const pageSize = 20;

  const fetchChunks = async (p = 1) => {
    setLoading(true);
    try {
      const res = await authFetch(`${API_BASE}/api/documents/${doc.id}/chunks?skip=${(p - 1) * pageSize}&limit=${pageSize}`);
      const data = await res.json();
      setItems(data.items || []);
      setTotal(data.total || 0);
    } finally { setLoading(false); }
  };

  useEffect(() => { if (doc) fetchChunks(1); }, [doc?.id]);

  const changeLabel = async (chunk, sensitivity, minLevel) => {
    const res = await authFetch(`${API_BASE}/api/documents/${doc.id}/chunks/${chunk.id}`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sensitivity, min_level: minLevel, reason: '人工复核修改' }),
    });
    if (res.ok) {
      message.success('已更新');
      fetchChunks(page);
      onUpdated?.();
    } else {
      const d = await res.json();
      message.error(d.detail || '更新失败');
    }
  };

  const levelColor = { public: 'green', internal: 'blue', secret: 'red' };
  const levelMin = { public: 10, internal: 50, secret: 100 };

  const columns = [
    { title: '#', dataIndex: 'chunk_index', width: 50 },
    { title: '片段内容', dataIndex: 'content', ellipsis: true, render: v => <span style={{ fontSize: 12 }}>{v}</span> },
    { title: '类型', dataIndex: 'chunk_type', width: 80, render: v => <Tag>{v}</Tag> },
    {
      title: '敏感等级', dataIndex: 'sensitivity', width: 160,
      render: (v, r) => (
        <Select size="small" value={v || 'public'} style={{ width: 130 }}
          onChange={(nv) => changeLabel(r, nv, levelMin[nv])}
          options={[
            { value: 'public', label: '公开' },
            { value: 'internal', label: '内部' },
            { value: 'secret', label: '机密' },
          ]} />
      ),
    },
    { title: '标记依据', dataIndex: 'classify_reason', ellipsis: true, render: v => <span style={{ fontSize: 12, color: '#6B7280' }}>{v || '-'}</span> },
  ];

  return (
    <Modal title={`敏感片段复核：${doc?.title || ''}`} open={!!doc} onCancel={onClose} footer={null} width={900}>
      <Table rowKey="id" size="small" loading={loading} dataSource={items} columns={columns}
        pagination={{ current: page, pageSize, total, onChange: (p) => { setPage(p); fetchChunks(p); }, showSizeChanger: false }}
      />
    </Modal>
  );
}

/* ---------- 敏感词管理（运营配置，DB+Redis 热更新） ---------- */
export function KeywordModal({ open, onClose }) {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(false);
  const [category, setCategory] = useState('sensitive');
  const [keyword, setKeyword] = useState('');
  const [level, setLevel] = useState('secret');

  const fetchAll = async () => {
    setLoading(true);
    try {
      const res = await authFetch(`${API_BASE}/api/rule-keywords`);
      const data = await res.json();
      setItems(data.items || []);
    } finally { setLoading(false); }
  };

  useEffect(() => { if (open) fetchAll(); }, [open]);

  const add = async () => {
    if (!keyword.trim()) { message.warning('请输入关键词'); return; }
    const res = await authFetch(`${API_BASE}/api/rule-keywords`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ category, keyword: keyword.trim(), level }),
    });
    if (res.ok) {
      message.success('已生效（无需重启）');
      setKeyword('');
      fetchAll();
    } else {
      const d = await res.json();
      message.error(d.detail || '添加失败');
    }
  };

  const remove = async (item) => {
    const res = await authFetch(`${API_BASE}/api/rule-keywords`, {
      method: 'DELETE', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ category: item.category, keyword: item.keyword }),
    });
    if (res.ok) { message.success('已删除'); fetchAll(); }
  };

  const catColor = { sensitive: 'red', abuse: 'volcano', classify: 'blue' };
  const catLabel = { sensitive: '敏感词', abuse: '违规词', classify: '分类词' };

  return (
    <Modal title="敏感词 / 违规词 / 分类关键词管理" open={open} onCancel={onClose} footer={null} width={720}>
      <div style={{ display: 'flex', gap: 8, marginBottom: 16, flexWrap: 'wrap' }}>
        <Select value={category} onChange={setCategory} style={{ width: 130 }}
          options={[{ value: 'sensitive', label: '敏感词' }, { value: 'abuse', label: '违规词(输出审核)' }, { value: 'classify', label: '分类词' }]} />
        <Input placeholder="关键词，如：薪酬" value={keyword} onChange={e => setKeyword(e.target.value)}
          onPressEnter={add} style={{ flex: 1, minWidth: 160 }} />
        <Select value={level} onChange={setLevel} style={{ width: 120 }}
          options={[{ value: 'secret', label: '机密(100)' }, { value: 'internal', label: '内部(50)' }, { value: 'public', label: '公开(10)' }]} />
        <Button type="primary" onClick={add}>新增</Button>
      </div>
      <div style={{ fontSize: 12, color: '#9CA3AF', marginBottom: 8 }}>
        配置写入数据库并缓存于 Redis，删除缓存后下个请求自动生效，无需重启服务
      </div>
      {items.length === 0 && !loading ? (
        <Empty description="暂无关键词，添加一个试试（如：薪酬 → 机密）" />
      ) : (
        <Table rowKey={(r) => `${r.category}-${r.keyword}`} size="small" loading={loading} dataSource={items}
          pagination={false}
          columns={[
            { title: '类别', dataIndex: 'category', width: 110, render: v => <Tag color={catColor[v] || 'default'}>{catLabel[v] || v}</Tag> },
            { title: '关键词', dataIndex: 'keyword' },
            { title: '命中等级', dataIndex: 'level', width: 120, render: v => <Tag color={v === 'secret' ? 'red' : v === 'internal' ? 'blue' : 'green'}>{v}</Tag> },
            { title: '状态', dataIndex: 'enabled', width: 80, render: v => (v ? <Tag color="green">启用</Tag> : <Tag>禁用</Tag>) },
            { title: '创建人', dataIndex: 'created_by', width: 100 },
            {
              title: '', width: 60,
              render: (_, r) => <Popconfirm title="删除该关键词？" onConfirm={() => remove(r)}><Button type="link" danger size="small">删除</Button></Popconfirm>,
            },
          ]}
        />
      )}
    </Modal>
  );
}
