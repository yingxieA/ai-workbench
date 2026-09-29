import React, { useState, useEffect } from 'react';
import { Input, Button, Drawer, Checkbox, Progress, Space, Tag, message, Popconfirm, Modal } from 'antd';
import { ArrowUpOutlined, ArrowDownOutlined, PlusOutlined, CheckOutlined, DeleteOutlined, FileTextOutlined, PlayCircleOutlined, GithubOutlined, BulbOutlined, RightOutlined } from '@ant-design/icons';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeRaw from 'rehype-raw';
import { MarkdownComponents } from '../components/MarkdownComponents';
import { authFetch } from '../utils/api';

function LearningPage() {
  const [paths, setPaths] = useState([]);
  const [goal, setGoal] = useState('');
  const [loading, setLoading] = useState(false);
  const [nodeDrawer, setNodeDrawer] = useState({ open: false, node: null });
  const [skillNames, setSkillNames] = useState([]);
  const [aiTutor, setAiTutor] = useState({ open: false, loading: false, content: '' });

  const moveNode = (pathIdx, phaseIdx, nodeIdx, dir) => {
    const newPaths = [...paths];
    const phase = newPaths[pathIdx].nodes[phaseIdx];
    const nodes = [...phase.nodes];
    const j = nodeIdx + dir;
    [nodes[nodeIdx], nodes[j]] = [nodes[j], nodes[nodeIdx]];
    phase.nodes = nodes;
    newPaths[pathIdx].nodes[phaseIdx] = phase;
    setPaths(newPaths);
  };

  const fetchPaths = async () => {
    const res = await authFetch(`${import.meta.env.VITE_API_BASE}/api/learning`);
    const data = await res.json();
    setPaths(data);
  };

  useEffect(() => {
    fetchPaths();
    authFetch(`${import.meta.env.VITE_API_BASE}/api/skills`).then(r => r.json()).then(d => setSkillNames(d.map(s => s.name))).catch(() => {});
  }, []);

  const handleGenerate = async () => {
    if (!goal.trim()) return;
    setLoading(true);
    const res = await authFetch(`${import.meta.env.VITE_API_BASE}/api/learning/generate?goal=${encodeURIComponent(goal)}`, { method: 'POST' });
    await res.json();
    fetchPaths();
    setLoading(false);
  };

  const handleAddToSkills = async (node) => {
    await authFetch(`${import.meta.env.VITE_API_BASE}/api/skills`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: node.title, goal: node.desc, category: '学习路径' }) });
    message.success('已加入技能管理');
    setSkillNames([...skillNames, node.title]);
  };

  // 切换节点完成状态（持久化到后端）
  const handleToggleNode = async (pathId, nodeId, completed) => {
    // 先本地更新（乐观更新）
    setPaths(paths.map(p => {
      if (p.id !== pathId) return p;
      const completedNodes = p.completed_nodes || [];
      const newCompleted = completed
        ? [...completedNodes, nodeId]
        : completedNodes.filter(id => id !== nodeId);
      return { ...p, completed_nodes: newCompleted };
    }));

    // 调用后端 API
    try {
      await authFetch(`${import.meta.env.VITE_API_BASE}/api/learning/${pathId}/node`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ node_id: nodeId, completed })
      });
    } catch (e) {
      message.error('更新失败');
      fetchPaths(); // 刷新回退
    }
  };

  // 删除整个学习路径
  const handleDeletePath = async (pathId) => {
    await authFetch(`${import.meta.env.VITE_API_BASE}/api/learning/${pathId}`, { method: 'DELETE' });
    message.success('已删除');
    fetchPaths();
  };

  // AI 帮教
  const handleAiTutor = async (node) => {
    setAiTutor({ open: true, loading: true, content: '' });

    try {
      const res = await authFetch(`${import.meta.env.VITE_API_BASE}/api/learning/ai-tutor`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          node_title: node.title,
          node_desc: node.desc || ''
        })
      });

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let content = '';
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });

        // SSE 事件之间用 \n\n 分隔
        const events = buffer.split('\n\n');
        buffer = events.pop() || ''; // 最后一个事件可能不完整，留到下次

        for (const event of events) {
          const lines = event.split('\n');
          for (const line of lines) {
            if (line.startsWith('data: ')) {
              content += line.slice(6);
            }
          }
          setAiTutor(prev => ({ ...prev, content }));
        }
      }
    } catch (e) {
      message.error('AI 帮教失败，请重试');
    } finally {
      setAiTutor(prev => ({ ...prev, loading: false }));
    }
  };

  // 计算总节点数和已完成节点数（从后端数据）
  const totalNodes = paths.reduce((sum, p) =>
    sum + (p.nodes || []).reduce((s, ph) => s + (ph.nodes || []).length, 0), 0
  );
  const doneNodes = paths.reduce((sum, p) => sum + (p.completed_nodes || []).length, 0);
  const percent = totalNodes > 0 ? Math.round(doneNodes / totalNodes * 100) : 0;

  return (
    <div style={{ paddingTop: 32 }}>
      <div style={{ textAlign: 'center', marginBottom: 24 }}>
        <h1 className="page-title">学习路径</h1>
        <p className="page-subtitle">分阶段的能力成长阶梯</p>
      </div>

      {/* 顶部输入区：居中紧凑 */}
      <div style={{ display: 'flex', gap: 8, marginBottom: 24, maxWidth: 600, margin: '0 auto 24px' }}>
        <Input
          placeholder="输入目标，如：3个月成为AI Agent开发"
          value={goal}
          onChange={e => setGoal(e.target.value)}
          onPressEnter={handleGenerate}
        />
        <Button type="primary" onClick={handleGenerate} loading={loading}>生成</Button>
      </div>

      {/* 整体进度条：横向细长 */}
      {paths.length > 0 && (
        <div style={{
          maxWidth: 800,
          margin: '0 auto 24px',
          padding: '16px 20px',
          background: '#F9FAFB',
          borderRadius: 8,
          border: '1px solid #E5E7EB',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 8 }}>
            <div style={{ fontWeight: 600, fontSize: 14, color: '#111827' }}>整体学习进度</div>
            <div style={{ fontSize: 12, color: '#6B7280' }}>{doneNodes} / {totalNodes} 个节点已完成</div>
            <div style={{ marginLeft: 'auto', fontWeight: 600, fontSize: 14, color: '#4D6BFE' }}>{percent}%</div>
          </div>
          <Progress
            percent={percent}
            strokeColor="#4D6BFE"
            trailColor="#E5E7EB"
            showInfo={false}
            size="small"
            style={{ marginBottom: 0 }}
          />
        </div>
      )}

      {/* 阶段卡片 */}
      {paths.map(p => (
        <div key={p.id} style={{ marginBottom: 24, maxWidth: 800, margin: '0 auto 24px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
            <div style={{ fontSize: 16, fontWeight: 600, color: '#111827' }}>{p.goal}</div>
            <Popconfirm title="确定删除这个学习路径？" onConfirm={() => handleDeletePath(p.id)}>
              <Button size="small" type="text" icon={<DeleteOutlined style={{ color: '#EF4444' }} />} />
            </Popconfirm>
          </div>
          {p.nodes.map((rawPhase, pi) => {
            let phase = rawPhase;
            if (typeof rawPhase === 'string') { try { phase = JSON.parse(rawPhase); } catch { return <div key={pi} style={{ padding: 12, color: '#9CA3AF', fontSize: 13 }}>{rawPhase}</div>; } }
            return (
              <div key={pi} style={{
                marginBottom: 16,
                padding: 20,
                background: '#fff',
                borderRadius: 8,
                border: '1px solid #E5E7EB',
                borderLeft: '4px solid #4D6BFE',
              }}>
                {/* 阶段标题 */}
                <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 }}>
                  <div style={{
                    width: 28,
                    height: 28,
                    borderRadius: 6,
                    background: '#EEF2FF',
                    color: '#4D6BFE',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    fontSize: 13,
                    fontWeight: 600
                  }}>
                    {pi + 1}
                  </div>
                  <div>
                    <div style={{ fontWeight: 600, fontSize: 15, color: '#111827' }}>{phase.phase}</div>
                    {phase.goal && <div style={{ fontSize: 12, color: '#6B7280', marginTop: 2 }}>{phase.goal}</div>}
                  </div>
                </div>

                {/* 子任务列表：两行布局 */}
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  {(phase.nodes || []).map((node, ni) => {
                    const nodeId = node.id || `${pi}-${ni}`;
                    const isChecked = (p.completed_nodes || []).includes(nodeId);
                    return (
                      <div key={nodeId} className="task-item" style={{
                        padding: '12px 16px',
                        background: '#F9FAFB',
                        borderRadius: 6,
                        transition: 'all 0.2s ease',
                      }}>
                        {/* 第一行：复选框 + 标题 + 操作按钮 */}
                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 10, flex: 1 }}>
                            <Checkbox
                              checked={isChecked}
                              onChange={(e) => handleToggleNode(p.id, nodeId, e.target.checked)}
                            />
                            <a
                              onClick={() => setNodeDrawer({ open: true, node })}
                              style={{
                                color: isChecked ? '#9CA3AF' : '#111827',
                                fontSize: 14,
                                fontWeight: 500,
                                cursor: 'pointer',
                                textDecoration: isChecked ? 'line-through' : 'none',
                              }}
                            >
                              {node.title}
                            </a>
                          </div>
                          {/* 操作按钮：悬浮显示，带文字说明 */}
                          <Space size={4} className="task-actions" style={{ opacity: 0, transition: 'opacity 0.2s' }}>
                            <Button
                              size="small"
                              type="text"
                              disabled={ni === 0}
                              icon={<ArrowUpOutlined />}
                              onClick={() => moveNode(0, pi, ni, -1)}
                            />
                            <Button
                              size="small"
                              type="text"
                              disabled={ni === (phase.nodes || []).length - 1}
                              icon={<ArrowDownOutlined />}
                              onClick={() => moveNode(0, pi, ni, 1)}
                            />
                            {skillNames.includes(node.title)
                              ? <Button
                                  size="small"
                                  type="primary"
                                  icon={<CheckOutlined />}
                                  style={{ background: '#10B981', borderColor: '#10B981' }}
                                >
                                  已加入
                                </Button>
                              : <Button
                                  size="small"
                                  type="primary"
                                  icon={<PlusOutlined />}
                                  onClick={() => handleAddToSkills(node)}
                                >
                                  加入技能
                                </Button>
                            }
                          </Space>
                        </div>
                        {/* 第二行：描述 */}
                        {node.desc && (
                          <div style={{
                            fontSize: 12,
                            color: '#6B7280',
                            marginTop: 6,
                            marginLeft: 28,
                            lineHeight: 1.5,
                          }}>
                            {node.desc}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
            );
          })}
        </div>
      ))}

      <Drawer
        open={nodeDrawer.open}
        title={nodeDrawer.node?.title}
        width={560}
        onClose={() => setNodeDrawer({ open: false, node: null })}
      >
        {nodeDrawer.node && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>

            {/* 1. 任务描述卡片化 */}
            <div>
              <h4 style={{ fontSize: 14, fontWeight: 600, color: '#111827', marginBottom: 12 }}>任务描述</h4>
              <div style={{
                padding: '12px 16px',
                background: '#F9FAFB',
                borderRadius: 8,
                fontSize: 13,
                color: '#4B5563',
                lineHeight: 1.6,
              }}>
                {nodeDrawer.node.desc || '暂无描述'}
              </div>
            </div>

            {/* 2. 学习资源卡片化 */}
            <div>
              <h4 style={{ fontSize: 14, fontWeight: 600, color: '#111827', marginBottom: 12 }}>学习资源</h4>
              {(nodeDrawer.node.resources || []).length === 0 ? (
                <div style={{ padding: 20, textAlign: 'center', color: '#9CA3AF', fontSize: 13 }}>暂无学习资源</div>
              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  {(nodeDrawer.node.resources || []).map((r, i) => {
                    // 判断资源类型
                    let Icon = FileTextOutlined;
                    let title = '学习文档';
                    if (r.includes('github.com')) {
                      Icon = GithubOutlined;
                      title = 'GitHub 仓库';
                    } else if (r.includes('youtube') || r.includes('bilibili') || r.includes('video')) {
                      Icon = PlayCircleOutlined;
                      title = '视频教程';
                    }

                    return (
                      <a
                        key={i}
                        href={r}
                        target="_blank"
                        rel="noreferrer"
                        className="resource-card"
                        style={{
                          display: 'flex',
                          alignItems: 'flex-start',
                          gap: 12,
                          padding: '10px 14px',
                          background: '#F9FAFB',
                          borderRadius: 8,
                          border: '1px solid #E5E7EB',
                          textDecoration: 'none',
                          transition: 'all 0.2s ease',
                        }}
                      >
                        <Icon style={{ fontSize: 18, color: '#4D6BFE', marginTop: 2 }} />
                        <div style={{ flex: 1 }}>
                          <div style={{ fontSize: 13, fontWeight: 600, color: '#111827' }}>{title}</div>
                          <div style={{ fontSize: 12, color: '#6B7280', marginTop: 2, wordBreak: 'break-all' }}>
                            {r.length > 50 ? r.slice(0, 50) + '...' : r}
                          </div>
                        </div>
                        <RightOutlined style={{ color: '#9CA3AF', marginTop: 4 }} />
                      </a>
                    );
                  })}
                </div>
              )}
            </div>

            {/* 3. 学习笔记 */}
            <div>
              <h4 style={{ fontSize: 14, fontWeight: 600, color: '#111827', marginBottom: 12 }}>学习笔记</h4>
              <Input.TextArea
                placeholder="记录你的学习心得和要点..."
                autoSize={{ minRows: 3, maxRows: 6 }}
                style={{
                  background: '#F9FAFB',
                  borderRadius: 8,
                  border: '1px solid #E5E7EB',
                }}
              />
            </div>

            {/* 4. 底部操作栏 */}
            <div style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              paddingTop: 16,
              borderTop: '1px solid #E5E7EB',
            }}>
              <Button
                icon={<BulbOutlined />}
                style={{
                  background: '#EEF2FF',
                  border: 'none',
                  color: '#4D6BFE',
                }}
                onClick={() => handleAiTutor(nodeDrawer.node)}
              >
                AI 帮教这个任务
              </Button>
              <Button
                type="primary"
                icon={<CheckOutlined />}
                onClick={() => {
                  const nodeId = nodeDrawer.node.id;
                  if (nodeId) {
                    // 找到对应的 path 和 node
                    const path = paths.find(p =>
                      (p.nodes || []).some(ph =>
                        (ph.nodes || []).some(n => n.id === nodeId)
                      )
                    );
                    if (path) {
                      handleToggleNode(path.id, nodeId, true);
                    }
                  }
                  setNodeDrawer({ open: false, node: null });
                }}
              >
                标记为已完成
              </Button>
            </div>

          </div>
        )}
      </Drawer>

      {/* AI 帮教弹窗 */}
      <Modal
        title={
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <BulbOutlined style={{ color: '#4D6BFE' }} />
            <span>AI 帮教</span>
          </div>
        }
        open={aiTutor.open}
        onCancel={() => setAiTutor({ open: false, loading: false, content: '' })}
        footer={null}
        width={640}
      >
        <div style={{
          minHeight: 300,
          background: '#F9FAFB',
          borderRadius: 8,
          overflow: 'hidden',
        }}>
          {aiTutor.loading && !aiTutor.content ? (
            <div style={{ padding: 20, color: '#9CA3AF' }}>AI 正在思考中...</div>
          ) : (
            <div className="markdown-body" style={{
              padding: '16px 20px',
              fontSize: 14,
              lineHeight: 1.6,
              wordBreak: 'break-word',
              overflowWrap: 'break-word',
            }}>
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                rehypePlugins={[rehypeRaw]}
                components={MarkdownComponents}
              >
                {aiTutor.content}
              </ReactMarkdown>
            </div>
          )}
          {aiTutor.loading && <span className="typing-cursor" style={{ marginLeft: 20 }}>▋</span>}
        </div>
      </Modal>
    </div>
  );
}

export default LearningPage;
