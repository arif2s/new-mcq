import SettingsView from "./components/SettingsView";
import { useState, useCallback, useEffect, useRef } from 'react';
import { Menu, X, HelpCircle } from 'lucide-react';
import Sidebar from './components/Sidebar';
import HomeView from './components/HomeView';
import QuizView from './components/QuizView';
import ResultsView from './components/ResultsView';
import Dashboard from './components/Dashboard';
import HabitTracker from './components/HabitTracker';
import QueueStatus from './components/QueueStatus';
import ExpertiseMap from './components/ExpertiseMap';
import HelpModal from './components/HelpModal';
import KnowledgeHub from './components/KnowledgeHub';
import StudyNotesView from './components/StudyNotesView';
import PreQuizModal from './components/PreQuizModal';
import SessionReviewView from './components/SessionReviewView';
import { fetchState, saveState, saveSession } from './api';
import {
  loadPersistence,
  savePersistence,
  updateHabitLog,
  updateAnkiCard,
  getAnkiDueQuestions,
  updateTopicExpertise,
  exportPersistenceJSON,
  addToReviewQueue,
} from './storage';
import type {
  AppPersistence,
  QuizConfig,
  ActiveQuiz,
  QuizQuestion,
  SubjectData,
  OptionKey,
  TestResult,
  AnswerRecord,
  DailyTargets,
  RankSimConfig,
} from './types';

function shuffleArray<T>(arr: T[]): T[] {
  const shuffled = [...arr];
  for (let i = shuffled.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [shuffled[i], shuffled[j]] = [shuffled[j], shuffled[i]];
  }
  return shuffled;
}

