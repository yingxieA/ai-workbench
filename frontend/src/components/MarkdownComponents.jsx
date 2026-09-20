import React from 'react';
import { Button, message } from 'antd';
import { CopyOutlined } from '@ant-design/icons';

export const MarkdownComponents = {
  code({ node, inline, className, children, ...props }) {
    const match = /language-(\w+)/.exec(className || '');
    let codeString = Array.isArray(children) ? children.join('') : String(children);
    codeString = codeString.replace(/\n$/, '');

    if (codeString.includes('|-----|') || codeString.includes('|---|')) {
      return <code className={className} {...props}>{children}</code>;
    }

    if (!inline && match) {
      const lang = match[1];
      return (
        <div style={{ margin: '16px 0', borderRadius: 12, overflow: 'hidden', border: '1px solid #3e4451', background: '#282c34' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '8px 16px', background: '#21252b', color: '#abb2bf', fontSize: 12, borderBottom: '1px solid #3e4451' }}>
            <span style={{ textTransform: 'lowercase', fontWeight: 600 }}>{lang}</span>
            <Button type="text" size="small" icon={<CopyOutlined />} style={{ color: '#abb2bf' }} onClick={() => { navigator.clipboard.writeText(codeString); message.success('已复制'); }} />
          </div>
          <pre style={{ margin: 0, padding: 16, color: '#abb2bf', fontSize: 13, lineHeight: 1.6, overflowX: 'auto', fontFamily: 'Consolas, Monaco, monospace' }}>
            {codeString}
          </pre>
        </div>
      );
    }
    return <code className={className} {...props}>{children}</code>;
  },
  ol: ({node, ...props}) => <ol {...props} />,
  ul: ({node, ...props}) => <ul {...props} />,
  li: ({node, ...props}) => <li {...props} />,
  p: ({node, ...props}) => <p {...props} />
};
