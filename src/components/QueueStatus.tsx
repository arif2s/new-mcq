import { useState, useEffect } from 'react';
import { Database, RefreshCw, CheckCircle, Clock, Settings } from 'lucide-react';
import { AppPersistence } from '../types';

interface QueueStatusProps {
  persistence: AppPersistence;
  onUpdateLimit: (limit: number) => void;
}

export default function QueueStatus({ persistence, onUpdateLimit }: QueueStatusProps) {
  const [tasks, setTasks] = useState<any[]>([]);
  const [showSettings, setShowSettings] = useState(false);

  const fetchStatus = () => {
    fetch('/api/queue/status')
      .then(res => res.json())
      .then(data => setTasks(data))
      .catch(console.error);
  };

  useEffect(() => {
    fetchStatus();
    const int = setInterval(fetchStatus, 5000);
    return () => clearInterval(int);
  }, []);

  return (
    <div className="max-w-5xl mx-auto p-6 animate-fade-in">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-3xl font-bold text-gray-100 flex items-center gap-3">
            <Database size={32} className="text-brand-400" />
            Background Processing Queue
          </h1>
          <p className="text-gray-400 mt-1">
            Status of MCQs being synthesized by local LLMs with detailed explanations and notes.
          </p>
        </div>
        <div className="flex items-center gap-3 relative">
          <button onClick={fetchStatus} className="p-2 bg-gray-800 text-gray-300 rounded hover:bg-gray-700">
            <RefreshCw size={20} />
          </button>
          <button onClick={() => setShowSettings(!showSettings)} className="p-2 bg-gray-800 text-gray-300 rounded hover:bg-gray-700">
            <Settings size={20} />
          </button>

          {showSettings && (
            <div className="absolute right-0 top-full mt-2 w-72 bg-gray-800 border border-gray-700 rounded-xl shadow-xl p-4 z-10 animate-fade-in">
              <h3 className="text-sm font-semibold text-gray-200 mb-3">Queue Settings</h3>
              <label className="block text-xs text-gray-400 mb-1">Max Processing Batch Limit</label>
              <input
                type="number"
                min="1"
                max="500"
                value={persistence.processingQueueLimit || 10}
                onChange={(e) => onUpdateLimit(Math.max(1, parseInt(e.target.value) || 10))}
                className="w-full bg-gray-900 border border-gray-600 rounded-lg px-3 py-2 text-sm text-gray-200"
              />
              <p className="text-[10px] text-gray-500 mt-2">
                Limit the number of topics added at once to prevent crashing the local LM Studio backend.
              </p>
            </div>
          )}
        </div>
      </div>

      <div className="bg-gray-800/60 rounded-2xl p-6 border border-gray-700 mb-8">
        {tasks.length === 0 ? (
          <p className="text-gray-500">Queue is empty.</p>
        ) : (
          <div className="space-y-3">
            {tasks.map(t => (
              <div key={t.id} className="p-4 rounded-xl border bg-gray-900/60 border-gray-700 flex justify-between items-center">
                <div className="flex-1 truncate pr-4">
                  <p className="text-sm font-semibold text-gray-200 truncate">{t.question}</p>
                </div>
                <div className="flex items-center gap-2">
                  {t.status === 'COMPLETED' ? (
                    <CheckCircle className="text-green-500" size={18} />
                  ) : t.status === 'PROCESSING' ? (
                    <RefreshCw className="text-brand-400 animate-spin" size={18} />
                  ) : (
                    <Clock className="text-gray-400" size={18} />
                  )}
                  <span className="text-xs uppercase text-gray-400 font-mono">{t.status}</span>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
