import React, { useState } from 'react';
import { Input, Button, Switch, Tooltip } from 'antd';
import { ArrowUpOutlined, StopOutlined, GlobalOutlined, BulbOutlined } from '@ant-design/icons';

const { TextArea } = Input;

const ChatInput = React.memo(({ onSend, loading, canSend, onStop, placeholder, webSearch, setWebSearch, deepThink, setDeepThink }) => {
  const [input, setInput] = useState('');
  return (
    <div className="custom-chat-input" style={{
      display: 'flex',
      flexDirection: 'column',
      padding: '12px 16px',
      background: 'var(--bg-primary)',
      borderRadius: 24,
      marginBottom: 0,
      border: '1px solid #7aa6ff',
      boxShadow: '0 0 0 1px rgba(122,166,255,0.18), 0 2px 8px rgba(122,166,255,0.14)',
    }}>
      {/* 功能开关行：对齐 DeepSeek 的「深度思考 / 智能搜索」胶囊 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6, paddingLeft: 2 }}>
        <Tooltip title="开启后可使用联网搜索工具（天气 / 新闻 / 实时信息），关闭后不会调用 web_search">
          <span style={{
            display: 'inline-flex', alignItems: 'center', gap: 6,
            padding: '3px 10px', borderRadius: 14, fontSize: 12,
            background: webSearch ? 'rgba(22,163,74,0.10)' : 'var(--bg-secondary)',
            border: `1px solid ${webSearch ? 'rgba(22,163,74,0.35)' : 'var(--border-color)'}`,
            color: webSearch ? '#16a34a' : 'var(--text-tertiary)',
            cursor: 'pointer', userSelect: 'none',
          }} onClick={() => setWebSearch(!webSearch)}>
            <GlobalOutlined />
            联网搜索
            <Switch size="small" checked={webSearch} onChange={(v) => setWebSearch(v)} style={{ transform: 'scale(0.8)' }} />
          </span>
        </Tooltip>
        <Tooltip title="开启后复杂多步任务（规划 / 对比 / 多工具协作）进入深度推理循环，关闭后直接给出简洁答案">
          <span style={{
            display: 'inline-flex', alignItems: 'center', gap: 6,
            padding: '3px 10px', borderRadius: 14, fontSize: 12,
            background: deepThink ? 'rgba(139,92,246,0.10)' : 'var(--bg-secondary)',
            border: `1px solid ${deepThink ? 'rgba(139,92,246,0.35)' : 'var(--border-color)'}`,
            color: deepThink ? '#8b5cf6' : 'var(--text-tertiary)',
            cursor: 'pointer', userSelect: 'none',
          }} onClick={() => setDeepThink(!deepThink)}>
            <BulbOutlined />
            深度思考
            <Switch size="small" checked={deepThink} onChange={(v) => setDeepThink(v)} style={{ transform: 'scale(0.8)' }} />
          </span>
        </Tooltip>
      </div>
      <TextArea
        value={input}
        onChange={e => setInput(e.target.value)}
        placeholder={placeholder}
        autoSize={{ minRows: 1, maxRows: 6 }}
        onPressEnter={e => { if (!e.shiftKey) { e.preventDefault(); if (input.trim()) { onSend(input); setInput(''); } } }}
        variant="borderless"
        style={{ flex: 1, background: 'transparent', resize: 'none', padding: '4px 0', fontSize: 14, boxShadow: 'none', border: 'none', outline: 'none' }}
      />
      <div style={{ display: 'flex', justifyContent: 'flex-end', alignItems: 'center', marginTop: 8 }}>
        {loading ? (
          /* 思考/回答中：只展示「停止」按钮（对齐豆包：发送与停止二选一）；
             打断模式仍可用——输入新问题回车即自动停止当前回答并发新问题 */
          <Button shape="circle" size="middle" onClick={onStop} title="停止生成" style={{ flexShrink: 0, background: 'var(--accent-color)', borderColor: 'var(--accent-color)', display: 'flex', alignItems: 'center', justifyContent: 'center' }} icon={<div style={{ width: 10, height: 10, borderRadius: 2, background: '#1f1f1f' }} />} />
        ) : canSend ? (
          <Button type="primary" shape="circle" size="middle" icon={<ArrowUpOutlined />} onClick={() => { if (input.trim()) { onSend(input); setInput(''); } }} style={{ flexShrink: 0, background: 'var(--accent-color)', borderColor: 'var(--accent-color)', color: '#fff' }} />
        ) : null}
      </div>
    </div>
  );
});

export default ChatInput;
