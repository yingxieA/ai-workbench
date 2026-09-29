import React, { useState, useEffect } from 'react';
import { Tabs, Table, Tag, Select, Button, Space, Input, Tooltip, message, Popconfirm, Switch, InputNumber, Progress } from 'antd';
import { ReloadOutlined, DownloadOutlined, CheckOutlined, CloseOutlined, UndoOutlined, DollarOutlined, WarningOutlined, SafetyCertificateOutlined } from '@ant-design/icons';
import { authFetch } from '../utils/api';

const API = import.meta.env.VITE_API_BASE;

const fbLabel = { like: ['点赞', 'green'], dislike: ['点踩', 'red'] };
const reviewLabel = { pending: ['待复核', 'orange'], approved: ['已通过', 'green'], rejected: ['已驳回', 'default'] };
const routeLabel = { direct: ['直接回答', 'blue'], rag: ['知识库 RAG', 'geekblue'], tool: ['工具调用', 'purple'], agent_loop: ['多步推理', 'cyan'] };
const verdictLabel = { supported: ['有支撑', 'green'], partially: ['部分支撑', 'orange'], unsupported: ['无支撑', 'red'] };

export default function QualityCenterPage() {
  const [tab, setTab] = useState('feedback');

  // 在线反馈
  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [fbType, setFbType] = useState('');
  const [route, setRoute] = useState('');
  const [reviewStatus, setReviewStatus] = useState('');
  const [loading, setLoading] = useState(false);

  const fetchFeedbacks = async (p = 1, override) => {
    setLoading(true);
    try {
      const o = override || {};
      const params = new URLSearchParams({ skip: (p - 1) * 30, limit: 30 });
      const ft = o.fbType !== undefined ? o.fbType : fbType;
      const rt = o.route !== undefined ? o.route : route;
      const rs = o.reviewStatus !== undefined ? o.reviewStatus : reviewStatus;
      if (ft) params.append('fb_type', ft);
      if (rt) params.append('route', rt);
      if (rs) params.append('review_status', rs);
      const res = await authFetch(`${API}/api/feedbacks?${params}`);
      const data = await res.json();
      setItems(data.items || []);
      setTotal(data.total || 0);
    } catch (e) { message.error('查询失败：' + e.message); }
    setLoading(false);
  };

  useEffect(() => { fetchFeedbacks(1); }, []);

  const review = async (r, status) => {
    try {
      const res = await authFetch(`${API}/api/feedbacks/${r.message_id}/review`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status }),
      });
      if (!res.ok) throw new Error((await res.json()).detail || '复核失败');
      message.success(status === 'approved' ? '已标记为通过（可导出训练集）' : status === 'rejected' ? '已驳回' : '已恢复待定');
      fetchFeedbacks(page);
    } catch (e) { message.error(e.message); }
  };

  const exportDataset = async () => {
    try {
      const res = await authFetch(`${API}/api/feedbacks/export?review_status=approved${fbType ? '&fb_type=' + fbType : ''}`);
      if (!res.ok) throw new Error('导出失败');
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url; a.download = `feedback_dataset_approved.jsonl`;
      a.click();
      URL.revokeObjectURL(url);
      message.success('已导出复核通过的标注数据（JSONL）');
    } catch (e) { message.error(e.message); }
  };

  // 幻觉检测
  const [hItems, setHItems] = useState([]);
  const [hTotal, setHTotal] = useState(0);
  const [hPage, setHPage] = useState(1);
  const [hVerdict, setHVerdict] = useState('');
  const [hLoading, setHLoading] = useState(false);

  const fetchHallucinations = async (p = 1, override) => {
    setHLoading(true);
    try {
      const o = override || {};
      const params = new URLSearchParams({ skip: (p - 1) * 30, limit: 30 });
      const v = o.verdict !== undefined ? o.verdict : hVerdict;
      if (v) params.append('verdict', v);
      const res = await authFetch(`${API}/api/feedbacks/hallucinations?${params}`);
      const data = await res.json();
      setHItems(data.items || []);
      setHTotal(data.total || 0);
    } catch (e) { message.error('查询失败：' + e.message); }
    setHLoading(false);
  };

  const doRefresh = () => {
    if (tab === 'feedback') { setPage(1); fetchFeedbacks(1); }
    else if (tab === 'hallucination') { setHPage(1); fetchHallucinations(1); }
    else if (tab === 'cost') { fetchCosts(); }
  };

  // 成本监控
  const [costData, setCostData] = useState(null);
  const [costLoading, setCostLoading] = useState(false);
  const [costDays, setCostDays] = useState(7);
  const [limitInput, setLimitInput] = useState(null);

  const fetchCosts = async (days = costDays) => {
    setCostLoading(true);
    try {
      const res = await authFetch(`${API}/api/feedbacks/costs?days=${days}`);
      if (!res.ok) throw new Error('查询失败');
      const data = await res.json();
      setCostData(data);
      setLimitInput(data.config?.daily_cost_limit ?? null);
    } catch (e) { message.error('成本数据查询失败：' + e.message); }
    setCostLoading(false);
  };

  const saveCostConfig = async (key, value) => {
    try {
      const res = await authFetch(`${API}/api/system-configs`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key, value: String(value), description: key === 'daily_cost_limit' ? '日成本预算（元）' : '成本超限自动降级开关' }),
      });
      if (!res.ok) throw new Error((await res.json()).detail || '保存失败');
      message.success('成本配置已更新，即时生效');
      fetchCosts();
    } catch (e) { message.error(e.message); }
  };

  const fbColumns = [    { title: '时间', dataIndex: 'created_at', width: 150, render: v => v ? v.slice(0, 19).replace('T', ' ') : '-' },
    { title: '反馈', dataIndex: 'feedback', width: 80, render: v => {
      const [label, color] = fbLabel[v] || [v || '-', 'default'];
      return <Tag color={color} style={{ margin: 0, borderRadius: 6 }}>{label}</Tag>;
    } },
    { title: '路由', dataIndex: 'route', width: 110, render: v => {
      const [label, color] = routeLabel[v] || [v || '-', 'default'];
      return <Tag color={color} style={{ margin: 0, borderRadius: 6 }}>{label}</Tag>;
    } },
    { title: '模型', dataIndex: 'model_used', width: 120, render: v => v || '-' },
    { title: '问题 / 回答', key: 'content', render: (_, r) => (
      <div style={{ maxWidth: 420 }}>
        <div style={{ fontWeight: 500, fontSize: 12, color: 'var(--text-primary)', marginBottom: 4, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {r.role === 'assistant' ? r.session_title : '·'}
        </div>
        <div style={{ fontSize: 12, color: 'var(--text-secondary)', maxHeight: 72, overflow: 'hidden', textOverflow: 'ellipsis', display: '-webkit-box', WebkitLineClamp: 3, WebkitBoxOrient: 'vertical' }}>
          {r.content}
        </div>
      </div>
    ) },
    { title: '点踩原因', dataIndex: 'feedback_reason', width: 160, ellipsis: true, render: v => v || '-' },
    { title: '复核', dataIndex: 'review_status', width: 90, render: v => {
      const [label, color] = reviewLabel[v] || [v, 'default'];
      return <Tag color={color} style={{ margin: 0, borderRadius: 6 }}>{label}</Tag>;
    } },
    { title: '操作', key: 'ops', width: 170, render: (_, r) => (
      <Space size={4}>
        <Tooltip title="标记通过（入训练集）"><Button size="small" type="text" icon={<CheckOutlined />} onClick={() => review(r, 'approved')} /></Tooltip>
        <Tooltip title="驳回"><Button size="small" type="text" danger icon={<CloseOutlined />} onClick={() => review(r, 'rejected')} /></Tooltip>
        {r.review_status !== 'pending' && (
          <Tooltip title="恢复待定"><Button size="small" type="text" icon={<UndoOutlined />} onClick={() => review(r, 'pending')} /></Tooltip>
        )}
      </Space>
    ) },
  ];

  const hColumns = [
    { title: '时间', dataIndex: 'created_at', width: 150, render: v => v ? v.slice(0, 19).replace('T', ' ') : '-' },
    { title: '判定', dataIndex: 'verdict', width: 90, render: v => {
      const [label, color] = verdictLabel[v] || [v || '-', 'default'];
      return <Tag color={color} style={{ margin: 0, borderRadius: 6 }}>{label}</Tag>;
    } },
    { title: '支撑度', dataIndex: 'score', width: 130, render: v => v == null ? '-' : (
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <div style={{ width: 70, height: 6, background: 'var(--border-color)', borderRadius: 3, overflow: 'hidden' }}>
          <div style={{ width: `${Math.round(v * 100)}%`, height: '100%', background: v >= 0.8 ? '#52c41a' : v >= 0.5 ? '#faad14' : '#ff4d4f' }} />
        </div>
        <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{Math.round(v * 100)}%</span>
      </div>
    ) },
    { title: '问题', dataIndex: 'question', width: 200, ellipsis: true },
    { title: '回答 / 片段', key: 'qa', render: (_, r) => (
      <div style={{ maxWidth: 460, fontSize: 12 }}>
        <div style={{ color: 'var(--text-primary)', marginBottom: 2 }}><b>回答</b>：{r.answer?.slice(0, 100) || '-'}</div>
        <div style={{ color: 'var(--text-tertiary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}><b>片段</b>：{r.contexts?.slice(0, 100) || '-'}</div>
      </div>
    ) },
    { title: '判定理由', dataIndex: 'reason', ellipsis: true, render: v => v || '-' },
    { title: '模型', dataIndex: 'model_used', width: 120, render: v => v || '-' },
  ];

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end', marginBottom: 20 }}>
        <div>
          <h1 className="page-title" style={{ fontSize: 22 }}>质量中心</h1>
          <p className="page-subtitle">在线反馈回流 · 幻觉检测 · 成本监控 —— 生产可观测与效果评估</p>
        </div>
        <Space>
          {tab === 'feedback' && (
            <>
              <Button icon={<DownloadOutlined />} onClick={exportDataset}>导出训练集</Button>
            </>
          )}
          <Button type="primary" icon={<ReloadOutlined />} onClick={doRefresh}>刷新</Button>
        </Space>
      </div>

      <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
        <Tabs
          activeKey={tab}
          onChange={(k) => { setTab(k); if (k === 'hallucination') fetchHallucinations(1); else if (k === 'cost') fetchCosts(); }}
          items={[
            { key: 'feedback', label: '在线反馈' },
            { key: 'hallucination', label: '幻觉检测' },
            { key: 'cost', label: '成本监控' },
          ]}
          tabBarStyle={{ padding: '0 16px', marginBottom: 0, background: 'var(--bg-primary)' }}
        />

        {tab === 'feedback' && (
          <>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center', padding: '14px 16px', borderBottom: '1px solid var(--border-color)', background: 'var(--bg-primary)' }}>
              <Select placeholder="反馈类型" allowClear style={{ width: 120 }} value={fbType || undefined}
                onChange={(v) => { setFbType(v || ''); setPage(1); fetchFeedbacks(1, { fbType: v || '', route, reviewStatus }); }}
                options={[{ value: 'like', label: '点赞' }, { value: 'dislike', label: '点踩' }]} />
              <Select placeholder="路由" allowClear style={{ width: 130 }} value={route || undefined}
                onChange={(v) => { setRoute(v || ''); setPage(1); fetchFeedbacks(1, { fbType, route: v || '', reviewStatus }); }}
                options={Object.keys(routeLabel).map(k => ({ value: k, label: routeLabel[k][0] }))} />
              <Select placeholder="复核状态" allowClear style={{ width: 120 }} value={reviewStatus || undefined}
                onChange={(v) => { setReviewStatus(v || ''); setPage(1); fetchFeedbacks(1, { fbType, route, reviewStatus: v || '' }); }}
                options={Object.keys(reviewLabel).map(k => ({ value: k, label: reviewLabel[k][0] }))} />
              <Button onClick={() => { setFbType(''); setRoute(''); setReviewStatus(''); setPage(1); fetchFeedbacks(1, { fbType: '', route: '', reviewStatus: '' }); }}>重置</Button>
              <span style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--text-tertiary)' }}>共 {total} 条反馈</span>
            </div>
            <Table rowKey="message_id" size="middle" loading={loading} dataSource={items} columns={fbColumns}
              pagination={{ current: page, pageSize: 30, total, showSizeChanger: false, showTotal: (t) => `共 ${t} 条`,
                onChange: (p) => { setPage(p); fetchFeedbacks(p); } }} />
          </>
        )}

        {tab === 'hallucination' && (
          <>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center', padding: '14px 16px', borderBottom: '1px solid var(--border-color)', background: 'var(--bg-primary)' }}>
              <Select placeholder="判定结果" allowClear style={{ width: 130 }} value={hVerdict || undefined}
                onChange={(v) => { setHVerdict(v || ''); setHPage(1); fetchHallucinations(1, { verdict: v || '' }); }}
                options={Object.keys(verdictLabel).map(k => ({ value: k, label: verdictLabel[k][0] }))} />
              <Button onClick={() => { setHVerdict(''); setHPage(1); fetchHallucinations(1, { verdict: '' }); }}>重置</Button>
              <span style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--text-tertiary)' }}>共 {hTotal} 条检测</span>
            </div>
            <Table rowKey="id" size="middle" loading={hLoading} dataSource={hItems} columns={hColumns}
              pagination={{ current: hPage, pageSize: 30, total: hTotal, showSizeChanger: false, showTotal: (t) => `共 ${t} 条`,
                onChange: (p) => { setHPage(p); fetchHallucinations(p); } }} />
          </>
        )}

        {tab === 'cost' && (() => {
          const c = costData;
          const guardOn = c?.config?.cost_guard_enabled !== false;
          const over = guardOn && c && c.today_cost >= c.config.daily_cost_limit;
          const maxDaily = Math.max(...(c?.daily || []).map(d => d.cost), 0.001);
          const pricing = c?.config?.pricing || {};
          return (
            <>
              <div style={{ padding: '16px 20px', borderBottom: '1px solid var(--border-color)', background: 'var(--bg-primary)' }}>
                <Space size={24} wrap>
                  <div>
                    <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginBottom: 4 }}>今日累计成本（元）</div>
                    <div style={{ fontSize: 28, fontWeight: 600, color: over ? '#ff4d4f' : 'var(--text-primary)' }}>
                      {c ? c.today_cost.toFixed(4) : '-'}
                    </div>
                  </div>
                  <div>
                    <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginBottom: 4 }}>日预算（元）</div>
                    <InputNumber size="small" min={0.0001} step={0.1} value={limitInput} style={{ width: 120 }}
                      onChange={(v) => setLimitInput(v)} onBlur={() => { if (limitInput !== null && limitInput !== c.config.daily_cost_limit) saveCostConfig('daily_cost_limit', limitInput); }} />
                  </div>
                  <div>
                    <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginBottom: 4 }}>超限自动降级</div>
                    <Switch checked={guardOn} checkedChildren="开" unCheckedChildren="关"
                      onChange={(v) => saveCostConfig('cost_guard_enabled', v)} />
                  </div>
                  <div>
                    <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginBottom: 4 }}>降级状态</div>
                    <Tag color={over ? 'red' : 'green'} style={{ borderRadius: 6 }}>
                      {over ? <><WarningOutlined /> 已超限，生效模型从 qwen-plus 起</> : <><SafetyCertificateOutlined /> 预算内，正常使用 qwen-max</>}
                    </Tag>
                  </div>
                  <div style={{ marginLeft: 'auto' }}>
                    <Select size="small" value={costDays} onChange={(v) => { setCostDays(v); fetchCosts(v); }}
                      options={[{ value: 7, label: '近 7 天' }, { value: 14, label: '近 14 天' }, { value: 30, label: '近 30 天' }]} />
                  </div>
                </Space>
              </div>

              <div style={{ padding: '16px 20px', borderBottom: '1px solid var(--border-color)' }}>
                <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 10 }}>近 {costDays} 天成本趋势</div>
                {c && c.daily.length > 0 ? (
                  <div style={{ display: 'flex', alignItems: 'flex-end', gap: 10, height: 96 }}>
                    {c.daily.map(d => (
                      <div key={d.date} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4 }}>
                        <div style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>{d.cost.toFixed(3)}</div>
                        <div style={{ width: '70%', height: 56, background: 'var(--border-color)', borderRadius: 4, display: 'flex', alignItems: 'flex-end', overflow: 'hidden' }}>
                          <div style={{ width: '100%', height: `${Math.max(d.cost / maxDaily * 100, 2)}%`, background: d.date === c.daily[c.daily.length - 1].date ? '#1677ff' : '#69b1ff', borderRadius: 4 }} />
                        </div>
                        <div style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>{d.date.slice(5)}</div>
                      </div>
                    ))}
                  </div>
                ) : <div style={{ color: 'var(--text-tertiary)', fontSize: 12 }}>暂无成本数据（尚未有 Graph 请求产生费用）</div>}
              </div>

              <div style={{ display: 'flex', gap: 16, padding: 16, flexWrap: 'wrap' }}>
                <div style={{ flex: 1, minWidth: 300, background: 'var(--bg-primary)', borderRadius: 8, border: '1px solid var(--border-color)', overflow: 'hidden' }}>
                  <div style={{ padding: '10px 14px', fontSize: 13, fontWeight: 600, borderBottom: '1px solid var(--border-color)' }}>按模型汇总</div>
                  <Table rowKey="model" size="small" pagination={false} loading={costLoading}
                    dataSource={c?.by_model || []}
                    columns={[
                      { title: '模型', dataIndex: 'model', render: v => <Tag style={{ borderRadius: 6, fontFamily: 'monospace' }}>{v}</Tag> },
                      { title: '调用次数', dataIndex: 'calls', width: 90 },
                      { title: 'Tokens', dataIndex: 'tokens', width: 100, render: v => (v || 0).toLocaleString() },
                      { title: '成本(元)', dataIndex: 'cost', width: 110, render: v => v.toFixed(4) },
                    ]} />
                </div>
                <div style={{ flex: 1, minWidth: 300, background: 'var(--bg-primary)', borderRadius: 8, border: '1px solid var(--border-color)', overflow: 'hidden' }}>
                  <div style={{ padding: '10px 14px', fontSize: 13, fontWeight: 600, borderBottom: '1px solid var(--border-color)' }}>按路由汇总</div>
                  <Table rowKey="route" size="small" pagination={false} loading={costLoading}
                    dataSource={c?.by_route || []}
                    columns={[
                      { title: '路由', dataIndex: 'route', render: v => {
                        const [label, color] = routeLabel[v] || [v, 'default'];
                        return <Tag color={color} style={{ borderRadius: 6 }}>{label}</Tag>;
                      } },
                      { title: '调用次数', dataIndex: 'calls', width: 90 },
                      { title: '成本(元)', dataIndex: 'cost', width: 110, render: v => v.toFixed(4) },
                    ]} />
                </div>
              </div>

              <div style={{ padding: '0 20px 20px' }}>
                <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>模型单价配置（元 / 1K tokens，可在系统配置 model_pricing 调整）</div>
                <Space size={12} wrap>
                  {Object.entries(pricing).map(([m, p]) => (
                    <Tag key={m} style={{ borderRadius: 6, fontSize: 12, padding: '4px 10px' }} color="blue">
                      {m}: {Number(p).toFixed(4)} 元/1K
                    </Tag>
                  ))}
                </Space>
                <div style={{ marginTop: 10, fontSize: 12, color: 'var(--text-tertiary)' }}>
                  <DollarOutlined /> 降级链：qwen-max → qwen-plus → deepseek-chat；超限时自动跳过最高档，全程无需重启。
                </div>
              </div>
            </>
          );
        })()}
      </div>
    </div>
  );
}
