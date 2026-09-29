import React, { useState, useEffect } from 'react';
import { Tabs, Table, Tag, Select, Button, Space, Input, Tooltip, message, Modal, Popconfirm, Empty } from 'antd';
import { ReloadOutlined, EditOutlined, DeleteOutlined, EyeOutlined, HistoryOutlined, PlusOutlined } from '@ant-design/icons';
import { authFetch } from '../utils/api';

const API = import.meta.env.VITE_API_BASE;

const profileTagColor = ['blue', 'green', 'orange', 'purple', 'cyan', 'magenta'];

export default function MemoryPage() {
  const [tab, setTab] = useState('sessions');
  // 会话记忆
  const [sItems, setSItems] = useState([]);
  const [sTotal, setSTotal] = useState(0);
  const [sPage, setSPage] = useState(1);
  const [sUsername, setSUsername] = useState('');
  const [sHasSummary, setSHasSummary] = useState(false);
  const [sLoading, setSLoading] = useState(false);
  const [refreshLoading, setRefreshLoading] = useState(false);
  // 用户画像
  const [pItems, setPItems] = useState([]);
  const [pTotal, setPTotal] = useState(0);
  const [pPage, setPPage] = useState(1);
  const [pUsername, setPUsername] = useState('');
  const [pLoading, setPLoading] = useState(false);
  const pageSize = 30;

  // ---------- 会话记忆 ----------
  const fetchSessions = async (p = 1, override) => {
    setSLoading(true);
    try {
      const o = override || {};
      const params = new URLSearchParams({ skip: (p - 1) * pageSize, limit: pageSize });
      const un = o.username !== undefined ? o.username : sUsername;
      const hs = o.has_summary !== undefined ? o.has_summary : sHasSummary;
      if (un) params.append('username', un);
      if (hs) params.append('has_summary', 'true');
      const res = await authFetch(`${API}/api/memories/sessions?${params}`);
      const data = await res.json();
      setSItems(data.items || []);
      setSTotal(data.total || 0);
    } catch (e) { message.error('会话记忆查询失败：' + e.message); }
    setSLoading(false);
  };

  // ---------- 用户画像 ----------
  const fetchProfiles = async (p = 1, override) => {
    setPLoading(true);
    try {
      const o = override || {};
      const params = new URLSearchParams({ skip: (p - 1) * pageSize, limit: pageSize });
      const un = o.username !== undefined ? o.username : pUsername;
      if (un) params.append('username', un);
      const res = await authFetch(`${API}/api/memories/profiles?${params}`);
      const data = await res.json();
      setPItems(data.items || []);
      setPTotal(data.total || 0);
    } catch (e) { message.error('用户画像查询失败：' + e.message); }
    setPLoading(false);
  };

  useEffect(() => { fetchSessions(1); fetchProfiles(1); }, []);

  const doRefresh = async () => {
    setRefreshLoading(true);
    setSPage(1); setPPage(1);
    await Promise.all([fetchSessions(1), fetchProfiles(1)]);
    setRefreshLoading(false);
  };

  // ---------- 详情 / 修正 / 删除 ----------
  const [detail, setDetail] = useState(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [editing, setEditing] = useState(null); // { type, session_id|user_id, summary|profile, version }

  const openDetail = async (row) => {
    try {
      const res = await authFetch(`${API}/api/memories/sessions/${row.session_id}`);
      const data = await res.json();
      setDetail({ ...row, ...data });
      setDetailOpen(true);
    } catch (e) { message.error('详情查询失败：' + e.message); }
  };

  const saveEdit = async () => {
    if (!editing) return;
    try {
      const isSession = editing.type === 'session';
      const url = isSession
        ? `${API}/api/memories/sessions/${editing.id}/summary`
        : `${API}/api/memories/profiles/${editing.id}`;
      const body = isSession ? { summary: editing.summary } : { profile: buildProfile() };
      const res = await authFetch(url, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error((await res.json()).detail || '保存失败');
      message.success('已修正');
      setEditing(null);
      fetchSessions(sPage); fetchProfiles(pPage);
    } catch (e) { message.error('保存失败：' + e.message); }
  };

  const deleteMemory = async (row, type) => {
    try {
      const url = type === 'session'
        ? `${API}/api/memories/sessions/${row.session_id}/memory`
        : `${API}/api/memories/profiles/${row.user_id}`;
      const res = await authFetch(url, { method: 'DELETE' });
      if (!res.ok) throw new Error((await res.json()).detail || '删除失败');
      message.success(type === 'session' ? '会话记忆已清除（聊天记录保留）' : '用户画像已清空');
      fetchSessions(sPage); fetchProfiles(pPage);
    } catch (e) { message.error('删除失败：' + e.message); }
  };

  // ---------- 列定义 ----------
  const sessionColumns = [
    { title: '会话', dataIndex: 'title', width: 180, ellipsis: true, render: (v, r) => (
      <Tooltip title={`会话ID: ${r.session_id}`}>
        <span style={{ fontWeight: 500, color: 'var(--text-primary)' }}>{v}</span>
      </Tooltip>
    ) },
    { title: '用户', dataIndex: 'username', width: 120, render: (v, r) => (
      <Tooltip title={`用户ID: ${r.user_id}`}><span style={{ color: 'var(--text-secondary)' }}>{v}</span></Tooltip>
    ) },
    { title: '摘要', dataIndex: 'summary', ellipsis: true, render: v => v ? (
      <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{v}</span>
    ) : <Tag style={{ margin: 0 }}>无摘要</Tag> },
    { title: '版本', dataIndex: 'version', width: 70, render: v => <Tag color="geekblue">{v ?? 0}</Tag> },
    { title: '消息数', dataIndex: 'msg_count', width: 80, render: v => <span style={{ color: 'var(--text-secondary)' }}>{v}</span> },
    { title: '更新时间', dataIndex: 'updated_at', width: 160, render: v => v ? v.slice(0, 19).replace('T', ' ') : '-' },
    { title: '操作', width: 220, fixed: 'right', render: (_, r) => (
      <Space size={4}>
        <Button size="small" type="text" icon={<EyeOutlined />} onClick={() => openDetail(r)}>详情</Button>
        <Button size="small" type="text" icon={<EditOutlined />} onClick={() => openEdit({ type: 'session', id: r.session_id, summary: r.summary || '', version: r.version })}>修正</Button>
        <Popconfirm title="清除该会话记忆（摘要+缓存）？聊天记录保留" okText="清除" cancelText="取消"
          onConfirm={() => deleteMemory(r, 'session')}>
          <Button size="small" type="text" danger icon={<DeleteOutlined />}>清除</Button>
        </Popconfirm>
      </Space>
    ) },
  ];

  const profileColumns = [
    { title: '用户', dataIndex: 'username', width: 150, render: (v, r) => (
      <Tooltip title={`用户ID: ${r.user_id}`}>
        <span style={{ fontWeight: 500, color: 'var(--text-primary)' }}>{v}</span>
      </Tooltip>
    ) },
    { title: '画像属性', dataIndex: 'profile', render: (v) => {
      const entries = Object.entries(v || {});
      if (!entries.length) return <Tag style={{ margin: 0 }}>空画像</Tag>;
      return entries.map(([k, val], i) => (
        <Tag key={k} color={profileTagColor[i % profileTagColor.length]} style={{ marginBottom: 4, fontSize: 12, borderRadius: 6 }}>
          {k}：{typeof val === 'object' ? JSON.stringify(val) : String(val)}
        </Tag>
      ));
    } },
    { title: '版本', dataIndex: 'version', width: 70, render: v => <Tag color="purple">{v ?? 0}</Tag> },
    { title: '更新时间', dataIndex: 'updated_at', width: 160, render: v => v ? v.slice(0, 19).replace('T', ' ') : '-' },
    { title: '操作', width: 200, fixed: 'right', render: (_, r) => (
      <Space size={4}>
        <Button size="small" type="text" icon={<EditOutlined />} onClick={() => openEdit({ type: 'profile', id: r.user_id, profile: r.profile || {}, version: r.version })}>编辑</Button>
        <Popconfirm title="清空该用户画像？" okText="清空" cancelText="取消" onConfirm={() => deleteMemory(r, 'profile')}>
          <Button size="small" type="text" danger icon={<DeleteOutlined />}>清空</Button>
        </Popconfirm>
      </Space>
    ) },
  ];

  // ---------- 编辑弹窗（画像：key-value 行编辑，由系统组装 JSON；摘要：纯文本） ----------
  const [editText, setEditText] = useState('');
  const [profileRows, setProfileRows] = useState([]); // [{key, value}] 反显行

  const openEdit = (e) => {
    setEditing(e);
    if (e.type === 'session') {
      setEditText(e.summary);
    } else {
      // 反显：对象展开为行（值若是对象/数组则 stringify 展示，保存时还原）
      setProfileRows(Object.entries(e.profile || {}).map(([k, v]) => ({
        key: k,
        value: v && typeof v === 'object' ? JSON.stringify(v) : String(v ?? ''),
      })));
    }
  };
  const onEditChange = (v) => {
    setEditText(v);
    setEditing(prev => prev ? { ...prev, summary: v } : prev);
  };

  // 行编辑：纯状态更新，无 updater 内 JSON.parse 陷阱
  const onRowChange = (idx, field, val) => {
    setProfileRows(rows => rows.map((r, i) => i === idx ? { ...r, [field]: val } : r));
  };
  const addRow = () => setProfileRows(rows => [...rows, { key: '', value: '' }]);
  const removeRow = (idx) => setProfileRows(rows => rows.filter((_, i) => i !== idx));

  // 校验：key 非空 + 无重复；值允许任意文本（合法 JSON 自动结构化）
  const rowKeys = profileRows.map(r => r.key.trim()).filter(Boolean);
  const dupKeys = rowKeys.filter((k, i) => rowKeys.indexOf(k) !== i);
  const profileOk = rowKeys.length > 0 && dupKeys.length === 0;

  const buildProfile = () => {
    const obj = {};
    for (const r of profileRows) {
      const k = r.key.trim();
      if (!k) continue;
      let v = (r.value || '').trim();
      if (v) {
        try {
          const parsed = JSON.parse(v);
          if (typeof parsed !== 'string') v = parsed; // 数组/对象/数字/布尔 → 结构化
        } catch (e) { /* 普通文本保持字符串 */ }
      }
      obj[k] = v;
    }
    return obj;
  };

  const parseOk = editing?.type === 'session' ? editText.trim().length > 0 : profileOk;

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end', marginBottom: 16 }}>
        <div>
          <h1 className="page-title" style={{ fontSize: 22 }}>记忆管理</h1>
          <p className="page-subtitle">AI 记住了什么、对不对、要不要清 —— 会话摘要与用户画像的观测控制台</p>
        </div>
        <Button type="primary" icon={<ReloadOutlined />} loading={refreshLoading} onClick={doRefresh}>刷新</Button>
      </div>

      <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
        <Tabs
          activeKey={tab} onChange={setTab} style={{ padding: '0 16px' }}
          items={[
            { key: 'sessions', label: <span><HistoryOutlined /> 会话记忆</span>,
              children: (
                <div>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center', padding: '0 0 14px', borderBottom: '1px solid var(--border-color)' }}>
                    <Input placeholder="按用户名搜索" style={{ width: 180 }} allowClear value={sUsername}
                      onChange={e => setSUsername(e.target.value)}
                      onPressEnter={() => { setSPage(1); fetchSessions(1, { username: sUsername, has_summary: sHasSummary }); }} />
                    <Select placeholder="摘要状态" allowClear style={{ width: 140 }} value={sHasSummary ? 'has' : undefined}
                      onChange={(v) => { setSHasSummary(!!v); setSPage(1); fetchSessions(1, { username: sUsername, has_summary: !!v }); }}
                      options={[{ value: 'has', label: '仅有摘要' }]} />
                    <Button onClick={() => { setSUsername(''); setSHasSummary(false); setSPage(1); fetchSessions(1, { username: '', has_summary: false }); }}>重置</Button>
                    <span style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--text-tertiary)' }}>共 {sTotal} 个会话</span>
                  </div>
                  <Table rowKey="session_id" size="middle" loading={sLoading} dataSource={sItems} columns={sessionColumns} scroll={{ x: 1100 }}
                    pagination={{ current: sPage, pageSize, total: sTotal, showSizeChanger: false, showTotal: t => `共 ${t} 条`,
                      onChange: (p) => { setSPage(p); fetchSessions(p); } }} />
                </div>
              ) },
            { key: 'profiles', label: <span><EditOutlined /> 用户画像</span>,
              children: (
                <div>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center', padding: '0 0 14px', borderBottom: '1px solid var(--border-color)' }}>
                    <Input placeholder="按用户名搜索" style={{ width: 180 }} allowClear value={pUsername}
                      onChange={e => setPUsername(e.target.value)}
                      onPressEnter={() => { setPPage(1); fetchProfiles(1, { username: pUsername }); }} />
                    <Button onClick={() => { setPUsername(''); setPPage(1); fetchProfiles(1, { username: '' }); }}>重置</Button>
                    <span style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--text-tertiary)' }}>共 {pTotal} 个画像</span>
                  </div>
                  <Table rowKey="user_id" size="middle" loading={pLoading} dataSource={pItems} columns={profileColumns} scroll={{ x: 900 }}
                    pagination={{ current: pPage, pageSize, total: pTotal, showSizeChanger: false, showTotal: t => `共 ${t} 条`,
                      onChange: (p) => { setPPage(p); fetchProfiles(p); } }} />
                </div>
              ) },
          ]}
        />
      </div>

      {/* 详情弹窗 */}
      <Modal title={`会话详情 · ${detail?.title || ''}`} open={detailOpen} onCancel={() => setDetailOpen(false)} footer={null} width={640}>
        {detail && (
          <div>
            <div style={{ marginBottom: 12 }}>
              <span style={{ color: 'var(--text-tertiary)' }}>摘要（v{detail.version}）：</span>
              <div style={{ marginTop: 6, padding: '8px 12px', background: 'var(--bg-secondary)', borderRadius: 8, fontSize: 13, lineHeight: 1.7, color: 'var(--text-primary)', whiteSpace: 'pre-wrap' }}>
                {detail.summary || <Empty description="无摘要" image={Empty.PRESENTED_IMAGE_SIMPLE} />}
              </div>
            </div>
            <div style={{ fontSize: 13, color: 'var(--text-tertiary)', marginBottom: 6 }}>最近对话（辅助核对摘要准确性）：</div>
            <div style={{ maxHeight: 300, overflowY: 'auto', border: '1px solid var(--border-color)', borderRadius: 8, padding: 8 }}>
              {(detail.recent_messages || []).map((m, i) => (
                <div key={i} style={{ marginBottom: 8, padding: '6px 10px', borderRadius: 6, background: m.role === 'user' ? 'var(--bg-secondary)' : 'transparent' }}>
                  <Tag color={m.role === 'user' ? 'blue' : 'green'} style={{ marginRight: 8, fontSize: 11 }}>{m.role === 'user' ? '用户' : 'AI'}</Tag>
                  <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{m.content}</span>
                </div>
              ))}
              {!(detail.recent_messages || []).length && <Empty description="暂无消息" image={Empty.PRESENTED_IMAGE_SIMPLE} />}
            </div>
          </div>
        )}
      </Modal>

      {/* 修正/编辑弹窗 */}
      <Modal
        title={editing?.type === 'session' ? `修正会话摘要（当前 v${editing?.version ?? 0}）` : `编辑用户画像（当前 v${editing?.version ?? 0}）`}
        open={!!editing} onCancel={() => setEditing(null)} onOk={saveEdit}
        okText="保存" cancelText="取消" okButtonProps={{ disabled: !parseOk }}
      >
        {editing?.type === 'session' ? (
          <Input.TextArea rows={6} value={editText} onChange={e => onEditChange(e.target.value)}
            placeholder="人工修正后的摘要（将注入后续对话）" />
        ) : (
          <div>
            <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginBottom: 8 }}>
              属性名 / 属性值 —— 值输入数组、数字等合法 JSON 会自动结构化存储，普通文本存为字符串
            </div>
            {profileRows.map((row, i) => (
              <div key={i} style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
                <Input placeholder="属性名（key）" value={row.key}
                  onChange={e => onRowChange(i, 'key', e.target.value)} style={{ width: 200 }} />
                <Input placeholder="属性值（value）" value={row.value}
                  onChange={e => onRowChange(i, 'value', e.target.value)} />
                <Button type="text" danger icon={<DeleteOutlined />} onClick={() => removeRow(i)} />
              </div>
            ))}
            <Button type="dashed" block icon={<PlusOutlined />} onClick={addRow} style={{ marginTop: 4 }}>添加属性</Button>
            {dupKeys.length > 0 && (
              <div style={{ marginTop: 8, fontSize: 12, color: '#e5534b' }}>属性名重复：{dupKeys.join('、')}</div>
            )}
            {profileRows.length === 0 && (
              <div style={{ marginTop: 8, fontSize: 12, color: 'var(--text-tertiary)' }}>暂无属性，点击"添加属性"新增</div>
            )}
          </div>
        )}
      </Modal>
    </div>
  );
}
