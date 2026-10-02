import { useState, useEffect } from 'react';
import { BookOpen, CheckCircle, RotateCcw, Clock, Settings, Edit3, Trash2 } from 'lucide-react';
import { AppPersistence } from '../types';
import { fetchSessionHistory, deleteSessions } from '../api';

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

  const [displayLimit, setDisplayLimit] = useState(10);
  const [showSettings, setShowSettings] = useState(false);
  const [isEditing, setIsEditing] = useState(false);
  const [selectedSessions, setSelectedSessions] = useState<Set<string>>(new Set());

  const loadSessions = () => {
    setLoading(true);
    fetchSessionHistory().then(data => {
      setSessions(data);
      setLoading(false);
    });
  };

  useEffect(() => {
    loadSessions();
  }, []);

  const handleToggleSelect = (id: string) => {
    const newSet = new Set(selectedSessions);
    if (newSet.has(id)) {
      newSet.delete(id);
    } else {
      newSet.add(id);
    }
    setSelectedSessions(newSet);
  };

  const handleDeleteSelected = async () => {
    if (selectedSessions.size === 0) return;
    if (confirm(`Are you sure you want to delete ${selectedSessions.size} session(s)?`)) {
      await deleteSessions(Array.from(selectedSessions));
      setSelectedSessions(new Set());
      setIsEditing(false);
      loadSessions();
    }
  };

  const displayedSessions = sessions.slice(0, displayLimit);

  return (
    <div className="p-6 max-w-6xl mx-auto">
      <div className="flex items-center justify-between mb-8">
        <div className="flex items-center gap-3">
          <BookOpen className="text-brand-500" size={28} />
          <h1 className="text-2xl font-bold text-gray-200">Knowledge Hub</h1>
        </div>
        <div className="flex items-center gap-3 relative">
          {isEditing && (
            <button
              onClick={handleDeleteSelected}
              disabled={selectedSessions.size === 0}
              className="flex items-center gap-2 px-3 py-1.5 bg-danger-600 hover:bg-danger-500 disabled:opacity-50 text-white text-sm rounded-lg transition-colors"
            >
              <Trash2 size={16} /> Delete Selected ({selectedSessions.size})
            </button>
          )}
          <button
            onClick={() => {
              setIsEditing(!isEditing);
              if (isEditing) setSelectedSessions(new Set());
            }}
            className={`p-2 rounded-lg transition-colors ${isEditing ? 'bg-brand-600 text-white' : 'bg-gray-800 text-gray-400 hover:text-gray-200'}`}
          >
            <Edit3 size={20} />
          </button>
          <button
            onClick={() => setShowSettings(!showSettings)}
            className="p-2 bg-gray-800 rounded-lg text-gray-400 hover:text-gray-200 transition-colors"
          >
            <Settings size={20} />
          </button>

          {showSettings && (
            <div className="absolute right-0 top-full mt-2 w-64 bg-gray-800 border border-gray-700 rounded-xl shadow-xl p-4 z-10 animate-fade-in">
              <h3 className="text-sm font-semibold text-gray-200 mb-3">Hub Settings</h3>
              <label className="block text-xs text-gray-400 mb-1">Display Limit (Newest First)</label>
              <div className="flex items-center gap-2">
                <input
                  type="number"
                  min="1"
                  value={displayLimit}
                  onChange={(e) => setDisplayLimit(Math.max(1, parseInt(e.target.value) || 10))}
                  className="w-full bg-gray-900 border border-gray-600 rounded-lg px-3 py-2 text-sm text-gray-200"
                />
              </div>
              <p className="text-[10px] text-gray-500 mt-2">Limits the number of sessions shown.</p>
            </div>
          )}
        </div>
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
          {displayedSessions.map(session => (
            <div key={session.id} className={`bg-gray-800/60 rounded-xl border ${selectedSessions.has(session.id) ? 'border-brand-500 ring-1 ring-brand-500' : 'border-gray-700 hover:border-gray-600'} transition-all flex flex-col h-full overflow-hidden shadow-lg hover:shadow-brand-500/10 relative`}>
              {isEditing && (
                <div className="absolute top-3 right-3 z-10">
                  <input
                    type="checkbox"
                    checked={selectedSessions.has(session.id)}
                    onChange={() => handleToggleSelect(session.id)}
                    className="w-5 h-5 rounded border-gray-600 bg-gray-900 text-brand-500 focus:ring-brand-500 cursor-pointer"
                  />
                </div>
              )}
              <div className="p-5 flex-1 cursor-default" onClick={() => isEditing && handleToggleSelect(session.id)}>
                <div className="flex justify-between items-start mb-3 pr-8">
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
