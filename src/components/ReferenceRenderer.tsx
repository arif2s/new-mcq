import { useState } from 'react';
import { BookOpen, FileText, Database, ExternalLink, X } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import rehypeRaw from 'rehype-raw';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import remarkGfm from 'remark-gfm';
import 'katex/dist/katex.min.css';

interface ReferenceRendererProps {
  references: any;
  correctTopics?: string[]; // Topics that match the correct answer
}

export default function ReferenceRenderer({ references, correctTopics }: ReferenceRendererProps) {
  const [activeRef, setActiveRef] = useState<any>(null);

  if (!references) return null;

  let refsObj = references;
  if (typeof references === 'string') {
    try {
      refsObj = JSON.parse(references);
    } catch (e) {
      return null;
    }
  }

  if (Object.keys(refsObj).length === 0) return null;

  // Flatten and categorize
  const allRefs: any[] = [];
  if (refsObj.pdf) allRefs.push(...refsObj.pdf.map((r: any) => ({ ...r, type: 'pdf' })));
  if (refsObj.obsidian) allRefs.push(...refsObj.obsidian.map((r: any) => ({ ...r, type: 'obsidian' })));
  if (refsObj.zim) allRefs.push(...refsObj.zim.map((r: any) => ({ ...r, type: 'zim' })));

  if (allRefs.length === 0) return null;

  // Sort logic: if correctTopics are provided, put matching titles at the top
  if (correctTopics && correctTopics.length > 0) {
    allRefs.sort((a, b) => {
      const aMatch = correctTopics.some(t => a.title?.toLowerCase().includes(t.toLowerCase()));
      const bMatch = correctTopics.some(t => b.title?.toLowerCase().includes(t.toLowerCase()));
      if (aMatch && !bMatch) return -1;
      if (!aMatch && bMatch) return 1;
      return 0;
    });
  }

  const handleOpenRef = async (ref: any) => {
    if (ref.type === 'pdf') {
      // Execute system shell command
      try {
        await fetch('/api/sources/open_pdf', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ file_path: ref.file_path, page: ref.page }),
        });
      } catch (e) {
        console.error('Failed to open PDF', e);
      }
    } else {
      // Open in internal viewer
      setActiveRef(ref);
    }
  };

  return (
    <div className="mt-4 border-t border-gray-700/50 pt-4">
      <h4 className="text-sm font-semibold text-gray-300 mb-3 flex items-center gap-2">
        <BookOpen size={16} className="text-brand-400" />
        Related Knowledge
      </h4>
      <div className="flex flex-wrap gap-2">
        {allRefs.map((ref, idx) => {
          let Icon = FileText;
          let colorClass = 'text-blue-400 bg-blue-400/10 border-blue-400/20 hover:bg-blue-400/20';
          if (ref.type === 'pdf') {
            Icon = BookOpen;
            colorClass = 'text-red-400 bg-red-400/10 border-red-400/20 hover:bg-red-400/20';
          } else if (ref.type === 'obsidian') {
            Icon = FileText;
            colorClass = 'text-purple-400 bg-purple-400/10 border-purple-400/20 hover:bg-purple-400/20';
          } else if (ref.type === 'zim') {
            Icon = Database;
            colorClass = 'text-green-400 bg-green-400/10 border-green-400/20 hover:bg-green-400/20';
          }

          const isHighlight = correctTopics && correctTopics.some(t => ref.title?.toLowerCase().includes(t.toLowerCase()));

          return (
            <button
              key={idx}
              onClick={() => handleOpenRef(ref)}
              className={`flex items-center gap-2 px-3 py-1.5 rounded-lg border text-xs transition-colors ${colorClass} ${isHighlight ? 'ring-1 ring-brand-500' : ''}`}
            >
              <Icon size={14} />
              <span className="truncate max-w-[200px]">{ref.title || 'Document'}</span>
              {ref.type === 'pdf' && ref.page && <span className="opacity-75">(Pg {ref.page})</span>}
              {ref.type === 'pdf' ? <ExternalLink size={12} className="ml-1 opacity-50" /> : null}
            </button>
          );
        })}
      </div>

      {activeRef && (
        <div className="fixed inset-0 bg-black/80 z-50 flex items-center justify-center p-4 md:p-8 animate-fade-in">
          <div className="bg-gray-900 border border-gray-700 rounded-2xl w-full max-w-4xl max-h-full flex flex-col shadow-2xl">
            <div className="flex justify-between items-center p-4 border-b border-gray-700">
              <h3 className="text-lg font-bold text-gray-200 flex items-center gap-2">
                {activeRef.type === 'obsidian' ? <FileText className="text-purple-400" /> : <Database className="text-green-400" />}
                {activeRef.title}
              </h3>
              <button
                onClick={() => setActiveRef(null)}
                className="p-1.5 bg-gray-800 text-gray-400 hover:text-white rounded-lg transition-colors"
              >
                <X size={20} />
              </button>
            </div>
            <div className="p-6 overflow-y-auto flex-1 prose prose-invert prose-brand max-w-none">
              <ReactMarkdown
                rehypePlugins={[rehypeRaw, rehypeKatex]}
                remarkPlugins={[remarkMath, remarkGfm]}
              >
                {activeRef.content || "Content loaded by backend..."}
              </ReactMarkdown>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
