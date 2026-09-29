import { Modal } from 'antd';
import MarkdownPreview from './MarkdownPreview';
import PdfPreview from './PdfPreview';
import JupyterPreview from './JupyterPreview';

function DocPreviewModal({ doc, onClose }) {
  if (!doc) return null;

  const renderContent = () => {
    const name = doc.title?.toLowerCase() || '';

    if (name.endsWith('.pdf')) {
      return <PdfPreview docId={doc.id} />;
    }
    if (name.endsWith('.md') || name.endsWith('.markdown')) {
      return <MarkdownPreview docId={doc.id} />;
    }
    if (name.endsWith('.ipynb')) {
      return <JupyterPreview docId={doc.id} />;
    }
    if (name.endsWith('.txt')) {
      return <MarkdownPreview docId={doc.id} />;
    }

    return <div style={{ padding: 40, textAlign: 'center', color: '#6B7280' }}>暂不支持该格式预览</div>;
  };

  return (
    <Modal
      title={doc.title}
      open={!!doc}
      onCancel={onClose}
      width="90vw"
      footer={null}
      style={{ top: 20 }}
      bodyStyle={{ padding: 0 }}
    >
      {renderContent()}
    </Modal>
  );
}

export default DocPreviewModal;
