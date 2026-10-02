import { useState, useRef, useMemo, useEffect } from 'react';
import {
  Upload, BookOpen, Brain, BarChart3, Calendar, Database,
  Settings, ChevronDown, Play, Download, FileText, CheckCircle
} from 'lucide-react';
import type { QuizConfig, SubjectData, QuizQuestion } from '../types';
import { parseCSV, generateSampleCSV } from '../csvParser';

interface SidebarProps {
  subjects: SubjectData[];
  processingQueueLimit?: number;
  onAddSubject: (name: string, fileName: string, questions: QuizQuestion[]) => void;
  onStartQuiz: (config: QuizConfig) => void;
  onNavigate: (view: string) => void;
  currentView: string;
  onExportData: () => void;
  onImportData: (json: string) => void;
}

export default function Sidebar({
  subjects,
  processingQueueLimit = 10,
  onAddSubject,
  onStartQuiz,
  onNavigate,
  currentView,
  onExportData,
  onImportData,
}: SidebarProps) {
  const [selectedSubject, setSelectedSubject] = useState('');
  const [topicFilter] = useState('all');
  const [questionOrder, setQuestionOrder] = useState<'sequential' | 'random'>('random');
  const [questionCount, setQuestionCount] = useState(10);
  const [timeLimitMinutes, setTimeLimitMinutes] = useState(0);
  const [questionTimeoutMinutes, setQuestionTimeoutMinutes] = useState(5);
  const [mode, setMode] = useState<'test' | 'learn' | 'target'>('learn');
  const [useSpacedRepetition, setUseSpacedRepetition] = useState(true);
  const [uploadError, setUploadError] = useState('');
  const [showSettings, setShowSettings] = useState(false);

  // States for backend search integration
  const [remoteSubjects, setRemoteSubjects] = useState<{subject: string, count: number}[]>([]);
  const [remoteTopics, setRemoteTopics] = useState<{topic: string, count: number}[]>([]);
  const [selectedRemoteSubjects, setSelectedRemoteSubjects] = useState<string[]>(['all']);
  const [selectedRemoteTopics, setSelectedRemoteTopics] = useState<string[]>(['all']);
  const [keyword, setKeyword] = useState('');
  const [matchedCount, setMatchedCount] = useState<number | null>(null);
  const [dbExists, setDbExists] = useState<boolean>(false);

  useEffect(() => {
    fetch('/api/mcq/db_status')
      .then(res => res.json())
      .then(data => {
        if (data.status === 'success') {
          setDbExists(data.exists);
        } else {
          setDbExists(false);
        }
      })
      .catch((error) => {
        console.error(error);
        setDbExists(false);
      });
  }, []);

  useEffect(() => {
    const fetchData = () => {
      fetch('/api/mcq/subjects').then(res => res.json()).then(data => setRemoteSubjects(data)).catch(console.error);
      fetch('/api/mcq/topics').then(res => res.json()).then(data => setRemoteTopics(data)).catch(console.error);
      fetch('/api/mcq/db_status')
        .then(res => res.json())
        .then(data => {
          if (data.status === 'success') {
            setDbExists(data.exists);
          } else {
            setDbExists(false);
          }
        })
        .catch((error) => {
          console.error(error);
          setDbExists(false);
        });
    };

    fetchData();

    window.addEventListener('db_updated', fetchData);
    return () => window.removeEventListener('db_updated', fetchData);
  }, []);

  useEffect(() => {
    const timer = setTimeout(() => {
      fetch('/api/mcq/search_count', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          subjects: selectedRemoteSubjects,
          topics: selectedRemoteTopics,
          keyword,
          count: 0
        })
      }).then(res => res.json()).then(data => {
        if (data.status === 'success') setMatchedCount(data.count);
      }).catch(console.error);
    }, 300);
    return () => clearTimeout(timer);
  }, [selectedRemoteSubjects, selectedRemoteTopics, keyword]);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const importInputRef = useRef<HTMLInputElement>(null);

  // Memoize heavy filtering to prevent re-execution when config sliders change
  const { } = useMemo(() => {
    const subject = subjects.find(s => s.name === selectedSubject);
    const t = subject ? ['all', ...subject.topics] : ['all'];
    const maxQ = subject
      ? topicFilter === 'all'
        ? subject.questions.length
        : subject.questions.filter(q => q.topic_name === topicFilter).length
      : 0;

    return { currentSubject: subject, topics: t, maxQuestions: maxQ };
  }, [subjects, selectedSubject, topicFilter]);

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploadError('');

    try {
      // Use modern Promise-based File API
      const text = await file.text();
      const { questions, errors } = parseCSV(text);

      if (errors.length > 0) {
        setUploadError(errors.slice(0, 3).join('; '));
        return;
      }

      if (questions.length === 0) {
        setUploadError('No valid questions found in the CSV.');
        return;
      }

      const subjectName = file.name.replace(/\.csv$/i, '').replace(/[_-]/g, ' ');
      onAddSubject(subjectName, file.name, questions);
      setSelectedSubject(subjectName);
      setUploadError('');
    } catch (error) {
      setUploadError('Failed to process file. Please try again.');
    } finally {
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  const handleImportJSON = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    try {
      const text = await file.text();
      onImportData(text);
    } catch (error) {
      // Import handler in App component manages parsing errors
    } finally {
      if (importInputRef.current) importInputRef.current.value = '';
    }
  };

  const handleStart = async ( ) => {
    if (!dbExists) {
      alert("Database not found! Please go to the Settings tab to build and index the database before searching.");
      onNavigate('settings');
      return;
    }

    try {
      // If keyword is empty, just rely entirely on selectedRemoteSubjects and selectedRemoteTopics.
      const res = await fetch('/api/mcq/generate_set', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          subjects: selectedRemoteSubjects,
          topics: selectedRemoteTopics,
          keyword: keyword.trim(),
          count: questionCount
        })
      });
      const data = await res.json();
      if (data.status === 'success' && data.questions.length > 0) {
        onAddSubject('Custom Set', 'custom_set.csv', data.questions);
        onStartQuiz({
          subjectName: 'Custom Set',
          topicFilter: 'all',
          questionOrder,
          questionCount: data.questions.length,
          timeLimitMinutes,
          questionTimeoutMinutes,
          mode,
          useSpacedRepetition,
        });
      } else {
        alert('No matching questions found.');
      }
    } catch (e) {
      console.error(e);
      alert('Error loading questions');
    }
  };

  const handleProcessRemote = async () => {
    if (questionCount > processingQueueLimit) {
      if (!confirm(`Warning: You are about to add ${questionCount} topics to the queue, which exceeds the configured limit of ${processingQueueLimit}.\n\nThis may crash the LM Studio backend if it runs out of memory or times out.\n\nAre you sure you want to proceed?`)) {
        return;
      }
    }

    try {
      const res = await fetch('/api/mcq/generate_set', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          subjects: selectedRemoteSubjects,
          topics: selectedRemoteTopics,
          keyword,
          count: questionCount
        })
      });
      const data = await res.json();
      if (data.status === 'success' && data.questions.length > 0) {
        const prepRes = await fetch('/api/queue/prepare', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({questions: data.questions})
        });
        if (prepRes.ok) {
          alert('Questions queued for preparation!');
        }
      } else {
         alert('No matching questions to process.');
      }
    } catch (e) {
      console.error(e);
    }
  };

  const handleDownloadSample = () => {
    const csv = generateSampleCSV();
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'sample_quiz.csv';
    a.click();
    URL.revokeObjectURL(url);
  };

  const navItems = [
    { id: 'home', icon: BookOpen, label: 'Home' },
    { id: 'settings', icon: Settings, label: 'Settings' },
    { id: 'knowledge', icon: BookOpen, label: 'Knowledge Hub' },
    { id: 'dashboard', icon: BarChart3, label: 'Dashboard' },
    { id: 'queue', icon: Database, label: 'Processing Queue' },
    { id: 'habits', icon: Calendar, label: 'Habit Tracker' },
    { id: 'expertise', icon: Brain, label: 'Expertise Map' },
  ];

  return (
    <div className="w-80 h-full bg-gray-900 border-r border-gray-700 flex flex-col overflow-y-auto">
      {/* Logo */}
      <div className="p-5 border-b border-gray-700">
        <h1 className="text-2xl font-bold text-brand-400 flex items-center gap-2">
          📝 Quiz Master Pro
        </h1>
        <p className="text-xs text-gray-400 mt-1">Smart Learning with Spaced Repetition</p>
      </div>

      {/* Navigation */}
      <nav className="p-3 border-b border-gray-700">
        {navItems.map(item => (
          <button
            key={item.id}
            onClick={() => onNavigate(item.id)}
            className={`w-full flex items-center gap-3 px-4 py-2.5 rounded-lg mb-1 text-sm font-medium transition-all ${
              currentView === item.id
                ? 'bg-brand-600 text-white'
                : 'text-gray-300 hover:bg-gray-800 hover:text-white'
            }`}
          >
            <item.icon size={18} />
            {item.label}
          </button>
        ))}
      </nav>

      {/* Upload Section */}
      {!dbExists && (
        <div className="p-4 border-b border-gray-700">
          <h3 className="text-sm font-semibold text-gray-300 mb-3 flex items-center gap-2">
            <Upload size={16} />
            Upload Quiz CSV
          </h3>
          <input
            ref={fileInputRef}
            type="file"
            accept=".csv"
            onChange={handleFileUpload}
            className="hidden"
          />
          <button
            onClick={() => fileInputRef.current?.click()}
            className="w-full py-2.5 border-2 border-dashed border-gray-600 rounded-lg text-gray-400 hover:border-brand-500 hover:text-brand-400 transition-all text-sm"
          >
            📁 Choose CSV File
          </button>
          {uploadError && (
            <p className="text-danger-500 text-xs mt-2">{uploadError}</p>
          )}
          <button
            onClick={handleDownloadSample}
            className="w-full mt-2 py-1.5 text-xs text-gray-500 hover:text-brand-400 transition-colors flex items-center justify-center gap-1"
          >
            <Download size={12} /> Download Sample CSV
          </button>
        </div>
      )}

      {/* Search & Quiz Config */}
      <div className="p-4 border-b border-gray-700 flex-1 overflow-y-auto">
        <h3 className="text-sm font-semibold text-gray-300 mb-3 flex items-center gap-2">
          <Settings size={16} />
          Quiz Configuration
        </h3>

        {/* Unified Search Config */}
        <div className="mb-4 p-3 bg-gray-800 rounded-lg border border-brand-500/30">
          <h4 className="text-xs font-semibold text-brand-400 mb-2">Subject / Topic Selection</h4>

          {/* Remote Subjects Filter */}
          <label className="block text-xs text-gray-400 mb-1">Subject Filters</label>
          <div className="relative mb-2">
            <select
              value={selectedRemoteSubjects.includes('all') ? 'all' : selectedRemoteSubjects[0] || ''}
              onChange={e => setSelectedRemoteSubjects(e.target.value === 'all' ? ['all'] : [e.target.value])}
              className="w-full bg-gray-900 border border-gray-600 rounded-lg px-3 py-2 text-sm text-gray-200 appearance-none pr-8"
            >
              <option value="all">📋 Select All Subjects</option>
              {remoteSubjects.map(s => (
                <option key={s.subject} value={s.subject}>{s.subject} ({s.count})</option>
              ))}
            </select>
            <ChevronDown size={14} className="absolute right-2 top-3 text-gray-400 pointer-events-none" />
          </div>

          {/* Remote Topics Filter */}
          <label className="block text-xs text-gray-400 mb-1">Topic Filters</label>
          <div className="relative mb-2">
            <select
              value={selectedRemoteTopics.includes('all') ? 'all' : selectedRemoteTopics[0] || ''}
              onChange={e => setSelectedRemoteTopics(e.target.value === 'all' ? ['all'] : [e.target.value])}
              className="w-full bg-gray-900 border border-gray-600 rounded-lg px-3 py-2 text-sm text-gray-200 appearance-none pr-8"
            >
              <option value="all">📋 All Topics</option>
              {remoteTopics.map(t => (
                <option key={t.topic} value={t.topic}>{t.topic} ({t.count})</option>
              ))}
            </select>
            <ChevronDown size={14} className="absolute right-2 top-3 text-gray-400 pointer-events-none" />
          </div>

          {/* Custom Keyword Search */}
          <label className="block text-xs text-gray-400 mb-1">Custom Keyword Search</label>
          <input
            type="text"
            value={keyword}
            onChange={e => setKeyword(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'Enter') {
                e.preventDefault();
                handleStart();
              }
            }}
            placeholder="e.g. potassium, insulin..."
            className="w-full bg-gray-900 border border-gray-600 rounded-lg px-3 py-2 text-sm text-gray-200 mb-2 focus:ring-brand-500 focus:border-brand-500"
          />

          {/* Status Indicator */}
          {matchedCount !== null && (
            <div className="flex items-center gap-2 mt-1">
              <span className="flex w-2 h-2 rounded-full bg-brand-500"></span>
              <span className="text-xs text-brand-300">
                {matchedCount} matching questions found
              </span>
            </div>
          )}
        </div>

        {/* Global Quiz Settings */}

          {/* Mode */}
          <label className="block text-xs text-gray-400 mb-1">Mode</label>
          <div className="flex gap-1 mb-3">
            <button
              onClick={() => setMode('test')}
              className={`flex-1 py-2 rounded-lg text-xs font-medium transition-all ${
                mode === 'test'
                  ? 'bg-brand-600 text-white'
                  : 'bg-gray-800 text-gray-400 hover:bg-gray-700'
              }`}
            >
              📝 Test
            </button>
            <button
              onClick={() => setMode('learn')}
              className={`flex-1 py-2 rounded-lg text-xs font-medium transition-all ${
                mode === 'learn'
                  ? 'bg-brand-600 text-white'
                  : 'bg-gray-800 text-gray-400 hover:bg-gray-700'
              }`}
            >
              📖 Learn
            </button>
            <button
              onClick={() => setMode('target')}
              className={`flex-1 py-2 rounded-lg text-xs font-medium transition-all ${
                mode === 'target'
                  ? 'bg-purple-600 text-white'
                  : 'bg-gray-800 text-gray-400 hover:bg-gray-700'
              }`}
            >
              🎯 Target
            </button>
          </div>

          {/* Target mode description */}
          {mode === 'target' && (
            <div className="mb-3 p-2 bg-purple-500/10 border border-purple-500/30 rounded-lg">
              <p className="text-xs text-purple-300">
                Quiz continues until you get {questionCount} correct answers. Wrong answers don't end the quiz!
              </p>
            </div>
          )}

          {/* Spaced Repetition Toggle */}
          <label className="flex items-center gap-2 mb-3 cursor-pointer group">
            <input
              type="checkbox"
              checked={useSpacedRepetition}
              onChange={e => setUseSpacedRepetition(e.target.checked)}
              className="w-4 h-4 rounded border-gray-600 bg-gray-800 text-brand-500 focus:ring-brand-500 focus:ring-offset-gray-900"
            />
            <span className="text-xs text-gray-300 group-hover:text-white transition-colors">
              🧠 Use Spaced Repetition
            </span>
          </label>

          {/* Question Order */}
          <label className="block text-xs text-gray-400 mb-1">Question Order</label>
          <div className="flex gap-1 mb-3">
            <button
              onClick={() => setQuestionOrder('sequential')}
              className={`flex-1 py-1.5 rounded-lg text-xs font-medium transition-all ${
                questionOrder === 'sequential'
                  ? 'bg-brand-600 text-white'
                  : 'bg-gray-800 text-gray-400 hover:bg-gray-700'
              }`}
            >
              📊 Sequential
            </button>
            <button
              onClick={() => setQuestionOrder('random')}
              className={`flex-1 py-1.5 rounded-lg text-xs font-medium transition-all ${
                questionOrder === 'random'
                  ? 'bg-brand-600 text-white'
                  : 'bg-gray-800 text-gray-400 hover:bg-gray-700'
              }`}
            >
              🎲 Random
            </button>
          </div>

          {/* Question Count / Target */}
          <label className="block text-xs text-gray-400 mb-1">
            {mode === 'target'
              ? `Target Correct: ${questionCount}`
              : `Questions: ${questionCount} ${matchedCount ? `(max ${matchedCount})` : ''}`
            }
          </label>
          <input
            type="range"
            min={1}
            max={mode === 'target' ? 100 : Math.max(matchedCount || 100, 1)}
            value={mode === 'target' ? questionCount : Math.min(questionCount, matchedCount || 100)}
            onChange={e => setQuestionCount(Number(e.target.value))}
            className="w-full mb-3 accent-brand-500"
          />

          {/* Question Timeout */}
          <label className="block text-xs text-gray-400 mb-1">
            Question Timeout: {questionTimeoutMinutes === 0 ? 'No timeout' : `${questionTimeoutMinutes} min`}
          </label>
          <input
            type="range"
            min={0}
            max={10}
            step={1}
            value={questionTimeoutMinutes}
            onChange={e => setQuestionTimeoutMinutes(Number(e.target.value))}
            className="w-full mb-1 accent-brand-500"
          />
          <p className="text-xs text-gray-500 mb-3">
            {questionTimeoutMinutes > 0 ? 'Pauses if no answer - asks if you need a break' : 'No pause for inactivity'}
          </p>

          {/* Time Limit */}
          <label className="block text-xs text-gray-400 mb-1">
            Total Time Limit: {timeLimitMinutes === 0 ? 'No limit' : `${timeLimitMinutes} min`}
          </label>
          <input
            type="range"
            min={0}
            max={120}
            step={5}
            value={timeLimitMinutes}
            onChange={e => setTimeLimitMinutes(Number(e.target.value))}
            className="w-full mb-4 accent-brand-500"
          />

          {/* Start Button */}
          <button
            onClick={() => handleStart()}
            disabled={matchedCount === 0 && keyword !== ''}
            className="w-full py-3 mb-2 bg-gradient-to-r from-brand-600 to-purple-600 text-white font-bold rounded-xl
                       hover:from-brand-500 hover:to-purple-500 transition-all disabled:opacity-40 disabled:cursor-not-allowed
                       flex items-center justify-center gap-2 text-sm shadow-lg hover:shadow-brand-500/25"
          >
            <Play size={18} />
            Start Learning
          </button>

          {/* Process Offline Button */}
          <button
            onClick={handleProcessRemote}
            disabled={matchedCount === 0 && keyword !== ''}
            className="w-full py-2 bg-brand-800 hover:bg-brand-700 text-white rounded-lg text-sm disabled:opacity-50 transition-colors border border-brand-600 flex items-center justify-center gap-2 shadow-lg"
          >
            <Database size={16} /> Process (Offline Preparation)
          </button>
        </div>

      {/* Auto-save indicator */}
      <div className="px-4 py-2 bg-success-500/10 border-t border-success-500/20">
        <p className="text-xs text-success-500 flex items-center gap-1">
          <CheckCircle size={12} />
          Auto-saves after each answer
        </p>
      </div>

      {/* Data Management */}
      <div className="p-4 border-t border-gray-700">
        <button
          onClick={() => setShowSettings(!showSettings)}
          className="w-full flex items-center justify-between text-xs text-gray-400 hover:text-gray-200 transition-colors mb-2"
        >
          <span className="flex items-center gap-1"><FileText size={12} /> Backup & Transfer</span>
          <ChevronDown size={12} className={`transition-transform ${showSettings ? 'rotate-180' : ''}`} />
        </button>
        {showSettings && (
          <div className="space-y-2 animate-fade-in">
            <p className="text-xs text-gray-500 mb-2">For backup or moving data to another device:</p>
            <button
              onClick={onExportData}
              className="w-full py-2 text-xs bg-gray-800 text-gray-300 rounded-lg hover:bg-gray-700 transition-colors flex items-center justify-center gap-1"
            >
              <Download size={12} /> Export Backup (JSON)
            </button>
            <input ref={importInputRef} type="file" accept=".json" onChange={handleImportJSON} className="hidden" />
            <button
              onClick={() => importInputRef.current?.click()}
              className="w-full py-2 text-xs bg-gray-800 text-gray-300 rounded-lg hover:bg-gray-700 transition-colors flex items-center justify-center gap-1"
            >
              <Upload size={12} /> Import Backup (JSON)
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
