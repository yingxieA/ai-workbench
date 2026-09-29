import React, { useState, useEffect, useRef } from 'react';
import { Input, Button, Upload, message, Modal, Pagination, Popconfirm, Segmented, Space, Tag, Tooltip, Dropdown } from 'antd';
import { EyeOutlined, DownloadOutlined, EditOutlined, DeleteOutlined, MoreOutlined, InboxOutlined, SafetyOutlined, AuditOutlined, SettingOutlined, ReloadOutlined } from '@ant-design/icons';
import DocPreviewModal from '../components/DocPreview';
import { PermissionModal, ReviewModal, KeywordModal } from '../components/DocPermissionModals';
import { authFetch } from '../utils/api';

function DocsPage({ t }) {
  const [uploading, setUploading] = useState(false);
  const [docs, setDocs] = useState([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState('');
  const [typeFilter, setTypeFilter] = useState(null);
  const [renameTarget, setRenameTarget] = useState(null);
  const [renameValue, setRenameValue] = useState('');
  const [previewDoc, setPreviewDoc] = useState(null);
  // 权限相关
  const [permDoc, setPermDoc] = useState(null);
  const [reviewDoc, setReviewDoc] = useState(null);
  const [keywordOpen, setKeywordOpen] = useState(false);
  const [relabeling, setRelabeling] = useState(null);
  const pageSize = 10;

  const visLabel = { public: ['公开', 'green'], internal: ['内部', 'blue'], restricted: ['受限', 'orange'] };

  const fetchDocs = async (p = 1, overrideType, overrideSearch) => {
    const params = new URLSearchParams({
      skip: (p - 1) * pageSize,
      limit: pageSize,
    });
    const t = overrideType !== undefined ? overrideType : typeFilter;
    if (t) params.append('type', t);
    const s = overrideSearch !== undefined ? overrideSearch : search;
    if (s) params.append('search', s);

    const res = await authFetch(`${import.meta.env.VITE_API_BASE}/api/documents/list?${params}`);
    const data = await res.json();
    setDocs(data.items);
    setTotal(data.total);
  };

  useEffect(() => { fetchDocs(1); }, []);

  const relabel = async (d) => {
    setRelabeling(d.id);
    try {
      const res = await authFetch(`${import.meta.env.VITE_API_BASE}/api/documents/${d.id}/relabel`, { method: 'POST' });
      const data = await res.json();
      if (res.ok) { message.success(`重新打标完成，处理 ${data.relabeled} 个片段`); }
      else message.error(data.detail || '重新打标失败');
    } finally { setRelabeling(null); }
  };

  const uploadCountRef = useRef(0);
  const totalUploadRef = useRef(0);
  const loadingHideRef = useRef(null);

  const handleUpload = async (file, fileList) => {
    // 第一个文件开始时，显示总的上传中提示
    if (uploadCountRef.current === 0) {
      totalUploadRef.current = fileList.length;
      setUploading(true);
      loadingHideRef.current = message.loading(`正在上传 ${totalUploadRef.current} 个文件...`, 0);
    }
    uploadCountRef.current += 1;

    const formData = new FormData();
    formData.append('file', file);
    const isPdf = file.name.toLowerCase().endsWith('.pdf');

    const finishUpload = () => {
      uploadCountRef.current -= 1;
      // 所有文件都上传完，才关闭总的提示
      if (uploadCountRef.current === 0) {
        if (loadingHideRef.current) loadingHideRef.current();
        setUploading(false);
        message.success(`全部上传完成！共处理 ${totalUploadRef.current} 个文件`);
        fetchDocs(1, newType);
      }
    };

    try {
      const url = isPdf
        ? `${import.meta.env.VITE_API_BASE}/api/documents/upload-async?category=default`
        : `${import.meta.env.VITE_API_BASE}/api/documents/upload?category=default`;
      const res = await authFetch(url, { method: 'POST', body: formData });
      const data = await res.json();

      if (isPdf && data.task_id) {
        // PDF 异步解析，轮询
        const poll = setInterval(async () => {
          const r = await authFetch(`${import.meta.env.VITE_API_BASE}/api/documents/task/${data.task_id}`);
          const t = await r.json();
          if (t.status === 'done' || t.status === 'failed') {
            clearInterval(poll);
            finishUpload();
          }
        }, 2000);
      } else {
        finishUpload();
      }
    } catch (e) {
      finishUpload();
    }
    return false;
  };

  const handleRename = async () => {
    if (!renameTarget) return;
    await authFetch(`${import.meta.env.VITE_API_BASE}/api/documents/${renameTarget.id}`, {
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
          multiple
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
              onSearch={(val) => { setPage(1); fetchDocs(1, undefined, val); }}
            />
            <Segmented
              value={typeFilter || 'all'}
              onChange={(v) => { const newType = v === 'all' ? null : v; setTypeFilter(newType);
                setPage(1);
                fetchDocs(1, newType);
              }}
              options={[
                { value: 'all', label: '全部' },
                { value: 'pdf', label: 'PDF' },
                { value: 'jupyter', label: 'Jupyter' },
                { value: 'markdown', label: 'Markdown' },
              ]}
            />
            <Button icon={<SettingOutlined />} onClick={() => setKeywordOpen(true)}>敏感词管理</Button>
          </div>
          <span style={{ fontSize: 12, color: '#9CA3AF' }}>共 {total} 个文档</span>
        </div>

        {/* 文档列表 */}
        {docs.map(d => (
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
              <div style={{ fontSize: 12, color: '#6B7280', marginTop: 4, display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                <span>{d.source}</span>
                <span>·</span>
                <span>{d.uploaded_at?.slice(0, 10)}</span>
                {(() => { const [label, color] = visLabel[d.visibility] || ['未知', 'default']; return <Tag color={color} style={{ margin: 0, fontSize: 11 }}>{label}</Tag>; })()}
                {d.min_level > 10 && <Tag style={{ margin: 0, fontSize: 11 }}>等级 {d.min_level}+</Tag>}
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
              <Tooltip title="预览">
                <Button
                  type="text"
                  size="small"
                  icon={<EyeOutlined />}
                  style={{ color: '#6B7280' }}
                  onClick={() => setPreviewDoc(d)}
                />
              </Tooltip>
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
              <Tooltip title="文档权限">
                <Button type="text" size="small" icon={<SafetyOutlined />} style={{ color: '#1677ff' }} onClick={() => setPermDoc(d)} />
              </Tooltip>
              <Tooltip title="敏感片段复核">
                <Button type="text" size="small" icon={<AuditOutlined />} style={{ color: '#722ed1' }} onClick={() => setReviewDoc(d)} />
              </Tooltip>
              <Tooltip title="重新打标">
                <Button type="text" size="small" icon={<ReloadOutlined />} loading={relabeling === d.id} style={{ color: '#13c2c2' }} onClick={() => relabel(d)} />
              </Tooltip>
              <Popconfirm title="确定删除？" okText="删除" cancelText="取消" okButtonProps={{ danger: true }} onConfirm={async () => {
                await authFetch(`${import.meta.env.VITE_API_BASE}/api/documents/${d.id}`, { method: 'DELETE' });
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
      <PermissionModal doc={permDoc} onClose={() => setPermDoc(null)} onUpdated={() => fetchDocs()} />
      <ReviewModal doc={reviewDoc} onClose={() => setReviewDoc(null)} onUpdated={() => fetchDocs()} />
      <KeywordModal open={keywordOpen} onClose={() => setKeywordOpen(false)} />
      <DocPreviewModal doc={previewDoc} onClose={() => setPreviewDoc(null)} />
    </div>
  );
}

export default DocsPage;