export default function App() {
  const [persistence, setPersistence] = useState<AppPersistence>(() => loadPersistence());
  const [currentView, setCurrentView] = useState('home');
  const [activeQuiz, setActiveQuiz] = useState<ActiveQuiz | null>(null);
  const [pendingQuiz, setPendingQuiz] = useState<ActiveQuiz | null>(null);
  const [lastResult, setLastResult] = useState<TestResult | null>(null);
  const [lastQuizQuestions, setLastQuizQuestions] = useState<QuizQuestion[]>([]);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [isStateLoaded, setIsStateLoaded] = useState(false);

  useEffect(() => {
    fetchState().then((state) => {
      if (state) setPersistence(state);
      setIsStateLoaded(true);
    });
  }, []);

  useEffect(() => {
    if (isStateLoaded) {
      savePersistence(persistence);
      saveState(persistence);
    }
  }, [persistence, isStateLoaded]);

  const handleAddSubject = useCallback((name: string, fileName: string, questions: QuizQuestion[]) => {
    setPersistence(prev => {
      const existing = prev.subjects.findIndex(s => s.name === name);
      const topics = [...new Set(questions.map(q => q.topic_name))];
      const subjectData: SubjectData = {
        name,
        fileName,
        questions,
        topics,
        dateAdded: new Date().toISOString(),
        lastAccessed: new Date().toISOString(),
        totalAttempts: 0,
      };

      const updated = { ...prev, subjects: [...prev.subjects] };
      if (existing >= 0) {
        updated.subjects[existing] = subjectData;
      } else {
        updated.subjects.push(subjectData);
      }
      return updated;
    });
  }, []);


  const handleStartQuiz = useCallback((config: QuizConfig) => {
    setPersistence(prev => {
      const subject = prev.subjects.find(s => s.name === config.subjectName);
      if (!subject) return prev;

      let pool = [...subject.questions];

      if (config.topicFilter !== 'all') {
        pool = pool.filter(q => q.topic_name === config.topicFilter);
      }

      if (config.useSpacedRepetition) {
        const allIds = pool.map(q => q.id);
        const { dueForReview, newQuestions } = getAnkiDueQuestions(prev, config.subjectName, allIds);

        const duePool = pool.filter(q => dueForReview.includes(q.id));
        const newPool = pool.filter(q => newQuestions.includes(q.id));
        const combined = [...shuffleArray(duePool), ...shuffleArray(newPool)];

        pool = combined.length > 0 ? combined : pool;
      }

      if (config.questionOrder === 'random' || config.mode === 'target') {
        pool = shuffleArray(pool);
      }

      if (config.mode !== 'target') {
        pool = pool.slice(0, config.questionCount);
      }


      if (pool.length > 0) {
        const now = Date.now();
        const newQuiz = {
          id: `session_${now}_${Math.random().toString(36).substr(2, 5)}`,
          config,
          questions: pool,
          currentIndex: 0,
          answers: {},
          startTime: now,
          questionStartTime: now,
          timeRemainingSeconds: config.timeLimitMinutes > 0 ? config.timeLimitMinutes * 60 : null,
          questionTimeRemainingSeconds: config.questionTimeoutMinutes > 0 ? config.questionTimeoutMinutes * 60 : null,
          isCompleted: false,
          isPaused: false,
          score: 0,
          correctCount: 0,
          wrongCount: 0,
          targetCorrect: config.mode === 'target' ? config.questionCount : null,
        };

        // Immediately persist the pending session
        const initialResult = {
          id: newQuiz.id,
          subjectName: config.subjectName,
          date: new Date(now).toISOString(),
          timestamp: now,
          topics: [...new Set(pool.map(q => q.topic_name))],
          totalQuestions: pool.length,
          correctAnswers: 0,
          wrongAnswers: 0,
          unanswered: pool.length,
          score: 0,
          maxScore: pool.length,
          accuracy: 0,
          timeLimitSeconds: newQuiz.timeRemainingSeconds,
          timeUsedSeconds: 0,
          answers: [],
          mode: config.mode,
        };
        saveSession(initialResult, pool);

        setPendingQuiz(newQuiz);
      }

      return prev;
    });
  }, []);

  const handleStartPendingQuiz = useCallback(() => {
    if (pendingQuiz) {
      setActiveQuiz({
        ...pendingQuiz,
        startTime: Date.now(),
        questionStartTime: Date.now()
      });
      setLastResult(null);
      setCurrentView('quiz');
      setPendingQuiz(null);
      setActiveSessionId(null);
    }
  }, [pendingQuiz]);

  const handleAnswer = useCallback((questionId: string, selected: string, timeSpentMs: number, timedOut: boolean) => {
    if (!activeQuiz || activeQuiz.answers[questionId]) return;
    const question = activeQuiz.questions.find(q => q.id === questionId);
    if (!question) return;

    const isCorrect = !timedOut && selected === question.correct_answer;

    setPersistence(prevPersist =>
      updateAnkiCard(prevPersist, questionId, activeQuiz.config.subjectName, question.topic_name, isCorrect)
    );

    setActiveQuiz(prev => {
      if (!prev || prev.answers[questionId]) return prev;
      const answer: AnswerRecord = {
        questionId,
        selected: selected as OptionKey,
        isCorrect,
        timestamp: Date.now(),
        timeSpentMs,
        timedOut,
      };
      return {
        ...prev,
        answers: { ...prev.answers, [questionId]: answer },
        score: prev.score + (timedOut ? 0 : isCorrect ? 4 : -1),
        correctCount: prev.correctCount + (isCorrect ? 1 : 0),
        wrongCount: prev.wrongCount + (!timedOut && !isCorrect ? 1 : 0),
        questionStartTime: Date.now(),
      };
    });
  }, [activeQuiz]);

  const handleNext = useCallback(() => {
    setActiveQuiz(prev => {
      if (!prev) return prev;

      let nextIndex = prev.currentIndex + 1;
      if (prev.config.mode === 'target' && nextIndex >= prev.questions.length) {
        const wrongOrUnanswered = prev.questions.filter(q => {
          const ans = prev.answers[q.id];
          return !ans || !ans.isCorrect;
        });
        if (wrongOrUnanswered.length > 0) {
          return {
            ...prev,
            questions: shuffleArray(wrongOrUnanswered),
            currentIndex: 0,
            questionStartTime: Date.now(),
          };
        }
      }

      return {
        ...prev,
        currentIndex: Math.min(nextIndex, prev.questions.length - 1),
        questionStartTime: Date.now(),
      };
    });
  }, []);

  const handlePrev = useCallback(() => {
    setActiveQuiz(prev => prev ? { ...prev, currentIndex: Math.max(prev.currentIndex - 1, 0), questionStartTime: Date.now() } : null);
  }, []);

  const handleUpdateTime = useCallback((seconds: number) => {
    setActiveQuiz(prev => prev ? { ...prev, timeRemainingSeconds: seconds } : null);
  }, []);

  const handleQuestionTimeout = useCallback(() => {
    setActiveQuiz(prev => prev ? { ...prev, isPaused: true } : null);
  }, []);

  const handleResume = useCallback(() => {
    setActiveQuiz(prev => prev ? { ...prev, isPaused: false } : null);
  }, []);

  const handleResetQuestionTimer = useCallback(() => {
    setActiveQuiz(prev => prev ? { ...prev, questionStartTime: Date.now() } : null);
  }, []);

  const handleCompleteQuiz = useCallback(() => {
    if (!activeQuiz) return;
    const prevQuiz = activeQuiz;
    const timeUsed = Math.floor((Date.now() - prevQuiz.startTime) / 1000);
    const answers = Object.values(prevQuiz.answers);
    const validAnswers = answers.filter(a => !a.timedOut);
    const correctAnswers = validAnswers.filter(a => a.isCorrect).length;
    const wrongAnswers = validAnswers.filter(a => !a.isCorrect).length;
    const timedOutCount = answers.filter(a => a.timedOut).length;
    const unanswered = prevQuiz.questions.length - answers.length;
    const accuracy = validAnswers.length > 0 ? (correctAnswers / validAnswers.length) * 100 : 0;

    const result: TestResult = {
      id: prevQuiz.id || `test_${Date.now()}`,
      subjectName: prevQuiz.config.subjectName,
      date: new Date().toISOString(),
      timestamp: Date.now(),
      topics: [...new Set(prevQuiz.questions.map(q => q.topic_name))],
      totalQuestions: answers.length,
      correctAnswers,
      wrongAnswers,
      unanswered: unanswered + timedOutCount,
      score: prevQuiz.score,
      maxScore: validAnswers.length * 4,
      accuracy,
      timeLimitSeconds: prevQuiz.config.timeLimitMinutes > 0 ? prevQuiz.config.timeLimitMinutes * 60 : null,
      timeUsedSeconds: timeUsed,
      answers,
      mode: prevQuiz.config.mode,
    };

    setPersistence(prevPersist => {
      let updated = { ...prevPersist, testResults: [...prevPersist.testResults, result] };
      updated = updateHabitLog(updated, validAnswers.length, correctAnswers, prevQuiz.config.subjectName, timeUsed);

      const topicGroups: Record<string, { total: number; attempted: number; correct: number }> = {};
      for (const q of prevQuiz.questions) {
        if (!topicGroups[q.topic_name]) {
          const allInTopic = prevPersist.subjects
            .find(s => s.name === prevQuiz.config.subjectName)
            ?.questions.filter(qq => qq.topic_name === q.topic_name).length || 0;
          topicGroups[q.topic_name] = { total: allInTopic, attempted: 0, correct: 0 };
        }
        const ans = prevQuiz.answers[q.id];
        if (ans && !ans.timedOut) {
          topicGroups[q.topic_name].attempted++;
          if (ans.isCorrect) topicGroups[q.topic_name].correct++;
        }
      }

      for (const [topic, stats] of Object.entries(topicGroups)) {
        updated = updateTopicExpertise(updated, topic, prevQuiz.config.subjectName, stats.total, stats.attempted, stats.correct);
      }

      const questionMap = new Map(prevQuiz.questions.map(q => [q.id, q]));

      for (const ans of validAnswers) {
        if (!ans.isCorrect) {
          const q = questionMap.get(ans.questionId);
          if (q) {
            updated = addToReviewQueue(updated, {
              questionId: q.id,
              subjectName: prevQuiz.config.subjectName,
              topic: q.topic_name,
              question: q.question,
              correctAnswer: q.correct_answer,
              userAnswer: ans.selected as OptionKey | "",
              explanation: q.explanation,
              options: { a: q.option_a, b: q.option_b, c: q.option_c, d: q.option_d },
            });
          }
        }
      }
      return updated;
    });

    saveSession(result, prevQuiz.questions);
    setLastResult(result);
    setLastQuizQuestions(prevQuiz.questions);
    setCurrentView('results');
    setActiveQuiz(null);
  }, [activeQuiz]);

  const handleTimeUp = useCallback(() => {
    handleCompleteQuiz();
  }, [handleCompleteQuiz]);

  const handleRetake = useCallback(() => {
    if (!lastResult) return;
    handleStartQuiz({
      subjectName: lastResult.subjectName,
      topicFilter: 'all',
      questionOrder: 'random',
      questionCount: lastResult.totalQuestions,
      timeLimitMinutes: lastResult.timeLimitSeconds ? lastResult.timeLimitSeconds / 60 : 0,
      questionTimeoutMinutes: 5,
      mode: lastResult.mode,
      useSpacedRepetition: true,
    });
  }, [lastResult, handleStartQuiz]);

  const handlePracticeWrongAnswers = useCallback(() => {
    if (!lastResult) return;
    const wrongIds = lastResult.answers.filter(a => !a.isCorrect && !a.timedOut).map(a => a.questionId);

    setPersistence(prev => {
      const subject = prev.subjects.find(s => s.name === lastResult.subjectName);
      if (!subject || wrongIds.length === 0) return prev;

      const wrongQuestions = subject.questions.filter(q => wrongIds.includes(q.id));
      if (wrongQuestions.length > 0) {
        const now = Date.now();
        setActiveQuiz({
          config: {
            subjectName: lastResult.subjectName,
            topicFilter: 'all',
            questionOrder: 'random',
            questionCount: wrongQuestions.length,
            timeLimitMinutes: 0,
            questionTimeoutMinutes: 5,
            mode: 'learn',
            useSpacedRepetition: true,
          },
          questions: shuffleArray(wrongQuestions),
          currentIndex: 0,
          answers: {},
          startTime: now,
          questionStartTime: now,
          timeRemainingSeconds: null,
          questionTimeRemainingSeconds: null,
          isCompleted: false,
          isPaused: false,
          score: 0,
          correctCount: 0,
          wrongCount: 0,
          targetCorrect: null,
        });
        setLastResult(null);
        setCurrentView('quiz');
      }
      return prev;
    });
  }, [lastResult]);

  const handleExportData = useCallback(() => {
    const json = exportPersistenceJSON();
    const blob = new Blob([json], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `quiz_master_data_${new Date().toISOString().split('T')[0]}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }, []);

  const handleImportData = useCallback((json: string) => {
    try {
      const data = JSON.parse(json) as AppPersistence;
      setPersistence(prev => ({
        ...prev,
        ...data,
        subjects: [
          ...prev.subjects.filter(s => !data.subjects.some(ds => ds.name === s.name)),
          ...data.subjects,
        ],
      }));
    } catch {
      alert('Invalid JSON file.');
    }
  }, []);

  const handleNavigate = useCallback((view: string) => {
    setCurrentView(view);
    if (view !== 'quiz') setActiveQuiz(null);
  }, []);

  const handleGoHome = useCallback(() => {
    setCurrentView('home');
    setActiveQuiz(null);
    setLastResult(null);
  }, []);

  const handleUpdateTargets = useCallback((targets: DailyTargets) => setPersistence(prev => ({ ...prev, dailyTargets: targets })), []);
  const handleUpdateRankConfigs = useCallback((configs: RankSimConfig[]) => setPersistence(prev => ({ ...prev, rankSimConfigs: configs })), []);

  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const mainScrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (mainScrollRef.current) {
      mainScrollRef.current.scrollTop = 0;
    }
  }, [currentView, activeQuiz?.currentIndex, activeSessionId, lastResult]);

  const renderContent = () => {
    if (currentView === 'study-notes' && pendingQuiz) {
      // Show notes for pending quiz topics
      return (
        <StudyNotesView
          sessionId=""
          pendingTopics={pendingQuiz.questions.map(q => q.topic_name)}
          onBack={() => setPendingQuiz(null)}
          onStartQuiz={handleStartPendingQuiz}
        />
      );
    }
    if (currentView === 'study-notes' && activeSessionId) {
      return (
        <StudyNotesView
          sessionId={activeSessionId}
          onBack={() => {
            setActiveSessionId(null);
            setCurrentView('knowledge');
          }}
        />
      );
    }
    if (currentView === 'session-review' && activeSessionId) {
      return (
        <SessionReviewView
          sessionId={activeSessionId}
          onBack={() => {
            setActiveSessionId(null);
            setCurrentView('knowledge');
          }}
        />
      );
    }
    if (currentView === 'quiz' && activeQuiz) {
      return (
        <QuizView
          quiz={activeQuiz}
          onAnswer={handleAnswer}
          onNext={handleNext}
          onPrev={handlePrev}
          onComplete={handleCompleteQuiz}
          onTimeUp={handleTimeUp}
          onUpdateTime={handleUpdateTime}
          onQuestionTimeout={handleQuestionTimeout}
          onResume={handleResume}
          onResetQuestionTimer={handleResetQuestionTimer}
        />
      );
    }
    if (currentView === 'results' && lastResult) {
      return (
        <ResultsView
          result={lastResult}
          questions={lastQuizQuestions}
          topicExpertise={persistence.topicExpertise}
          rankConfigs={persistence.rankSimConfigs || []}
          onRetake={handleRetake}
          onGoHome={handleGoHome}
          onPracticeWrong={handlePracticeWrongAnswers}
          onStudyTopics={() => {
            setActiveSessionId(lastResult.id);
            setCurrentView('study-notes');
          }}
        />
      );
    }
    if (currentView === 'dashboard') return <Dashboard persistence={persistence} onUpdateRankConfigs={handleUpdateRankConfigs} />;
    if (currentView === 'habits') return <HabitTracker persistence={persistence} />;
    if (currentView === 'queue') return <QueueStatus
      persistence={persistence}
      onUpdatePersistence={(updates) => setPersistence(prev => ({ ...prev, ...updates }))}
    />;
    if (currentView === 'expertise') return <ExpertiseMap persistence={persistence} />;
    if (currentView === 'settings') return <SettingsView />;
    if (currentView === 'knowledge') {
      return (
        <KnowledgeHub
          persistence={persistence}
          onNavigate={handleNavigate}
          onStudyTopics={(sessionId) => {
            setActiveSessionId(sessionId);
            setCurrentView('study-notes');
          }}
          onReviewExplanations={(sessionId) => {
            setActiveSessionId(sessionId);
            setCurrentView('session-review');
          }}
          onRetakeSet={(session) => {
            handleStartQuiz({
              subjectName: session.subject_name,
              topicFilter: 'all',
              questionOrder: 'random',
              questionCount: session.total_questions,
              timeLimitMinutes: session.time_limit_seconds ? session.time_limit_seconds / 60 : 0,
              questionTimeoutMinutes: 5,
              mode: session.mode as any,
              useSpacedRepetition: true,
            });
          }}
        />
      );
    }
    return <HomeView persistence={persistence} onNavigate={handleNavigate} onUpdateTargets={handleUpdateTargets} />;
  };

  return (
    <div className="flex h-screen bg-gray-950 overflow-hidden">
      {pendingQuiz && currentView !== 'study-notes' && (
        <PreQuizModal
          config={pendingQuiz.config}
          onStudyFirst={() => setCurrentView('study-notes')}
          onStartQuiz={handleStartPendingQuiz}
          onCancel={() => setPendingQuiz(null)}
        />
      )}
      <button
        onClick={() => setSidebarOpen(!sidebarOpen)}
        className="lg:hidden fixed top-4 left-4 z-50 p-2 bg-gray-800 rounded-lg border border-gray-700 text-gray-300 hover:text-white"
      >
        {sidebarOpen ? <X size={20} /> : <Menu size={20} />}
      </button>

      <button
        onClick={() => setHelpOpen(true)}
        className="fixed top-4 right-4 z-50 p-2 bg-gray-800 rounded-lg border border-gray-700 text-gray-300 hover:text-brand-400 hover:border-brand-500 transition-colors flex items-center gap-2"
      >
        <HelpCircle size={20} />
        <span className="hidden sm:inline text-sm">Help</span>
      </button>

      <HelpModal isOpen={helpOpen} onClose={() => setHelpOpen(false)} />

      {sidebarOpen && (
        <div className="lg:hidden fixed inset-0 bg-black/60 z-30" onClick={() => setSidebarOpen(false)} />
      )}

      <div className={`fixed lg:static h-full z-40 transition-transform duration-300 ${sidebarOpen ? 'translate-x-0' : '-translate-x-full lg:translate-x-0'}`}>
        <Sidebar
          subjects={persistence.subjects}
          onAddSubject={handleAddSubject}
          processingQueueLimit={persistence.processingQueueLimit}
          onStartQuiz={(config) => {
            handleStartQuiz(config);
            setSidebarOpen(false);
          }}
          onNavigate={(view) => {
            handleNavigate(view);
            setSidebarOpen(false);
          }}
          currentView={currentView}
          onExportData={handleExportData}
          onImportData={handleImportData}
        />
      </div>

      <main ref={mainScrollRef} className="flex-1 overflow-y-auto h-full lg:ml-0">
        {renderContent()}
      </main>
    </div>
  );
}
