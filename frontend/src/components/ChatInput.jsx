import React, { useState } from 'react';
import { Input, Button } from 'antd';
import { ArrowUpOutlined, StopOutlined } from '@ant-design/icons';

const { TextArea } = Input;

const ChatInput = React.memo(({ onSend, loading, canSend, onStop, placeholder }) => {
  const [input, setInput] = useState('');
  return (
    <div className="custom-chat-input" style={{
      display: 'flex',
      flexDirection: 'column',
      padding: '12px 16px',
      background: 'var(--bg-primary)',
      borderRadius: 24,
      marginBottom: 0,
      border: '1px solid var(--border-color)',
      boxShadow: 'var(--input-shadow)',
    }}>
      <TextArea
        value={input}
        onChange={e => setInput(e.target.value)}
        placeholder={placeholder}
        autoSize={{ minRows: 1, maxRows: 6 }}
        onPressEnter={e => { if (!e.shiftKey) { e.preventDefault(); if (canSend) { onSend(input); setInput(''); } } }}
        variant="borderless"
        style={{ flex: 1, background: 'transparent', resize: 'none', padding: '4px 0', fontSize: 14, boxShadow: 'none', border: 'none', outline: 'none' }}
      />
      <div style={{ display: 'flex', justifyContent: 'flex-end', alignItems: 'center', marginTop: 8 }}>
        {canSend ? (
          <Button type="primary" shape="circle" size="middle" icon={<ArrowUpOutlined />} onClick={() => { onSend(input); setInput(''); }} style={{ flexShrink: 0, background: 'var(--accent-color)', borderColor: 'var(--accent-color)', color: '#fff' }} />
        ) : loading ? (
          <Button shape="circle" size="middle" onClick={onStop} style={{ flexShrink: 0, background: 'var(--accent-color)', borderColor: 'var(--accent-color)', display: 'flex', alignItems: 'center', justifyContent: 'center' }} icon={<div style={{ width: 10, height: 10, borderRadius: 2, background: '#fff' }} />} />
        ) : null}
      </div>
    </div>
  );
});

export default ChatInput;
