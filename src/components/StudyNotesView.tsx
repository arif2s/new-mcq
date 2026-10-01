import { useState, useEffect, useCallback } from 'react';
import { ChevronLeft, ChevronRight, Play, ArrowLeft } from 'lucide-react';
import { fetchSessionTopics } from '../api';

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

  return (
    <div className="p-4 md:p-8 max-w-5xl mx-auto min-h-screen flex flex-col">
      <div className="flex justify-between items-center mb-6">
        <button onClick={onBack} className="flex items-center gap-2 text-gray-400 hover:text-white transition-colors">
          <ArrowLeft size={20} /> Back
        </button>
        <div className="text-sm font-medium text-gray-400">
          Topic {currentIndex + 1} of {topics.length}
        </div>
      </div>

      <div className="flex-1 bg-gray-900 border border-gray-700 rounded-2xl shadow-xl overflow-hidden flex flex-col">
        <div className="bg-gray-800 p-6 border-b border-gray-700">
          <h2 className="text-2xl font-bold text-gray-100">{currentTopic.display_title || currentTopic.topic_name}</h2>
          <span className="inline-block mt-2 px-3 py-1 bg-brand-600/20 text-brand-400 text-xs rounded-full">
            Clinical Synthesis
          </span>
        </div>

        <div className="p-6 overflow-y-auto flex-1 prose prose-invert prose-brand max-w-none prose-headings:text-gray-200 prose-a:text-brand-400">
          {currentTopic.unified_article ? (
            <div dangerouslySetInnerHTML={{ __html: currentTopic.unified_article }} />
          ) : (
             <div dangerouslySetInnerHTML={{ __html: currentTopic.enhanced_explanation || "No detailed notes available." }} />
          )}
        </div>
      </div>

      <div className="mt-8 flex justify-between items-center">
        <button
          onClick={handlePrev}
          disabled={currentIndex === 0}
          className="flex items-center gap-2 px-5 py-3 rounded-xl bg-gray-800 text-gray-300 disabled:opacity-30 disabled:cursor-not-allowed hover:bg-gray-700 transition-colors"
        >
          <ChevronLeft size={20} /> Previous Topic
        </button>

        {isLast && onStartQuiz ? (
          <button
            onClick={onStartQuiz}
            className="flex items-center gap-2 px-8 py-3 rounded-xl bg-gradient-to-r from-brand-600 to-purple-600 text-white font-bold hover:from-brand-500 hover:to-purple-500 transition-all shadow-lg hover:shadow-brand-500/25 animate-fade-in"
          >
            Start Quiz <Play size={20} />
          </button>
        ) : (
          <button
            onClick={handleNext}
            disabled={isLast}
            className="flex items-center gap-2 px-5 py-3 rounded-xl bg-gray-800 text-gray-300 disabled:opacity-30 disabled:cursor-not-allowed hover:bg-gray-700 transition-colors"
          >
            Next Topic <ChevronRight size={20} />
          </button>
        )}
      </div>
      <p className="text-center text-xs text-gray-600 mt-4">Use Left/Right arrow keys to navigate</p>
    </div>
  );
}
