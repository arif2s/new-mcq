import { useState } from 'react';
import SourceManager from './SourceManager';
import { Database, Server, RefreshCw, CheckCircle, XCircle } from 'lucide-react';

export default function SettingsView() {
  const [dbStatus, setDbStatus] = useState<string>('');
  const [lmStatus, setLmStatus] = useState<{ status: 'idle' | 'testing' | 'success' | 'error', message: string }>({ status: 'idle', message: '' });

  const handleBuildDb = async () => {
    setDbStatus('Building database...');
    try {
      const res = await fetch('/api/mcq/build_db', { method: 'POST' });
      const data = await res.json();
      if (data.status === 'success') {
        setDbStatus('Database generation started in background.');
      } else {
        setDbStatus('Error: ' + data.message);
      }
    } catch (e) {
      setDbStatus('Error connecting to server.');
    }
  };

  const handleTestLmStudio = async () => {
    setLmStatus({ status: 'testing', message: 'Testing connection to LM Studio...' });
    try {
      const res = await fetch('/api/mcq/diagnostics/lm_studio');
      const data = await res.json();
      if (data.status === 'success') {
        setLmStatus({ status: 'success', message: data.message });
      } else {
        setLmStatus({ status: 'error', message: data.message });
      }
    } catch (e) {
      setLmStatus({ status: 'error', message: 'Failed to reach backend for diagnostics.' });
    }
  };

  return (
    <div className="p-6 max-w-5xl mx-auto space-y-8 animate-fade-in">
      <h2 className="text-2xl font-bold text-gray-100 mb-6">Settings & Diagnostics</h2>

      <div className="bg-gray-800 rounded-xl p-6 border border-gray-700">
        <h3 className="text-lg font-semibold text-gray-200 mb-4 flex items-center gap-2">
          <Database size={20} className="text-brand-400" />
          Database Management
        </h3>
        <p className="text-sm text-gray-400 mb-4">
          Parse CSVs in the data folder and build the local SQLite database for fast custom search functionality.
        </p>
        <button
          onClick={handleBuildDb}
          className="px-4 py-2 bg-brand-600 hover:bg-brand-500 text-white rounded-lg text-sm transition-colors flex items-center gap-2"
        >
          <RefreshCw size={16} />
          Build / Index Database
        </button>
        {dbStatus && <p className="mt-3 text-sm text-brand-300">{dbStatus}</p>}
      </div>

      <div className="bg-gray-800 rounded-xl p-6 border border-gray-700">
        <h3 className="text-lg font-semibold text-gray-200 mb-4 flex items-center gap-2">
          <Server size={20} className="text-purple-400" />
          System Diagnostics
        </h3>
        <p className="text-sm text-gray-400 mb-4">
          Test the connection to your local LM Studio server. Ensure LM Studio is running and the local server is enabled.
        </p>
        <button
          onClick={handleTestLmStudio}
          disabled={lmStatus.status === 'testing'}
          className="px-4 py-2 bg-gray-700 hover:bg-gray-600 text-white rounded-lg text-sm transition-colors flex items-center gap-2 disabled:opacity-50"
        >
          <RefreshCw size={16} className={lmStatus.status === 'testing' ? 'animate-spin' : ''} />
          Test Connection
        </button>
        {lmStatus.status !== 'idle' && (
          <div className={`mt-4 p-3 rounded-lg border flex items-center gap-2 ${
            lmStatus.status === 'success' ? 'bg-success-500/10 border-success-500/30 text-success-400' :
            lmStatus.status === 'error' ? 'bg-danger-500/10 border-danger-500/30 text-danger-400' :
            'bg-gray-900 border-gray-600 text-gray-300'
          }`}>
            {lmStatus.status === 'success' && <CheckCircle size={16} />}
            {lmStatus.status === 'error' && <XCircle size={16} />}
            <p className="text-sm">{lmStatus.message}</p>
          </div>
        )}
      </div>

      <div className="border-t border-gray-700 pt-8">
        <h3 className="text-lg font-semibold text-gray-200 mb-4">Knowledge Vault</h3>
        <SourceManager />
      </div>
    </div>
  );
}
