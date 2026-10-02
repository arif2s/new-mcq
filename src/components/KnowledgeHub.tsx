import { useState, useEffect } from 'react';
import { BookOpen, CheckCircle, RotateCcw, Clock, } from 'lucide-react';
import { AppPersistence } from '../types';
import { fetchSessionHistory } from '../api';

interface KnowledgeHubProps {
  persistence: AppPersistence;
  onNavigate: (view: string) => void;
  onStudyTopics: (sessionId: string) => void;
  onReviewExplanations: (sessionId: string) => void;
  onRetakeSet: (session: any) => void;
}

export default function KnowledgeHub({ onStudyTopics, onReviewExplanations, onRetakeSet }: KnowledgeHubProps) {
  const [sessions, setSessions] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let mounted = true;
    fetchSessionHistory().then(data => {
      if (mounted) {
        setSessions(data);
        setLoading(false);
      }
    });
    return () => { mounted = false; };
  }, []);

  return (
    <div className="p-6 max-w-6xl mx-auto">
      <div className="flex items-center gap-3 mb-8">
        <BookOpen className="text-brand-500" size={28} />
        <h1 className="text-2xl font-bold text-gray-200">Knowledge Hub</h1>
      </div>

      {loading ? (
        <div className="text-center py-12 text-gray-400">Loading your study history...</div>
      ) : sessions.length === 0 ? (
        <div className="text-center py-16 bg-gray-800/40 rounded-2xl border border-gray-700">
          <BookOpen size={48} className="mx-auto text-brand-500 mb-4 opacity-50" />
          <h2 className="text-xl font-semibold text-gray-200 mb-2">No Study Sessions Yet</h2>
          <p className="text-gray-400">Complete a quiz to see your history here.</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {sessions.map(session => (
            <div key={session.id} className="bg-gray-800/60 rounded-xl border border-gray-700 hover:border-gray-600 transition-all flex flex-col h-full overflow-hidden shadow-lg hover:shadow-brand-500/10">
              <div className="p-5 flex-1">
                <div className="flex justify-between items-start mb-3">
                  <span className="text-xs font-medium px-2.5 py-1 bg-brand-600/20 text-brand-400 rounded-lg">
                    {session.subject_name}
                  </span>
                  <div className="text-xs text-gray-500 flex items-center gap-1">
                    <Clock size={12} />
                    {new Date(session.date).toLocaleDateString()}
                  </div>
                </div>

                <h3 className="text-lg font-semibold text-gray-200 mb-2">Session {session.id.split('_').pop()?.substring(0,6)}</h3>

                <div className="flex flex-wrap gap-1 mb-4">
                  {session.topics && session.topics.map((t: string) => (
                    <span key={t} className="text-[10px] px-2 py-0.5 bg-gray-700 text-gray-400 rounded-full">
                      {t}
                    </span>
                  ))}
                </div>

                <div className="grid grid-cols-2 gap-4 bg-gray-900/50 rounded-lg p-3 border border-gray-700/50 mb-2">
                  <div className="text-center">
                    <p className="text-xs text-gray-500 mb-1">Score</p>
                    <p className="text-lg font-bold text-white">{session.score} / {session.max_score}</p>
                  </div>
                  <div className="text-center">
                    <p className="text-xs text-gray-500 mb-1">Accuracy</p>
                    <p className={`text-lg font-bold ${session.accuracy >= 70 ? 'text-success-500' : session.accuracy >= 50 ? 'text-warning-500' : 'text-danger-500'}`}>
                      {Math.round(session.accuracy)}%
                    </p>
                  </div>
                </div>
                <div className="flex justify-between text-xs text-gray-400 px-1">
                  <span><span className="text-success-400">{session.correct_answers}</span> Correct</span>
                  <span><span className="text-danger-400">{session.wrong_answers}</span> Wrong</span>
                </div>
              </div>

              <div className="border-t border-gray-700 p-3 bg-gray-900/30 grid grid-cols-2 gap-2">
                <button
                  onClick={() => onStudyTopics(session.id)}
                  className="py-2 px-3 text-xs font-medium bg-brand-600/20 text-brand-400 hover:bg-brand-600/30 hover:text-brand-300 rounded-lg transition-colors flex items-center justify-center gap-1.5 col-span-2"
                >
                  <BookOpen size={14} /> Study Topics Covered
                </button>
                <button
                  onClick={() => onReviewExplanations(session.id)}
                  className="py-2 px-3 text-xs font-medium bg-gray-700 text-gray-300 hover:bg-gray-600 rounded-lg transition-colors flex items-center justify-center gap-1.5"
                >
                  <CheckCircle size={14} /> Explanations
                </button>
                <button
                  onClick={() => onRetakeSet(session)}
                  className="py-2 px-3 text-xs font-medium bg-gray-700 text-gray-300 hover:bg-gray-600 rounded-lg transition-colors flex items-center justify-center gap-1.5"
                >
                  <RotateCcw size={14} /> Retake Set
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
