import React, { useState, useEffect } from 'react';
import { Table, Tag, Switch, Button, Modal, Form, Input, InputNumber, Select, Tooltip, Popconfirm, message, Space, Alert } from 'antd';
import { PlusOutlined, ReloadOutlined, ThunderboltOutlined, ExperimentOutlined, SafetyCertificateOutlined } from '@ant-design/icons';
import { authFetch } from '../utils/api';

const API = import.meta.env.VITE_API_BASE;

const typeTag = {
  exact: ['精确匹配', 'blue'],
  regex: ['正则', 'purple'],
  keyword: ['关键词', 'cyan'],
};

const intentTag = {
  chitchat: ['闲聊', 'green'],
  identity: ['身份', 'geekblue'],
  thanks: ['感谢', 'gold'],
  farewell: ['再见', 'orange'],
  control: ['控制', 'volcano'],
  help: ['帮助', 'cyan'],
};

export default function IntentRulesPage() {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm();
  const [testing, setTesting] = useState(null);
  const [lightEnabled, setLightEnabled] = useState(false);
  const [injEnabled, setInjEnabled] = useState(true);
  const [outEnabled, setOutEnabled] = useState(true);

  const fetchRules = async () => {
    setLoading(true);
    try {
      const res = await authFetch(`${API}/api/intent-rules`);
      const data = await res.json();
      setItems(data.items || []);
    } catch (e) { message.error('加载失败：' + e.message); }
    setLoading(false);
  };

  useEffect(() => { fetchRules(); }, []);

  // 读取系统配置：L2 轻量模型开关
  useEffect(() => {
    authFetch(`${API}/api/system-configs`)
      .then(r => r.json())
      .then(data => {
        const item = (data.items || []).find(c => c.key === 'prefilter_light_enabled');
        if (item) setLightEnabled(item.value === 'true');
        const inj = (data.items || []).find(c => c.key === 'guardrail_injection_enabled');
        if (inj) setInjEnabled(inj.value === 'true');
        const out = (data.items || []).find(c => c.key === 'guardrail_output_enabled');
        if (out) setOutEnabled(out.value === 'true');
      })
      .catch(() => {});
  }, []);

  const handleAdd = async () => {
    try {
      const values = await form.validateFields();
      const res = await authFetch(`${API}/api/intent-rules`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(values),
      });
      const data = await res.json();
      if (data.ok) {
        message.success(data.action === 'update' ? '已更新（幂等覆盖）' : '已新增，热更新立即生效');
        setOpen(false);
        form.resetFields();
        fetchRules();
      } else {
        message.error(data.detail || '保存失败');
      }
    } catch (e) {
      if (e?.response) message.error(e.response.detail || '保存失败');
    }
  };

  const handleToggleLight = async (enabled) => {
    setLightEnabled(enabled);
    try {
      const res = await authFetch(`${API}/api/system-configs`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          key: 'prefilter_light_enabled',
          value: enabled ? 'true' : 'false',
          description: 'L2 轻量模型闲聊分类开关（true=开启，短闲聊走 qwen-turbo 直接应答，省主模型）',
        }),
      });
      const data = await res.json();
      if (data.ok) {
        message.success(enabled ? '已开启 L2：短闲聊将由 qwen-turbo 直接应答（热更新生效）' : '已关闭 L2：闲聊放行主模型（热更新生效）');
      } else {
        setLightEnabled(!enabled);
        message.error(data.detail || '更新失败');
      }
    } catch (e) {
      setLightEnabled(!enabled);
      message.error('更新失败：' + e.message);
    }
  };

  const handleToggleGuardrail = async (key, enabled, label) => {
    try {
      const res = await authFetch(`${API}/api/system-configs`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key, value: enabled ? 'true' : 'false', description: label }),
      });
      const data = await res.json();
      if (data.ok) message.success(`${label}已${enabled ? '开启' : '关闭'}（热更新生效）`);
      else message.error(data.detail || '更新失败');
    } catch (e) { message.error('更新失败：' + e.message); }
  };

  const handleToggle = async (record, enabled) => {
    const res = await authFetch(`${API}/api/intent-rules/${record.id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled }),
    });
    const data = await res.json();
    if (data.ok) {
      message.success(enabled ? '已启用，立即生效' : '已停用');
      fetchRules();
    } else message.error(data.detail || '操作失败');
  };

  const handleDelete = async (record) => {
    const res = await authFetch(`${API}/api/intent-rules`, {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ trigger_type: record.trigger_type, trigger: record.trigger }),
    });
    const data = await res.json();
    if (data.ok) message.success('已删除（软删）');
    else message.error(data.detail || '删除失败');
    fetchRules();
  };

  // 测试：跳转到智能问答验证该话术（热更新是否命中）
  const openTest = (record) => {
    setTesting(record);
    message.info(`在「智能问答」发送「${record.trigger}」即可验证固定应答是否生效`);
  };

  const columns = [
    { title: '类型', dataIndex: 'trigger_type', width: 100, render: v => {
      const [label, color] = typeTag[v] || [v, 'default'];
      return <Tag color={color} style={{ margin: 0, borderRadius: 6 }}>{label}</Tag>;
    } },
    { title: '触发词', dataIndex: 'trigger', width: 220, ellipsis: true, render: (v, r) => (
      <Tooltip title={v}>
        <span style={{ fontFamily: r.trigger_type === 'regex' ? 'Consolas, monospace' : 'inherit', fontSize: 12, color: 'var(--text-primary)' }}>{v}</span>
      </Tooltip>
    ) },
    { title: '固定应答', dataIndex: 'reply_template', ellipsis: true, render: v => (
      <Tooltip title={v}><span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{v}</span></Tooltip>
    ) },
    { title: '意图', dataIndex: 'intent', width: 90, render: v => {
      const [label, color] = intentTag[v] || [v, 'default'];
      return <Tag color={color} style={{ margin: 0, borderRadius: 6 }}>{label}</Tag>;
    } },
    { title: '优先级', dataIndex: 'priority', width: 80, render: v => <span style={{ fontFamily: 'Consolas, monospace' }}>{v}</span> },
    { title: '启用', dataIndex: 'enabled', width: 70, render: (v, r) => (
      <Switch size="small" checked={v} onChange={(e) => handleToggle(r, e)} />
    ) },
    { title: '创建人', dataIndex: 'created_by', width: 110, render: v => v || '-' },
    { title: '操作', width: 160, render: (_, r) => (
      <Space size="small">
        <Button size="small" type="link" icon={<ExperimentOutlined />} onClick={() => openTest(r)}>测试</Button>
        <Popconfirm title="删除后该话术不再命中，确认？" okText="删除" cancelText="取消" okButtonProps={{ danger: true }} onConfirm={() => handleDelete(r)}>
          <Button size="small" type="link" danger>删除</Button>
        </Popconfirm>
      </Space>
    ) },
  ];

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end', marginBottom: 20 }}>
        <div>
          <h1 className="page-title" style={{ fontSize: 22 }}>固定话术管理</h1>
          <p className="page-subtitle">前置过滤第 4 关「规则匹配」——高频问答直接应答，不消耗模型调用</p>
        </div>
        <Space>
          <Tooltip title="刷新">
            <Button icon={<ReloadOutlined />} onClick={fetchRules} />
          </Tooltip>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => { form.resetFields(); setOpen(true); }}>
            新增话术
          </Button>
        </Space>
      </div>

      <Alert
        type="info"
        showIcon
        icon={<ThunderboltOutlined />}
        message="改动即时生效，无需重启服务"
        description="保存后自动清除 Redis 缓存，下一个请求即命中新话术。命中优先级：精确 → 正则 → 关键词，同层取 priority 大者。"
        style={{ marginBottom: 16, borderRadius: 10 }}
      />

      <div className="card" style={{ marginBottom: 16, padding: '14px 16px', display: 'flex', alignItems: 'center', gap: 12 }}>
        <Switch checked={lightEnabled} onChange={handleToggleLight} />
        <div>
          <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-primary)' }}>
            L2 轻量模型闲聊分类（qwen-turbo）
            <Tag color={lightEnabled ? 'green' : 'default'} style={{ marginLeft: 8, borderRadius: 6 }}>{lightEnabled ? '已开启' : '已关闭'}</Tag>
          </div>
          <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginTop: 2 }}>
            开启后：规则未命中的短闲聊（≤30 字）由 qwen-turbo 分类，判定为闲聊则直接应答、不消耗主模型；模型失败自动降级放行。
            关闭后：闲聊直接放行进图由主模型回答。
          </div>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 16, padding: '14px 16px' }}>
        <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-primary)', marginBottom: 10 }}>
          <SafetyCertificateOutlined style={{ marginRight: 6, color: 'var(--accent-color)' }} />
          安全护栏（Prompt 注入防护 + 输出内容审核）
        </div>
        <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, minWidth: 320 }}>
            <Switch checked={injEnabled} onChange={(v) => { setInjEnabled(v); handleToggleGuardrail('guardrail_injection_enabled', v, '输入侧 Prompt 注入防护'); }} />
            <div>
              <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)' }}>
                输入侧 Prompt 注入防护
                <Tag color={injEnabled ? 'green' : 'default'} style={{ marginLeft: 8, borderRadius: 6 }}>{injEnabled ? '已开启' : '已关闭'}</Tag>
              </div>
              <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginTop: 2 }}>
                检测"忽略指令 / 越狱 / 扮演系统管理员 / 泄露 system prompt"等注入，命中直接拦截并审计；单独讨论 system prompt 不误伤
              </div>
            </div>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, minWidth: 320 }}>
            <Switch checked={outEnabled} onChange={(v) => { setOutEnabled(v); handleToggleGuardrail('guardrail_output_enabled', v, '输出内容审核'); }} />
            <div>
              <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)' }}>
                输出内容审核
                <Tag color={outEnabled ? 'green' : 'default'} style={{ marginLeft: 8, borderRadius: 6 }}>{outEnabled ? '已开启' : '已关闭'}</Tag>
              </div>
              <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginTop: 2 }}>
                模型回答生成后复核：敏感词 / 违规词（词库可加）/ 注入回写特征，命中替换为拦截提示并审计
              </div>
            </div>
          </div>
        </div>
      </div>

      <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
        <Table rowKey="id" size="middle" loading={loading} dataSource={items} columns={columns}
          pagination={{ pageSize: 20, showSizeChanger: false, showTotal: (t) => `共 ${t} 条话术` }} />
      </div>

      <Modal title="新增 / 更新固定话术" open={open} onOk={handleAdd} onCancel={() => setOpen(false)}
        okText="保存" cancelText="取消" width={560}>
        <Form form={form} layout="vertical" initialValues={{ trigger_type: 'exact', intent: 'chitchat', priority: 1 }}>
          <Form.Item name="trigger_type" label="匹配类型" rules={[{ required: true }]}>
            <Select options={[
              { value: 'exact', label: '精确匹配（整句一致，最稳）' },
              { value: 'regex', label: '正则（灵活，如 谢谢(你)?）' },
              { value: 'keyword', label: '关键词（子串命中，触发词≥2字）' },
            ]} />
          </Form.Item>
          <Form.Item name="trigger" label="触发词 / 正则" rules={[{ required: true, message: '请输入触发内容' }]}>
            <Input placeholder="如：你好 / ^(谢谢|感谢)(你|啦|了)?([!！。]|\s*)$" style={{ fontFamily: 'Consolas, monospace' }} />
          </Form.Item>
          <Form.Item name="reply_template" label="固定应答内容" rules={[{ required: true, message: '请输入应答内容' }]}>
            <Input.TextArea rows={3} placeholder="如：你好！我是智能问答助手，可以帮你解答文档问题、调用工具、分析数据，有什么想问的吗？" />
          </Form.Item>
          <Form.Item name="intent" label="意图分类" rules={[{ required: true }]}>
            <Select options={[
              { value: 'chitchat', label: '闲聊问候' }, { value: 'identity', label: '身份问答' },
              { value: 'thanks', label: '感谢' }, { value: 'farewell', label: '告别' },
              { value: 'control', label: '对话控制' }, { value: 'help', label: '帮助' },
            ]} />
          </Form.Item>
          <Form.Item name="priority" label="优先级（同层命中取大者，0-100）">
            <InputNumber min={0} max={100} style={{ width: '100%' }} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
