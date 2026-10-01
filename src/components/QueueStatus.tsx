import { useState, useEffect } from 'react';
import { Database, RefreshCw, CheckCircle, Clock } from 'lucide-react';

export default function QueueStatus() {
  const [tasks, setTasks] = useState<any[]>([]);

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
        <button onClick={fetchStatus} className="p-2 bg-gray-800 text-gray-300 rounded hover:bg-gray-700">
          <RefreshCw size={20} />
        </button>
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
