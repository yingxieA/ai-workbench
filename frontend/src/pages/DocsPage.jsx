import React, { useState, useEffect } from 'react';
import { Input, Button, Upload, message, Modal, Pagination, Popconfirm, Segmented, Space, Tag, Tooltip, Dropdown } from 'antd';
import { EyeOutlined, DownloadOutlined, EditOutlined, DeleteOutlined, MoreOutlined, InboxOutlined } from '@ant-design/icons';

function DocsPage({ t }) {
  const [uploading, setUploading] = useState(false);
  const [docs, setDocs] = useState([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState('');
  const [typeFilter, setTypeFilter] = useState(null);
  const [renameTarget, setRenameTarget] = useState(null);
  const [renameValue, setRenameValue] = useState('');
  const pageSize = 10;

  const fetchDocs = async (p = 1) => {
    const res = await fetch(`${import.meta.env.VITE_API_BASE}/api/documents/list?skip=${(p-1)*pageSize}&limit=${pageSize}`);
    const data = await res.json();
    setDocs(data.items); setTotal(data.total);
  };

  useEffect(() => { fetchDocs(1); }, []);

  const handleUpload = async (file) => {
    setUploading(true);
    const hide = message.loading('上传中...', 0);
    const formData = new FormData();
    formData.append('file', file);
    const isPdf = file.name.toLowerCase().endsWith('.pdf');

    try {
      const url = isPdf
        ? `${import.meta.env.VITE_API_BASE}/api/documents/upload-async?category=default`
        : `${import.meta.env.VITE_API_BASE}/api/documents/upload?category=default`;
      const res = await fetch(url, { method: 'POST', body: formData });
      const data = await res.json();

      if (isPdf && data.task_id) {
        const poll = setInterval(async () => {
          const r = await fetch(`${import.meta.env.VITE_API_BASE}/api/documents/task/${data.task_id}`);
          const t = await r.json();
          if (t.status === 'done') { clearInterval(poll); hide(); message.success(`解析完成，共 ${t.chunks} 块`); fetchDocs(1); setUploading(false); }
          else if (t.status === 'failed') { clearInterval(poll); hide(); message.error('解析失败：' + (t.error || '')); setUploading(false); }
        }, 2000);
      } else {
        hide();
        if (data.status === 'ok') message.success(`上传成功，共 ${data.chunks} 块`);
        else if (data.status === 'exists') message.info('文件已存在');
        else message.error(data.detail || '上传失败');
        fetchDocs(1); setUploading(false);
      }
    } catch (e) {
      hide(); message.error('上传失败：' + e.message); setUploading(false);
    }
    return false;
  };

  const handleRename = async () => {
    if (!renameTarget) return;
    await fetch(`${import.meta.env.VITE_API_BASE}/api/documents/${renameTarget.id}`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title: renameValue })
    });
    setDocs(docs.map(x => x.id === renameTarget.id ? { ...x, title: renameValue } : x));
    setRenameTarget(null);
  };

  return (
    <div style={{ paddingTop: 32 }}>
      <div style={{ textAlign: 'center', marginBottom: 24 }}>
        <h1 className="page-title">{t('docs.title')}</h1>
        <p className="page-subtitle">{t('docs.subtitle')}</p>
      </div>
      {/* 上传区域：紧凑瘦身 */}
      <div className="card" style={{ marginBottom: 16, padding: 0 }}>
        <Upload.Dragger 
          accept=".md,.txt,.ipynb,.pdf" 
          beforeUpload={handleUpload} 
          showUploadList={false}
          style={{ padding: '20px 0', background: 'transparent', border: 'none' }}
        >
          <p style={{ margin: 0, fontSize: 14, color: '#6B7280' }}>
            <InboxOutlined style={{ marginRight: 8, fontSize: 18, color: '#9CA3AF' }} />
            点击或拖拽文件到这里（支持 .md / .txt / .ipynb / .pdf）
          </p>
        </Upload.Dragger>
      </div>
      {/* 工具栏与筛选区 */}
      <div className="card" style={{ padding: 0 }}>
        <div style={{ 
          display: 'flex', 
          justifyContent: 'space-between', 
          alignItems: 'center', 
          padding: '12px 16px', 
          borderBottom: '1px solid #E5E7EB' 
        }}>
          <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
            <Input.Search 
              placeholder="搜索文档名..." 
              style={{ width: 240 }} 
              onChange={e => setSearch(e.target.value)} 
            />
            <Segmented 
              value={typeFilter || 'all'} 
              onChange={(v) => setTypeFilter(v === 'all' ? null : v)} 
              options={[
                { value: 'all', label: '全部' }, 
                { value: 'pdf', label: 'PDF' }, 
                { value: 'jupyter', label: 'Jupyter' }, 
                { value: 'markdown', label: 'Markdown' },
              ]} 
            />
          </div>
          <span style={{ fontSize: 12, color: '#9CA3AF' }}>共 {total} 个文档</span>
        </div>

        {/* 文档列表 */}
        {docs.filter(d => d.title.includes(search) && (!typeFilter || d.source === typeFilter)).map(d => (
          <div key={d.id} className="doc-item" style={{ 
            display: 'flex', 
            justifyContent: 'space-between', 
            alignItems: 'center', 
            padding: '10px 16px', 
            borderBottom: '1px solid #F3F4F6',
            transition: 'background 0.2s',
          }}>
            {/* 左侧：文件名 + 元数据 */}
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 14, fontWeight: 600, color: '#111827' }}>{d.title}</div>
              <div style={{ fontSize: 12, color: '#6B7280', marginTop: 4, display: 'flex', alignItems: 'center', gap: 8 }}>
                <span>{d.source}</span>
                <span>·</span>
                <span>{d.uploaded_at?.slice(0, 10)}</span>
                {d.chunk_count > 0 && (
                  <Tag style={{ 
                    background: '#F3F4F6', 
                    border: 'none', 
                    color: '#6B7280', 
                    margin: 0,
                    fontSize: 11,
                    lineHeight: '18px',
                  }}>
                    {d.chunk_count} 个知识块
                  </Tag>
                )}
              </div>
            </div>

            {/* 右侧：操作按钮（图标，悬浮显示） */}
            <Space size={4} className="doc-actions" style={{ opacity: 0, transition: 'opacity 0.2s' }}>
              {d.title?.endsWith('.pdf') && (
                <Tooltip title="预览">
                  <Button 
                    type="text" 
                    size="small" 
                    icon={<EyeOutlined />} 
                    style={{ color: '#6B7280' }}
                    onClick={() => window.open(`${import.meta.env.VITE_API_BASE}/api/documents/${d.id}/preview`, '_blank')} 
                  />
                </Tooltip>
              )}
              <Tooltip title="下载">
                <Button 
                  type="text" 
                  size="small" 
                  icon={<DownloadOutlined />} 
                  style={{ color: '#6B7280' }}
                  href={`${import.meta.env.VITE_API_BASE}/api/documents/${d.id}/download`} 
                  target="_blank"
                />
              </Tooltip>
              <Tooltip title="重命名">
                <Button 
                  type="text" 
                  size="small" 
                  icon={<EditOutlined />} 
                  style={{ color: '#6B7280' }}
                  onClick={() => { setRenameTarget(d); setRenameValue(d.title); }} 
                />
              </Tooltip>
              <Popconfirm title="确定删除？" okText="删除" cancelText="取消" okButtonProps={{ danger: true }} onConfirm={async () => {
                await fetch(`${import.meta.env.VITE_API_BASE}/api/documents/${d.id}`, { method: 'DELETE' });
                fetchDocs();
              }}>
                <Tooltip title="删除">
                  <Button 
                    type="text" 
                    size="small" 
                    icon={<DeleteOutlined />} 
                    style={{ color: '#EF4444' }}
                  />
                </Tooltip>
              </Popconfirm>
            </Space>
          </div>
        ))}

        <div style={{ marginTop: 16, textAlign: 'center', padding: 16 }}>
          <Pagination current={page} total={total} pageSize={pageSize} onChange={(p) => { setPage(p); fetchDocs(p); }} />
        </div>
      </div>
      <Modal title="重命名文档" open={!!renameTarget} onCancel={() => setRenameTarget(null)} onOk={handleRename} width={500}>
        <Input value={renameValue} onChange={e => setRenameValue(e.target.value)} onPressEnter={handleRename} autoFocus />
      </Modal>
    </div>
  );
}

export default DocsPage;
