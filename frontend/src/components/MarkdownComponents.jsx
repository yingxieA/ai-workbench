import React from 'react';
import { Button, message } from 'antd';
import { CopyOutlined, DownloadOutlined } from '@ant-design/icons';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { oneDark } from 'react-syntax-highlighter/dist/esm/styles/prism';

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
      const doCopy = () => { navigator.clipboard.writeText(codeString); message.success('已复制'); };
      const doDownload = () => {
        const blob = new Blob([codeString], { type: 'text/plain;charset=utf-8' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `code.${lang || 'txt'}`;
        a.click();
        URL.revokeObjectURL(url);
        message.success('已下载');
      };
      return (
        <div style={{ margin: '16px 0', borderRadius: 12, overflow: 'hidden', border: '1px solid #3e4451', background: '#282c34' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '8px 16px', background: '#21252b', color: '#abb2bf', fontSize: 12, borderBottom: '1px solid #3e4451' }}>
            <span style={{ textTransform: 'lowercase', fontWeight: 600 }}>{lang}</span>
            <div style={{ display: 'flex', gap: 2 }}>
              <Button type="text" size="small" icon={<CopyOutlined />} style={{ color: '#abb2bf' }} onClick={doCopy} title="复制代码" />
              <Button type="text" size="small" icon={<DownloadOutlined />} style={{ color: '#abb2bf' }} onClick={doDownload} title="下载代码" />
            </div>
          </div>
          <SyntaxHighlighter
            language={lang}
            style={oneDark}
            customStyle={{ margin: 0, padding: 16, fontSize: 13, lineHeight: 1.6, background: '#282c34' }}
            PreTag="div"
            showLineNumbers
          >
            {codeString}
          </SyntaxHighlighter>
        </div>
      );
    }
    return <code className={className} {...props}>{children}</code>;
  },
  ol: ({node, ...props}) => <ol {...props} />,
  ul: ({node, ...props}) => <ul {...props} />,
  li: ({node, ...props}) => <li {...props} />,
  p: ({node, ...props}) => <p {...props} />,
  a: ({node, children, href, ...props}) => {
    // 外部链接一律新标签页打开（防止当前页跳转导致问答界面丢失）；内部锚点/协议链接保持默认
    const isExternal = /^https?:\/\//i.test(href || '');
    return isExternal
      ? <a href={href} target="_blank" rel="noopener noreferrer" {...props}>{children}</a>
      : <a href={href} {...props}>{children}</a>;
  }
};
