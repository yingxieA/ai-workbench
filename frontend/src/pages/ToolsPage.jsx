import React, { useState, useEffect } from 'react';
import { Table, Tag, Switch, Button, Space, Input, Select, Modal, Form, Tooltip, message, Alert } from 'antd';
import { ReloadOutlined, ApiOutlined, DeleteOutlined, ThunderboltOutlined } from '@ant-design/icons';
import { authFetch } from '../utils/api';

const API = import.meta.env.VITE_API_BASE;

export default function ToolsPage() {
  const [tools, setTools] = useState([]);
  const [loading, setLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [regLoading, setRegLoading] = useState(false);
  const [form] = Form.useForm();

  const fetchTools = async () => {
    setLoading(true);
    try {
      const res = await authFetch(`${API}/api/tools`);
      const data = await res.json();
      setTools(data.tools || []);
    } catch (e) { message.error('加载工具池失败：' + e.message); }
    setLoading(false);
  };

  useEffect(() => { fetchTools(); }, []);

  const toggle = async (name, enabled) => {
    try {
      const res = await authFetch(`${API}/api/tools/${name}/toggle`, { method: 'PUT' });
      const data = await res.json();
      if (data.ok) {
        setTools(tools.map(t => t.name === name ? { ...t, enabled: data.enabled } : t));
        message.success(`「${name}」已${data.enabled ? '启用' : '停用'}`);
      } else {
        message.error(data.detail || '切换失败');
        fetchTools();
      }
    } catch (e) { message.error('切换失败：' + e.message); }
  };

  const unregister = async (name) => {
    try {
      const res = await authFetch(`${API}/api/tools/external/${name}`, { method: 'DELETE' });
      const data = await res.json();
      if (data.ok) {
        setTools(tools.filter(t => t.name !== name));
        message.success(`已卸载「${name}」`);
      } else {
        message.error(data.detail || '卸载失败');
      }
    } catch (e) { message.error('卸载失败：' + e.message); }
  };

  const registerExternal = async (values) => {
    setRegLoading(true);
    try {
      const payload = { ...values, args: (values.args || '').split(' ').filter(Boolean) };
      if (payload.conn_type === 'stdio') payload.url = '';
      else payload.command = '';
      const res = await authFetch(`${API}/api/tools/external`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (data.ok) {
        message.success(`连接成功，已注册 ${data.registered.length} 个工具`);
        setModalOpen(false);
        form.resetFields();
        fetchTools();
      } else {
        message.error(data.detail || '注册失败');
      }
    } catch (e) { message.error('注册失败：' + e.message); }
    setRegLoading(false);
  };

  const columns = [
    { title: '工具名', dataIndex: 'name', width: 190, render: v => <span style={{ fontFamily: 'Consolas, monospace', fontSize: 12, color: 'var(--text-primary)' }}>{v}</span> },
    { title: '描述', dataIndex: 'description', ellipsis: true, render: v => (
      <Tooltip title={v}><span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{v}</span></Tooltip>
    ) },
    { title: '风险', dataIndex: 'risk_text', width: 110, render: (v, r) => (
      <Tag color={r.risk_level === 'high' ? 'red' : 'green'} style={{ margin: 0, fontSize: 12, padding: '1px 10px', borderRadius: 6 }}>{v}</Tag>
    ) },
    { title: '来源', dataIndex: 'source', width: 90, render: v => (
      <Tag color={v === 'external' ? 'purple' : 'blue'} style={{ margin: 0, fontSize: 12, padding: '1px 10px', borderRadius: 6 }}>{v === 'external' ? '外部 MCP' : '内置'}</Tag>
    ) },
    { title: '分类', dataIndex: 'tags', width: 160, render: v => (v || []).map(t => (
      <Tag key={t} style={{ margin: '0 4px 2px 0', fontSize: 11, borderRadius: 6 }}>{t}</Tag>
    )) },
    { title: '状态', dataIndex: 'enabled', width: 110, render: (v, r) => (
      <Switch size="small" checked={!!v} onChange={(checked) => toggle(r.name, checked)} />
    ) },
    { title: '操作', key: 'op', width: 90, render: (_, r) => r.source === 'external' ? (
      <Tooltip title="卸载该外部工具">
        <Button size="small" danger icon={<DeleteOutlined />} onClick={() => unregister(r.name)} />
      </Tooltip>
    ) : <span style={{ color: 'var(--text-tertiary)', fontSize: 12 }}>—</span> },
  ];

  const connType = Form.useWatch('conn_type', form);

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end', marginBottom: 20 }}>
        <div>
          <h1 className="page-title" style={{ fontSize: 22 }}>工具管理</h1>
          <p className="page-subtitle">工具池可视化：启停热切换、接入外部 MCP Server，一切皆插件</p>
        </div>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={fetchTools}>刷新</Button>
          <Button type="primary" icon={<ApiOutlined />} onClick={() => setModalOpen(true)}>注册外部 MCP</Button>
        </Space>
      </div>

      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16, borderRadius: 10 }}
        message="启用中的工具会注入意图路由器的工具清单；停用后即刻从清单消失（无需重启）。高风险工具（红）执行前会弹人工确认。"
      />

      <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
        <Table rowKey="name" size="middle" loading={loading} dataSource={tools} columns={columns}
          pagination={false} />
      </div>

      <Modal
        title="注册外部 MCP Server"
        open={modalOpen}
        onCancel={() => setModalOpen(false)}
        footer={null}
        width={520}
      >
        <Form form={form} layout="vertical" initialValues={{ conn_type: 'stdio', prefix: 'ext_' }} onFinish={registerExternal}>
          <Form.Item name="name" label="连接名称" rules={[{ required: true, message: '请输入连接名称' }]}>
            <Input placeholder="如：github-mcp" />
          </Form.Item>
          <Form.Item name="conn_type" label="连接类型">
            <Select options={[
              { value: 'streamable_http', label: 'Streamable HTTP（推荐，远程 URL 端点）' },
              { value: 'sse', label: 'SSE（远程 URL 端点）' },
              { value: 'stdio', label: 'stdio（本地命令启动，如 npx）' },
            ]} />
          </Form.Item>
          {connType === 'stdio' ? (
            <>
              <Form.Item name="command" label="启动命令" rules={[{ required: true, message: '请输入启动命令' }]}>
                <Input placeholder="如：npx -y @modelcontextprotocol/server-github" />
              </Form.Item>
              <Form.Item name="args" label="命令参数（空格分隔，可选）">
                <Input placeholder="如：--token xxx" />
              </Form.Item>
            </>
          ) : (
            <Form.Item name="url" label="MCP Server 端点" rules={[{ required: true, message: '请输入端点 URL' }]}>
              <Input placeholder="如：https://mcp.amap.com/mcp?key=xxx" />
            </Form.Item>
          )}
          <Form.Item name="prefix" label="工具名前缀（避免与内置冲突）">
            <Input placeholder="ext_" />
          </Form.Item>
          <div style={{ textAlign: 'right' }}>
            <Space>
              <Button onClick={() => setModalOpen(false)}>取消</Button>
              <Button type="primary" htmlType="submit" loading={regLoading} icon={<ThunderboltOutlined />}>连接并注册</Button>
            </Space>
          </div>
        </Form>
      </Modal>
    </div>
  );
}
