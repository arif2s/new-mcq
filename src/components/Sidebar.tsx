import { useState, useRef } from 'react';
import {
  Upload, BookOpen, Brain, BarChart3, Calendar,
  Settings, ChevronDown, Play, Download, Trash2, FileText, CheckCircle, ClipboardList
} from 'lucide-react';
import type { QuizConfig, SubjectData, QuizQuestion } from '../types';
import { parseCSV, generateSampleCSV } from '../csvParser';

interface SidebarProps {
  subjects: SubjectData[];
  onAddSubject: (name: string, fileName: string, questions: QuizQuestion[]) => void;
  onRemoveSubject: (name: string) => void;
  onStartQuiz: (config: QuizConfig) => void;
  onNavigate: (view: string) => void;
  currentView: string;
  onExportData: () => void;
  onImportData: (json: string) => void;
}

export default function Sidebar({
  subjects,
  onAddSubject,
  onRemoveSubject,
  onStartQuiz,
  onNavigate,
  currentView,
  onExportData,
  onImportData,
}: SidebarProps) {
  const [selectedSubject, setSelectedSubject] = useState('');
  const [topicFilter, setTopicFilter] = useState('all');
  const [questionOrder, setQuestionOrder] = useState<'sequential' | 'random'>('random');
  const [questionCount, setQuestionCount] = useState(10);
  const [timeLimitMinutes, setTimeLimitMinutes] = useState(0);
  const [questionTimeoutMinutes, setQuestionTimeoutMinutes] = useState(5);
  const [mode, setMode] = useState<'test' | 'learn' | 'target'>('learn');
  const [useSpacedRepetition, setUseSpacedRepetition] = useState(true);
  const [uploadError, setUploadError] = useState('');
  const [showSettings, setShowSettings] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const importInputRef = useRef<HTMLInputElement>(null);

  const currentSubject = subjects.find(s => s.name === selectedSubject);
  const topics = currentSubject ? ['all', ...currentSubject.topics] : ['all'];
  const maxQuestions = currentSubject
    ? topicFilter === 'all'
      ? currentSubject.questions.length
      : currentSubject.questions.filter(q => q.topic_name === topicFilter).length
    : 0;

  const handleFileUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploadError('');

    const reader = new FileReader();
    reader.onload = (ev) => {
      const text = ev.target?.result as string;
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
    };
    reader.readAsText(file);
    if (fileInputRef.current) fileInputRef.current.value = '';
  };

  const handleImportJSON = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => {
      const text = ev.target?.result as string;
      onImportData(text);
    };
    reader.readAsText(file);
    if (importInputRef.current) importInputRef.current.value = '';
  };

  const handleStart = () => {
    if (!selectedSubject) return;
    onStartQuiz({
      subjectName: selectedSubject,
      topicFilter,
      questionOrder,
      questionCount: mode === 'target' ? questionCount : Math.min(questionCount, maxQuestions),
      timeLimitMinutes,
      questionTimeoutMinutes,
      mode,
      useSpacedRepetition,
    });
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
    { id: 'review', icon: ClipboardList, label: 'Review Queue' },
    { id: 'dashboard', icon: BarChart3, label: 'Dashboard' },
    { id: 'habits', icon: Calendar, label: 'Habit Tracker' },
    { id: 'expertise', icon: Brain, label: 'Expertise Map' },
  ];

  const getModeLabel = () => {
    switch (mode) {
      case 'test': return 'Test';
      case 'learn': return 'Learning';
      case 'target': return `Target (${questionCount} correct)`;
    }
  };

  return (
    <div className="w-80 min-h-screen bg-gray-900 border-r border-gray-700 flex flex-col overflow-y-auto">
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

      {/* Quiz Config */}
      {subjects.length > 0 && (
        <div className="p-4 border-b border-gray-700 flex-1 overflow-y-auto">
          <h3 className="text-sm font-semibold text-gray-300 mb-3 flex items-center gap-2">
            <Settings size={16} />
            Quiz Configuration
          </h3>

          {/* Subject */}
          <label className="block text-xs text-gray-400 mb-1">Subject</label>
          <div className="relative mb-3">
            <select
              value={selectedSubject}
              onChange={e => {
                setSelectedSubject(e.target.value);
                setTopicFilter('all');
              }}
              className="w-full bg-gray-800 border border-gray-600 rounded-lg px-3 py-2 text-sm text-gray-200 appearance-none pr-8"
            >
              <option value="">Select subject...</option>
              {subjects.map(s => (
                <option key={s.name} value={s.name}>{s.name} ({s.questions.length} Q)</option>
              ))}
            </select>
            <ChevronDown size={14} className="absolute right-2 top-3 text-gray-400 pointer-events-none" />
          </div>

          {/* Delete subject button */}
          {selectedSubject && (
            <button
              onClick={() => {
                onRemoveSubject(selectedSubject);
                setSelectedSubject('');
              }}
              className="w-full mb-3 py-1.5 text-xs text-danger-500 hover:bg-danger-500/10 rounded transition-colors flex items-center justify-center gap-1"
            >
              <Trash2 size={12} /> Remove Subject
            </button>
          )}

          {/* Topic filter */}
          <label className="block text-xs text-gray-400 mb-1">Topic Filter</label>
          <div className="relative mb-3">
            <select
              value={topicFilter}
              onChange={e => setTopicFilter(e.target.value)}
              className="w-full bg-gray-800 border border-gray-600 rounded-lg px-3 py-2 text-sm text-gray-200 appearance-none pr-8"
            >
              {topics.map(t => (
                <option key={t} value={t}>{t === 'all' ? '📋 All Topics' : t}</option>
              ))}
            </select>
            <ChevronDown size={14} className="absolute right-2 top-3 text-gray-400 pointer-events-none" />
          </div>

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
              : `Questions: ${questionCount} ${maxQuestions > 0 ? `(max ${maxQuestions})` : ''}`
            }
          </label>
          <input
            type="range"
            min={1}
            max={mode === 'target' ? 100 : Math.max(maxQuestions, 1)}
            value={mode === 'target' ? questionCount : Math.min(questionCount, maxQuestions || 1)}
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
            onClick={handleStart}
            disabled={!selectedSubject}
            className="w-full py-3 bg-gradient-to-r from-brand-600 to-purple-600 text-white font-bold rounded-xl
                       hover:from-brand-500 hover:to-purple-500 transition-all disabled:opacity-40 disabled:cursor-not-allowed
                       flex items-center justify-center gap-2 text-sm shadow-lg hover:shadow-brand-500/25"
          >
            <Play size={18} />
            Start {getModeLabel()}
          </button>
        </div>
      )}

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
