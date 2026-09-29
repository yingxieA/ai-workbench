import { useEffect, useState } from 'react';
import { Spin } from 'antd';

function PdfPreview({ docId }) {
  const [loading, setLoading] = useState(true);
  const apiBase = import.meta.env.VITE_API_BASE;

  useEffect(() => {
    setLoading(true);
    // 用 iframe 内嵌 PDF，简单稳定
    const timer = setTimeout(() => setLoading(false), 1000);
    return () => clearTimeout(timer);
  }, [docId]);

  const pdfUrl = `${apiBase}/api/documents/${docId}/preview`;

  return (
    <div style={{ maxHeight: '80vh' }}>
      {loading && <Spin tip="加载中..." style={{ display: 'block', textAlign: 'center', padding: 100 }} />}
      <iframe
        src={pdfUrl}
        style={{
          width: '100%',
          height: '75vh',
          border: 'none',
          display: loading ? 'none' : 'block',
        }}
        onLoad={() => setLoading(false)}
      />
    </div>
  );
}

export default PdfPreview;
