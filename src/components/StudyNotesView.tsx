import { useState, useEffect, useCallback } from 'react';
import { ChevronLeft, ChevronRight, Play, ArrowLeft, BookOpen, List, X } from 'lucide-react';
import { fetchSessionTopics } from '../api';
import ReferenceRenderer from './ReferenceRenderer';

interface StudyNotesViewProps {
  sessionId: string;
  pendingTopics?: string[];
  onBack: () => void;
  onStartQuiz?: () => void; // Provided if this is a pre-quiz intercept
}

export default function StudyNotesView({ sessionId, pendingTopics, onBack, onStartQuiz }: StudyNotesViewProps) {
  const [topics, setTopics] = useState<any[]>([]);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let mounted = true;
    if (sessionId) {
      fetchSessionTopics(sessionId).then(data => {
        if (mounted) {
          setTopics(data);
          setLoading(false);
        }
      });
    } else if (pendingTopics && pendingTopics.length > 0) {
      // Mocking fetch logic for pending quiz (normally we'd hit an API with topic names to fetch notes, doing simplified version)
      const uniqueTopics = [...new Set(pendingTopics)];
      setTopics(uniqueTopics.map(t => ({ topic_name: t, display_title: t, unified_article: "<p>Note generation in progress. Start quiz.</p>" })));
      setLoading(false);
    } else {
       setLoading(false);
    }
    return () => { mounted = false; };
  }, [sessionId, pendingTopics]);

  const handleNext = useCallback(() => {
    if (currentIndex < topics.length - 1) {
      setCurrentIndex(prev => prev + 1);
    }
  }, [currentIndex, topics.length]);

  const handlePrev = useCallback(() => {
    if (currentIndex > 0) {
      setCurrentIndex(prev => prev - 1);
    }
  }, [currentIndex]);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'ArrowRight') handleNext();
      if (e.key === 'ArrowLeft') handlePrev();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [handleNext, handlePrev]);

  if (loading) {
    return <div className="p-8 text-center text-gray-400">Loading topic notes...</div>;
  }

  if (topics.length === 0) {
    return (
      <div className="p-8 max-w-4xl mx-auto">
        <button onClick={onBack} className="mb-6 flex items-center gap-2 text-gray-400 hover:text-white transition-colors">
          <ArrowLeft size={20} /> Back
        </button>
        <div className="text-center py-12 bg-gray-800/40 rounded-xl border border-gray-700 text-gray-400">
          No generated topic notes found for this session yet. They might still be processing.
        </div>
        {onStartQuiz && (
          <div className="mt-6 flex justify-center">
            <button onClick={onStartQuiz} className="px-6 py-3 bg-brand-600 hover:bg-brand-500 text-white rounded-lg flex items-center gap-2 font-medium">
              <Play size={20} /> Start Quiz Directly
            </button>
          </div>
        )}
      </div>
    );
  }

  const currentTopic = topics[currentIndex];
  const isLast = currentIndex === topics.length - 1;


  const [sidebarOpen, setSidebarOpen] = useState(false);

  return (
    <div className="flex h-screen bg-black overflow-hidden relative">
      {/* Mobile Sidebar Overlay */}
      {sidebarOpen && (
        <div className="md:hidden fixed inset-0 bg-black/80 z-20" onClick={() => setSidebarOpen(false)} />
      )}

      {/* Sidebar Component: Medical Topics */}
      <div className={`fixed md:static inset-y-0 left-0 w-72 bg-gray-900 border-r border-gray-800 z-30 transform transition-transform duration-300 flex flex-col ${sidebarOpen ? 'translate-x-0' : '-translate-x-full md:translate-x-0'}`}>
        <div className="p-4 border-b border-gray-800 flex justify-between items-center bg-gray-950">
          <div className="flex items-center gap-2 text-brand-400 font-bold">
            <BookOpen size={20} />
            Medical Topics
          </div>
          <button className="md:hidden text-gray-400 hover:text-white" onClick={() => setSidebarOpen(false)}>
            <X size={20} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-2 space-y-1">
          {topics.map((t, i) => (
            <button
              key={i}
              onClick={() => {
                setCurrentIndex(i);
                setSidebarOpen(false);
              }}
              className={`w-full text-left px-4 py-3 rounded-xl text-sm transition-colors ${i === currentIndex ? 'bg-brand-600/20 text-brand-400 font-medium' : 'text-gray-400 hover:bg-gray-800 hover:text-gray-200'}`}
            >
              <div className="truncate">{t.display_title || t.topic_name}</div>
            </button>
          ))}
        </div>

        <div className="p-4 border-t border-gray-800 bg-gray-950">
          <button onClick={onBack} className="w-full flex items-center justify-center gap-2 py-2 px-4 rounded-lg bg-gray-800 text-gray-300 hover:bg-gray-700 transition-colors text-sm">
            <ArrowLeft size={16} /> Back to Hub
          </button>
        </div>
      </div>

      {/* Main View Component */}
      <div className="flex-1 flex flex-col h-full overflow-hidden">
        {/* Header */}
        <div className="h-16 flex items-center justify-between px-4 md:px-8 border-b border-gray-800 bg-gray-950">
          <div className="flex items-center gap-3">
            <button onClick={() => setSidebarOpen(true)} className="md:hidden text-gray-400 hover:text-white">
              <List size={24} />
            </button>
            <div className="text-sm font-medium text-gray-400 hidden md:block">
              Topic {currentIndex + 1} of {topics.length}
            </div>
          </div>

          {onStartQuiz && (
            <button
              onClick={onStartQuiz}
              className="flex items-center gap-2 px-4 py-1.5 rounded-lg bg-brand-600 text-white text-sm font-medium hover:bg-brand-500 transition-colors"
            >
              <Play size={16} /> Start Quiz
            </button>
          )}
        </div>

        {/* Content Area */}
        <div className="flex-1 overflow-y-auto p-4 md:p-8">
          <div className="max-w-4xl mx-auto">
            <div className="mb-6">
              <h2 className="text-3xl font-bold text-gray-100">{currentTopic.display_title || currentTopic.topic_name}</h2>
              <span className="inline-block mt-2 px-3 py-1 bg-brand-600/20 text-brand-400 text-xs rounded-full border border-brand-500/20">
                Clinical Synthesis & Reference
              </span>
            </div>

            <div className="bg-gray-900 border border-gray-800 rounded-2xl shadow-xl overflow-hidden mb-8">
              <div className="p-6 md:p-8 prose prose-invert prose-brand max-w-none prose-headings:text-gray-200 prose-a:text-brand-400">
                {currentTopic.unified_article ? (
                  <div dangerouslySetInnerHTML={{ __html: currentTopic.unified_article }} />
                ) : (
                   <div dangerouslySetInnerHTML={{ __html: currentTopic.enhanced_explanation || "No detailed notes available." }} />
                )}
              </div>
            </div>

            {/* References Section */}
            <ReferenceRenderer references={currentTopic.top_references} correctTopics={[currentTopic.topic_name]} />

            {/* Navigation Footer */}
            <div className="mt-8 pt-6 border-t border-gray-800 flex justify-between items-center mb-8">
              <button
                onClick={handlePrev}
                disabled={currentIndex === 0}
                className="flex items-center gap-2 px-4 py-2 rounded-lg bg-gray-900 text-gray-300 disabled:opacity-30 disabled:cursor-not-allowed hover:bg-gray-800 transition-colors border border-gray-800"
              >
                <ChevronLeft size={20} /> Previous
              </button>

              <div className="text-xs text-gray-500 hidden md:block">Use Left/Right arrow keys to navigate</div>

              <button
                onClick={handleNext}
                disabled={isLast}
                className="flex items-center gap-2 px-4 py-2 rounded-lg bg-gray-900 text-gray-300 disabled:opacity-30 disabled:cursor-not-allowed hover:bg-gray-800 transition-colors border border-gray-800"
              >
                Next <ChevronRight size={20} />
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
