import React, { useState, useEffect } from 'react';
import { Button, Form, Input, Modal, Drawer, Tabs, Tag, Space, Progress, Collapse, Checkbox, Divider, Steps, Popconfirm, Dropdown, Select, message } from 'antd';
import { ReloadOutlined, MoreOutlined, DeleteOutlined, ArrowUpOutlined, ArrowDownOutlined, PlayCircleOutlined, FileTextOutlined, GithubOutlined, ArrowRightOutlined } from '@ant-design/icons';
import { authFetch } from '../utils/api';

function SkillsPage({ t }) {
  const [skills, setSkills] = useState([]);
  const [modalOpen, setModalOpen] = useState(false);
  const [form] = Form.useForm();
  const [drawerSkill, setDrawerSkill] = useState(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [newResource, setNewResource] = useState('');
  const [verifying, setVerifying] = useState(false);
  const [quizData, setQuizData] = useState(null);
  const [quizLoading, setQuizLoading] = useState(false);
  const [sortBy, setSortBy] = useState('progress');
  const [aiTaskLoading, setAiTaskLoading] = useState(null);
  const [aiLoading, setAiLoading] = useState(false);
  const [resourceLoading, setResourceLoading] = useState(false);

  const fetchSkills = async (sort) => {
    const s = sort || sortBy;
    const res = await authFetch(`${import.meta.env.VITE_API_BASE}/api/skills?sort=${s}`);
    setSkills(await res.json());
  };

  useEffect(() => { fetchSkills(); }, []);

  const handleAdd = async () => {
    try {
      const values = await form.validateFields();
      await authFetch(`${import.meta.env.VITE_API_BASE}/api/skills`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(values) });
      message.success('添加成功'); setModalOpen(false); form.resetFields(); fetchSkills();
    } catch (e) { console.error(e); }
  };

  const handleUpdate = async (id, data) => {
    await authFetch(`${import.meta.env.VITE_API_BASE}/api/skills/${id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
    fetchSkills();
  };

  const openDrawer = async (s) => {
    setDrawerSkill({ ...s });
    setDrawerOpen(true);
    const res = await authFetch(`${import.meta.env.VITE_API_BASE}/api/skills`);
    const list = await res.json();
    const fresh = list.find(x => x.id === s.id);
    if (fresh) setDrawerSkill({ ...fresh });
  };

  const toggleSubTask = async (idx) => {
    const task = (drawerSkill.tasks || [])[idx];
    if (!task) return;
    const completed = !task.completed;
    const tasks = (drawerSkill.tasks || []).map((t, i) => i === idx ? { ...t, completed } : t);
    setDrawerSkill({ ...drawerSkill, tasks });
    await authFetch(`${import.meta.env.VITE_API_BASE}/api/skills/${drawerSkill.id}/tasks/${task.id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ completed }) });
    fetchSkills();
  };

  const addSubTask = async (text) => {
    if (!text.trim()) return;
    const res = await authFetch(`${import.meta.env.VITE_API_BASE}/api/skills/${drawerSkill.id}/tasks`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title: text }) });
    const data = await res.json();
    setDrawerSkill({ ...drawerSkill, tasks: [...(drawerSkill.tasks || []), data] });
  };

  const moveTask = async (idx, dir) => {
    const tasks = [...(drawerSkill.tasks || [])];
    const j = idx + dir;
    [tasks[idx], tasks[j]] = [tasks[j], tasks[idx]];
    setDrawerSkill({ ...drawerSkill, tasks });
  };

  const deleteTask = async (taskId) => {
    await authFetch(`${import.meta.env.VITE_API_BASE}/api/skills/${drawerSkill.id}/tasks/${taskId}`, { method: 'DELETE' });
    setDrawerSkill({ ...drawerSkill, tasks: (drawerSkill.tasks || []).filter(t => t.id !== taskId) });
  };

  const handleTaskGuide = async (i, t) => {
    setAiTaskLoading(i);
    try {
      const res = await authFetch(`${import.meta.env.VITE_API_BASE}/api/skills/${drawerSkill.id}/tasks/${t.id}/generate_guide`, { method: 'POST' });
      const data = await res.json();
      if (data.task) {
        const tasks = (drawerSkill.tasks || []).map((x, j) => j === i ? { ...x, ...data.task } : x);
        setDrawerSkill({ ...drawerSkill, tasks });
        message.success('AI 生成完成');
      }
    } finally { setAiTaskLoading(null); }
  };

  const addResource = () => {
    if (!newResource.trim()) return;
    const resources = [...(drawerSkill.resources || []), { url: newResource, title: newResource }];
    setDrawerSkill({ ...drawerSkill, resources });
    handleUpdate(drawerSkill.id, { resources });
    setNewResource('');
  };

  const handleAIGenerate = async () => {
    setAiLoading(true);
    const res = await authFetch(`${import.meta.env.VITE_API_BASE}/api/skills/${drawerSkill.id}/generate_subtasks`, { method: 'POST' });
    const data = await res.json();
    if (data.tasks) { setDrawerSkill({ ...drawerSkill, tasks: data.tasks }); message.success('AI 拆解完成'); }
    else { message.error('AI 生成失败'); }
    setAiLoading(false);
  };

  const handleAIResources = async () => {
    setResourceLoading(true);
    const res = await authFetch(`${import.meta.env.VITE_API_BASE}/api/skills/${drawerSkill.id}/generate_resources`, { method: 'POST' });
    const data = await res.json();
    if (data.resources) { setDrawerSkill({ ...drawerSkill, resources: data.resources }); message.success('AI 推荐完成'); }
    else { message.error('推荐失败'); }
    setResourceLoading(false);
  };

  return (
    <div style={{ paddingTop: 32 }}>
      <div style={{ textAlign: 'center', marginBottom: 24 }}>
        <h1 className="page-title">技能管理</h1>
        <p className="page-subtitle">项目式学习，目标拆解 + 实战检验</p>
      </div>

      {/* 顶部操作按钮：左对齐 */}
      <div style={{ display: 'flex', justifyContent: 'flex-start', marginBottom: 16 }}>
        <Space size={8}>
          <Button type="primary" onClick={() => setModalOpen(true)}>添加技能</Button>
          <Button onClick={fetchSkills} icon={<ReloadOutlined />}>刷新</Button>
          <Select defaultValue="progress" style={{ width: 140 }} onChange={(v) => { setSortBy(v); fetchSkills(v); }} options={[
            { value: 'progress', label: '按进度排序' }, { value: 'created', label: '按创建时间' },
          ]} />
        </Space>
      </div>

      {/* 技能卡片网格 */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: 16 }}>
        {skills.map(s => (
          <div key={s.id} className="skill-card" style={{
            padding: 16,
            borderRadius: 8,
            border: '1px solid #E5E7EB',
            background: '#fff',
            cursor: 'pointer',
            position: 'relative',
            transition: 'all 0.2s ease',
            boxShadow: '0 2px 8px rgba(0, 0, 0, 0.04)',
          }}
          onClick={() => openDrawer(s)}
          onMouseEnter={e => {
            e.currentTarget.style.boxShadow = '0 4px 12px rgba(0, 0, 0, 0.08)';
            e.currentTarget.style.transform = 'translateY(-2px)';
          }}
          onMouseLeave={e => {
            e.currentTarget.style.boxShadow = '0 2px 8px rgba(0, 0, 0, 0.04)';
            e.currentTarget.style.transform = 'translateY(0)';
          }}
          >
            {/* 第一行：标题 + 状态 + 更多 */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
              <div style={{
                fontSize: 15,
                fontWeight: 600,
                color: '#111827',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
                flex: 1,
                marginRight: 8,
              }}>
                {s.name}
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
                {/* 状态标签 */}
                {s.status === 'learning' && (
                  <Tag style={{ background: '#FFF7ED', border: 'none', color: '#EA580C', margin: 0 }}>学习中</Tag>
                )}
                {s.status === 'done' && (
                  <Tag style={{ background: '#F0FDF4', border: 'none', color: '#16A34A', margin: 0 }}>已掌握</Tag>
                )}
                {(!s.status || s.status === 'todo') && (
                  <Tag style={{ background: '#F3F4F6', border: 'none', color: '#4B5563', margin: 0 }}>待学习</Tag>
                )}
                {/* 更多操作 */}
                <Dropdown trigger={['click']} menu={{
                  items: [
                    { key: 'edit_goal', label: '编辑目标', onClick: () => openDrawer(s) },
                    { key: 'delete', label: (
                      <Popconfirm title="确定删除该技能？" okText="删除" cancelText="取消" okButtonProps={{ danger: true }} onConfirm={async () => {
                        await authFetch(`${import.meta.env.VITE_API_BASE}/api/skills/${s.id}`, { method: 'DELETE' });
                        fetchSkills();
                      }}><span style={{ color: '#ff4d4f' }}>删除</span></Popconfirm>
                    )}
                  ]
                }}>
                  <MoreOutlined style={{ color: '#9CA3AF', fontSize: 14 }} onClick={(e) => e.stopPropagation()} />
                </Dropdown>
              </div>
            </div>

            {/* 第二行：描述 */}
            {s.goal && (
              <div style={{
                fontSize: 13,
                color: '#4B5563',
                lineHeight: 1.5,
                marginBottom: 12,
                display: '-webkit-box',
                WebkitLineClamp: 2,
                WebkitBoxOrient: 'vertical',
                overflow: 'hidden',
              }}>
                {s.goal}
              </div>
            )}

            {/* 第三行：分类标签 */}
            {s.category && (
              <Tag style={{ background: '#F3F4F6', border: 'none', color: '#4B5563', marginBottom: 12 }}>
                {s.category}
              </Tag>
            )}

            {/* 第四行：进度条（整行） */}
            <div style={{ marginBottom: 12 }}>
              <Progress
                percent={s.progress || 0}
                strokeColor="#4D6BFE"
                trailColor="#F3F4F6"
                size="small"
                style={{ marginBottom: 0 }}
              />
            </div>

            {/* 第五行：开始学习按钮（右下角） */}
            <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
              <Button
                type="link"
                size="small"
                icon={<ArrowRightOutlined />}
                style={{ color: '#4D6BFE', padding: 0, fontSize: 13, fontWeight: 500 }}
                onClick={() => openDrawer(s)}
              >
                开始学习
              </Button>
            </div>
          </div>
        ))}
      </div>
      <Modal open={modalOpen} title="添加技能" onOk={handleAdd} onCancel={() => setModalOpen(false)} okText="确定" cancelText="取消">
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="技能名称" rules={[{ required: true }]}><Input placeholder="如：Dify 开发" /></Form.Item>
          <Form.Item name="category" label="分类"><Input placeholder="如：AI 应用" /></Form.Item>
          <Form.Item name="goal" label="学习目标"><Input.TextArea placeholder="学会用 Dify 搭建 RAG 应用..." rows={3} /></Form.Item>
        </Form>
      </Modal>

      <Drawer open={drawerOpen} onClose={() => setDrawerOpen(false)} width={800} title={drawerSkill?.name}>
        {drawerSkill && (
          <Tabs defaultActiveKey="1" items={[
            {
              key: '1', label: '目标与拆解',
              children: (
                <div>
                  <h4>学习目标</h4>
                  <Input.TextArea value={drawerSkill.goal || ''} onChange={(e) => setDrawerSkill({ ...drawerSkill, goal: e.target.value })} onBlur={() => handleUpdate(drawerSkill.id, { goal: drawerSkill.goal })} autoSize={{ minRows: 2, maxRows: 5 }} placeholder="未设定目标" style={{ background: '#faf8f5', borderRadius: 8 }} />
                  <h4 style={{ marginTop: 24, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <span>子任务清单<span style={{ marginLeft: 12, fontSize: 12, color: 'var(--text-tertiary)' }}>{(drawerSkill.tasks || []).filter(t => t.completed).length}/{(drawerSkill.tasks || []).length}</span></span>
                    <Button type="link" size="small" onClick={handleAIGenerate} loading={aiLoading}>✨ AI 一键拆解</Button>
                  </h4>
                  <Progress percent={Math.round(((drawerSkill.tasks || []).filter(t => t.completed).length / Math.max((drawerSkill.tasks || []).length, 1)) * 100)} size="small" strokeColor="var(--accent-color)" style={{ marginBottom: 12 }} />
                  <Collapse style={{ background: '#fff' }} items={(drawerSkill.tasks || []).map((t, i) => ({
                    key: i,
                    label: (
                      <div className="task-row" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                        <Checkbox checked={t.completed} onChange={() => toggleSubTask(i)} onClick={(e) => e.stopPropagation()}>
                          <span style={{ textDecoration: t.completed ? 'line-through' : 'none', color: t.completed ? 'var(--text-tertiary)' : 'var(--text-primary)' }}>{t.title}</span>
                        </Checkbox>
                        <Space size={4} className="task-actions" style={{ opacity: 0, transition: 'opacity 0.25s' }} onClick={(e) => e.stopPropagation()}>
                          <Button size="small" type="text" disabled={i === 0} onClick={() => moveTask(i, -1)}><ArrowUpOutlined /></Button>
                          <Button size="small" type="text" disabled={i === (drawerSkill.tasks || []).length - 1} onClick={() => moveTask(i, 1)}><ArrowDownOutlined /></Button>
                          <Popconfirm title="删除此子任务？" okText="删除" cancelText="取消" okButtonProps={{ danger: true }} onConfirm={() => deleteTask(t.id)}>
                            <Button size="small" type="text" danger><DeleteOutlined /></Button>
                          </Popconfirm>
                        </Space>
                      </div>
                    ),
                    children: (
                      <div style={{ padding: '16px 24px' }}>
                        {t.guide && (
                          <div style={{ marginBottom: 4 }}>
                            <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 12, borderLeft: '3px solid var(--accent-color)', paddingLeft: 8 }}>操作步骤</div>
                            <Steps direction="vertical" size="small" items={(Array.isArray(t.guide) ? t.guide : [t.guide]).filter(Boolean).map((step, idx) => ({
                              title: typeof step === 'string' ? step.replace(/^\d+[\.、]\s*/, '') : (step.action || ''),
                              description: typeof step === 'string' ? undefined : (
                                <div style={{ fontSize: 12, color: '#666', lineHeight: 1.6 }}>
                                  {step.detail && <div>{step.detail}</div>}
                                  {step.target && <div style={{ marginTop: 4 }}><Tag style={{ background: '#faf8f5', border: '1px solid var(--border-color)', color: 'var(--accent-color)' }}>{step.target}</Tag></div>}
                                </div>
                              )
                            }))} />
                          </div>
                        )}
                        <Divider style={{ margin: '16px 0', borderColor: 'var(--border-color)' }} />
                        {t.resources && t.resources.length > 0 && (
                          <div style={{ marginBottom: 4 }}>
                            <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 12, borderLeft: '3px solid var(--accent-color)', paddingLeft: 8 }}>学习资源</div>
                            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                              {t.resources.map((r, j) => (
                                <a key={j} href={r.url} target="_blank" rel="noreferrer" style={{ display: 'flex', alignItems: 'flex-start', gap: 8, padding: '10px 12px', background: '#faf8f5', borderRadius: 8, textDecoration: 'none', color: 'var(--accent-color)', transition: 'all 0.2s', border: '1px solid var(--border-color)' }}
                                  onMouseEnter={e => { e.currentTarget.style.background = 'var(--border-color)'; e.currentTarget.style.boxShadow = '0 4px 12px rgba(0,0,0,0.05)'; }}
                                  onMouseLeave={e => { e.currentTarget.style.background = '#faf8f5'; e.currentTarget.style.boxShadow = 'none'; }}>
                                  {r.type === 'video' ? <PlayCircleOutlined style={{ marginTop: 2 }} /> : <FileTextOutlined style={{ marginTop: 2 }} />}
                                  <div>
                                    <div style={{ fontSize: 13 }}>{r.title}</div>
                                    {r.description && <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginTop: 2 }}>{r.description}</div>}
                                  </div>
                                </a>
                              ))}
                            </div>
                          </div>
                        )}
                        <Divider style={{ margin: '16px 0', borderColor: 'var(--border-color)' }} />
                        <div>
                          <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 12, borderLeft: '3px solid var(--accent-color)', paddingLeft: 8 }}>学习笔记</div>
                          <Input.TextArea placeholder="记录学习笔记..." autoSize={{ minRows: 3, maxRows: 6 }} value={t.notes || ''}
                            onChange={(e) => { const val = e.target.value; const tasks = (drawerSkill.tasks || []).map((x, j) => j === i ? { ...x, notes: val } : x); setDrawerSkill({ ...drawerSkill, tasks }); }}
                            style={{ borderRadius: 8, background: '#faf8f5' }} />
                          <Button size="small" type="primary" style={{ marginTop: 8, background: 'var(--accent-color)', borderColor: 'var(--accent-color)' }} onClick={async () => {
                            try {
                              await authFetch(`${import.meta.env.VITE_API_BASE}/api/skills/${drawerSkill.id}/tasks/${t.id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ notes: t.notes }) });
                              const res = await authFetch(`${import.meta.env.VITE_API_BASE}/api/skills`);
                              const list = await res.json();
                              const fresh = list.find(s => s.id === drawerSkill.id);
                              if (fresh) setDrawerSkill(fresh);
                              message.success('笔记已保存');
                            } catch (err) { message.error('保存失败'); }
                          }}>保存笔记</Button>
                        </div>
                        <div style={{ textAlign: 'center', marginTop: 16 }}>
                          <Button size="small" style={{ background: 'linear-gradient(135deg, var(--accent-color), #a68a64)', color: '#fff', border: 'none', borderRadius: 20 }} loading={aiTaskLoading === i} onClick={() => handleTaskGuide(i, t)}>
                            ✨ AI 帮教这个任务
                          </Button>
                        </div>
                      </div>
                    )
                  }))} />
                  <Input.Search placeholder="添加子任务，回车确认" enterButton="添加" style={{ marginTop: 12 }} onSearch={addSubTask} />
                </div>
              )
            },
            {
              key: '2', label: '学习资源',
              children: (
                <div>
                  <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
                    <Input placeholder="输入学习链接" value={newResource} onChange={e => setNewResource(e.target.value)} />
                    <Button type="primary" onClick={addResource} style={{ background: 'var(--accent-color)', borderColor: 'var(--accent-color)' }}>添加</Button>
                    <Button onClick={handleAIResources} loading={resourceLoading}>✨ AI 推荐</Button>
                  </div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                    {(drawerSkill.resources || []).map((r, i) => (
                      <a key={i} href={r.url} target="_blank" rel="noreferrer" style={{ display: 'flex', alignItems: 'flex-start', gap: 10, padding: '12px 14px', background: '#faf8f5', borderRadius: 8, textDecoration: 'none', border: '1px solid var(--border-color)', transition: 'all 0.2s' }}
                        onMouseEnter={e => { e.currentTarget.style.boxShadow = '0 4px 12px rgba(0,0,0,0.05)'; }}
                        onMouseLeave={e => { e.currentTarget.style.boxShadow = 'none'; }}>
                        {r.type === 'video' ? <PlayCircleOutlined style={{ marginTop: 2, color: 'var(--accent-color)' }} /> : r.url?.includes('github') ? <GithubOutlined style={{ marginTop: 2, color: 'var(--accent-color)' }} /> : <FileTextOutlined style={{ marginTop: 2, color: 'var(--accent-color)' }} />}
                        <div style={{ flex: 1 }}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                            <span style={{ color: 'var(--accent-color)', fontSize: 13, fontWeight: 500 }}>{r.title || r.url}</span>
                            <Tag style={{ background: 'var(--border-color)', border: 'none', color: '#666', fontSize: 11 }}>{r.type === 'video' ? '视频教程' : r.type === 'doc' ? '官方文档' : '教程'}</Tag>
                          </div>
                          {r.description && <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginTop: 4 }}>{r.description}</div>}
                        </div>
                      </a>
                    ))}
                  </div>
                </div>
              )
            },
            {
              key: '3', label: '实战检验',
              children: (
                <div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
                    <h4 style={{ margin: 0 }}>验收标准</h4>
                    <Button size="small" loading={verifying} onClick={async () => {
                      setVerifying(true);
                      try {
                        const res = await authFetch(`${import.meta.env.VITE_API_BASE}/api/skills/${drawerSkill.id}/generate_verification`, { method: 'POST' });
                        const data = await res.json();
                        if (data.verification) { setDrawerSkill({ ...drawerSkill, verification: data.verification }); message.success('验收标准已生成'); }
                      } catch { message.error('生成失败'); }
                      finally { setVerifying(false); }
                    }}>✨ AI 生成验收标准</Button>
                  </div>
                  <Input.TextArea value={drawerSkill.verification || ''} onChange={e => setDrawerSkill({ ...drawerSkill, verification: e.target.value })} onBlur={() => handleUpdate(drawerSkill.id, { verification: drawerSkill.verification })} rows={3} placeholder="如：能独立部署一个 Dify 应用并接入知识库" style={{ background: '#faf8f5', borderRadius: 8 }} />
                  <h4 style={{ marginTop: 24 }}>踩坑笔记</h4>
                  <Input.TextArea value={drawerSkill.notes || ''} onChange={e => setDrawerSkill({ ...drawerSkill, notes: e.target.value })} onBlur={() => handleUpdate(drawerSkill.id, { notes: drawerSkill.notes })} rows={8} placeholder="记录学习中遇到的问题和解决方案..." style={{ background: '#faf8f5', borderRadius: 8 }} />
                </div>
              )
            },
            {
              key: '4', label: '智能辅助',
              children: (
                <div>
                  <div style={{ textAlign: 'center', padding: '24px 0' }}>
                    <p style={{ color: 'var(--text-tertiary)', marginBottom: 24 }}>AI 帮你拆解学习路径、生成练习题</p>
                    <Space>
                      <Button type="primary" style={{ background: 'var(--accent-color)', borderColor: 'var(--accent-color)' }} loading={quizLoading} onClick={async () => {
                        setQuizLoading(true);
                        try {
                          const res = await authFetch(`${import.meta.env.VITE_API_BASE}/api/skills/${drawerSkill.id}/generate_quiz`, { method: 'POST' });
                          const data = await res.json();
                          if (data.quiz) setQuizData(data.quiz);
                        } catch { message.error('生成失败'); }
                        finally { setQuizLoading(false); }
                      }}>AI 出题检验</Button>
                    </Space>
                  </div>
                  {quizData && (
                    <div style={{ marginTop: 16 }}>
                      <Divider style={{ borderColor: 'var(--border-color)' }} />
                      {quizData.map((q, i) => (
                        <div key={i} style={{ marginBottom: 16, padding: 12, background: '#faf8f5', borderRadius: 8 }}>
                          <div style={{ fontWeight: 600, marginBottom: 8 }}>{i + 1}. {q.question}</div>
                          {q.options && q.options.length > 0 && (
                            <div style={{ marginLeft: 12, color: '#666', fontSize: 13 }}>
                              {q.options.map((o, j) => <div key={j}>{o}</div>)}
                            </div>
                          )}
                          <div style={{ marginTop: 8, fontSize: 13, color: 'var(--accent-color)' }}>参考答案：{q.answer}</div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )
            }
          ]} />
        )}
      </Drawer>
    </div>
  );
}

export default SkillsPage;
