import React, { useState, useEffect } from 'react';
import { Input, Button, Drawer, Checkbox, Progress, Space, Tag, message } from 'antd';
import { ArrowUpOutlined, ArrowDownOutlined, PlusOutlined, CheckOutlined } from '@ant-design/icons';

function LearningPage() {
  const [paths, setPaths] = useState([]);
  const [goal, setGoal] = useState('');
  const [loading, setLoading] = useState(false);
  const [checkedNodes, setCheckedNodes] = useState({});
  const [nodeDrawer, setNodeDrawer] = useState({ open: false, node: null });
  const [skillNames, setSkillNames] = useState([]);

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
    const res = await fetch(`${import.meta.env.VITE_API_BASE}/api/learning`);
    setPaths(await res.json());
  };

  useEffect(() => {
    fetchPaths();
    fetch(`${import.meta.env.VITE_API_BASE}/api/skills`).then(r => r.json()).then(d => setSkillNames(d.map(s => s.name))).catch(() => {});
  }, []);

  const handleGenerate = async () => {
    if (!goal.trim()) return;
    setLoading(true);
    const res = await fetch(`${import.meta.env.VITE_API_BASE}/api/learning/generate?goal=${encodeURIComponent(goal)}`, { method: 'POST' });
    await res.json();
    fetchPaths();
    setLoading(false);
  };

  const handleAddToSkills = async (node) => {
    await fetch(`${import.meta.env.VITE_API_BASE}/api/skills`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: node.title, goal: node.desc, category: '学习路径' }) });
    message.success('已加入技能管理');
    setSkillNames([...skillNames, node.title]);
  };

  const totalNodes = paths.flatMap(p => (p.nodes || []).flatMap(ph => ph.nodes || [])).length;
  const doneNodes = Object.values(checkedNodes).filter(Boolean).length;
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
          <div style={{ fontSize: 16, fontWeight: 600, marginBottom: 16, color: '#111827' }}>{p.goal}</div>
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
                  {(phase.nodes || []).map((node, ni) => (
                    <div key={node.id || ni} className="task-item" style={{ 
                      padding: '12px 16px', 
                      background: '#F9FAFB', 
                      borderRadius: 6, 
                      transition: 'all 0.2s ease',
                    }}>
                      {/* 第一行：复选框 + 标题 + 操作按钮 */}
                      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flex: 1 }}>
                          <Checkbox 
                            checked={!!checkedNodes[node.id || `${pi}-${ni}`]} 
                            onChange={(e) => setCheckedNodes({ ...checkedNodes, [node.id || `${pi}-${ni}`]: e.target.checked })} 
                          />
                          <a 
                            onClick={() => setNodeDrawer({ open: true, node })} 
                            style={{ color: '#111827', fontSize: 14, fontWeight: 500, cursor: 'pointer' }}
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
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      ))}

      <Drawer open={nodeDrawer.open} title={nodeDrawer.node?.title} width={480} onClose={() => setNodeDrawer({ open: false, node: null })}>
        {nodeDrawer.node && (
          <div>
            <h4>任务描述</h4>
            <p style={{ color: '#4B5563', lineHeight: 1.6 }}>{nodeDrawer.node.desc || '暂无描述'}</p>
            <h4 style={{ marginTop: 24 }}>学习资源</h4>
            {(nodeDrawer.node.resources || []).map((r, i) => (
              <a key={i} href={r} target="_blank" rel="noreferrer" style={{ display: 'block', color: '#4D6BFE', marginBottom: 8 }}>{r}</a>
            ))}
          </div>
        )}
      </Drawer>
    </div>
  );
}

export default LearningPage;
