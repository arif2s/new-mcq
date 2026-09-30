import { useState, useCallback, useEffect } from 'react';
import { Menu, X, HelpCircle } from 'lucide-react';
import Sidebar from './components/Sidebar';
import HomeView from './components/HomeView';
import QuizView from './components/QuizView';
import ResultsView from './components/ResultsView';
import Dashboard from './components/Dashboard';
import HabitTracker from './components/HabitTracker';
import ExpertiseMap from './components/ExpertiseMap';
import HelpModal from './components/HelpModal';
import ReviewQueue from './components/ReviewQueue';
import {
  loadPersistence,
  savePersistence,
  updateHabitLog,
  updateAnkiCard,
  getAnkiDueQuestions,
  updateTopicExpertise,
  exportPersistenceJSON,
  addToReviewQueue,
  markReviewItemDone,
  removeReviewItem,
  clearAllReviewQueue,
} from './storage';
import type {
  AppPersistence,
  QuizConfig,
  ActiveQuiz,
  QuizQuestion,
  SubjectData,
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
  const [lastResult, setLastResult] = useState<TestResult | null>(null);
  const [lastQuizQuestions, setLastQuizQuestions] = useState<QuizQuestion[]>([]);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);

  // Save persistence whenever it changes
  useEffect(() => {
    savePersistence(persistence);
  }, [persistence]);

  // Add a subject
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

      const updated = { ...prev };
      if (existing >= 0) {
        updated.subjects = [...prev.subjects];
        updated.subjects[existing] = subjectData;
      } else {
        updated.subjects = [...prev.subjects, subjectData];
      }
      return updated;
    });
  }, []);

  // Remove a subject
  const handleRemoveSubject = useCallback((name: string) => {
    setPersistence(prev => ({
      ...prev,
      subjects: prev.subjects.filter(s => s.name !== name),
      ankiCards: prev.ankiCards.filter(c => c.subjectName !== name),
      topicExpertise: prev.topicExpertise.filter(e => e.subject !== name),
    }));
  }, []);

  // Start a quiz
  const handleStartQuiz = useCallback((config: QuizConfig) => {
    const subject = persistence.subjects.find(s => s.name === config.subjectName);
    if (!subject) return;

    let pool = [...subject.questions];

    // Filter by topic
    if (config.topicFilter !== 'all') {
      pool = pool.filter(q => q.topic_name === config.topicFilter);
    }

    // For spaced repetition, prioritize due cards and new cards
    if (config.useSpacedRepetition) {
      const allIds = pool.map(q => q.id);
      const { dueForReview, newQuestions } = getAnkiDueQuestions(persistence, config.subjectName, allIds);

      const duePool = pool.filter(q => dueForReview.includes(q.id));
      const newPool = pool.filter(q => newQuestions.includes(q.id));
      const combined = [...shuffleArray(duePool), ...shuffleArray(newPool)];

      pool = combined.length > 0 ? combined : pool;
    }

    // Randomize or keep sequential
    if (config.questionOrder === 'random') {
      pool = shuffleArray(pool);
    }

    // For target mode, we need more questions than the target count
    // For regular modes, limit to question count
    if (config.mode === 'target') {
      // Keep all questions for target mode - we'll keep going until target correct
      // But shuffle them for variety
      pool = shuffleArray(pool);
    } else {
      pool = pool.slice(0, config.questionCount);
    }

    if (pool.length === 0) return;

    const now = Date.now();
    const quiz: ActiveQuiz = {
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

    setActiveQuiz(quiz);
    setLastResult(null);
    setCurrentView('quiz');
  }, [persistence]);

  // Handle answer with timing
  const handleAnswer = useCallback((questionId: string, selected: string, timeSpentMs: number, timedOut: boolean) => {
    setActiveQuiz(prev => {
      if (!prev || prev.answers[questionId]) return prev;

      const question = prev.questions.find(q => q.id === questionId);
      if (!question) return prev;

      const isCorrect = !timedOut && selected === question.correct_answer;
      const answer: AnswerRecord = {
        questionId,
        selected,
        isCorrect,
        timestamp: Date.now(),
        timeSpentMs,
        timedOut,
      };

      const newScore = prev.score + (timedOut ? 0 : isCorrect ? 4 : -1);
      const newCorrect = prev.correctCount + (isCorrect ? 1 : 0);
      const newWrong = prev.wrongCount + (!timedOut && !isCorrect ? 1 : 0);

      const updated = {
        ...prev,
        answers: { ...prev.answers, [questionId]: answer },
        score: newScore,
        correctCount: newCorrect,
        wrongCount: newWrong,
        questionStartTime: Date.now(), // Reset for next question
      };

      return updated;
    });

    // Auto-save after each answer
    if (activeQuiz) {
      const question = activeQuiz.questions.find(q => q.id === questionId);
      if (question) {
        const isCorrect = !timedOut && selected === question.correct_answer;
        setPersistence(prev => {
          let updated = updateAnkiCard(prev, questionId, activeQuiz.config.subjectName, question.topic_name, isCorrect);
          return updated;
        });
      }
    }
  }, [activeQuiz]);

  // Navigate questions
  const handleNext = useCallback(() => {
    setActiveQuiz(prev => {
      if (!prev) return prev;

      // In target mode, if we've used all questions but haven't hit target, cycle back
      let nextIndex = prev.currentIndex + 1;
      if (prev.config.mode === 'target' && nextIndex >= prev.questions.length) {
        // Shuffle and restart from beginning (excluding already answered correctly)
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
    setActiveQuiz(prev => {
      if (!prev) return prev;
      return {
        ...prev,
        currentIndex: Math.max(prev.currentIndex - 1, 0),
        questionStartTime: Date.now(),
      };
    });
  }, []);

  // Update timer
  const handleUpdateTime = useCallback((seconds: number) => {
    setActiveQuiz(prev => {
      if (!prev) return prev;
      return { ...prev, timeRemainingSeconds: seconds };
    });
  }, []);

  // Question timeout - pause quiz
  const handleQuestionTimeout = useCallback(() => {
    setActiveQuiz(prev => {
      if (!prev) return prev;
      return { ...prev, isPaused: true };
    });
  }, []);

  // Resume from pause
  const handleResume = useCallback(() => {
    setActiveQuiz(prev => {
      if (!prev) return prev;
      return { ...prev, isPaused: false };
    });
  }, []);

  // Reset question timer
  const handleResetQuestionTimer = useCallback(() => {
    setActiveQuiz(prev => {
      if (!prev) return prev;
      return { ...prev, questionStartTime: Date.now() };
    });
  }, []);

  // Complete quiz
  const handleCompleteQuiz = useCallback(() => {
    if (!activeQuiz) return;

    const timeUsed = Math.floor((Date.now() - activeQuiz.startTime) / 1000);
    const answers = Object.values(activeQuiz.answers);
    const validAnswers = answers.filter(a => !a.timedOut);
    const correctAnswers = validAnswers.filter(a => a.isCorrect).length;
    const wrongAnswers = validAnswers.filter(a => !a.isCorrect).length;
    const timedOutCount = answers.filter(a => a.timedOut).length;
    const unanswered = activeQuiz.questions.length - answers.length;
    const accuracy = validAnswers.length > 0 ? (correctAnswers / validAnswers.length) * 100 : 0;

    const result: TestResult = {
      id: `test_${Date.now()}`,
      subjectName: activeQuiz.config.subjectName,
      date: new Date().toISOString(),
      timestamp: Date.now(),
      topics: [...new Set(activeQuiz.questions.map(q => q.topic_name))],
      totalQuestions: answers.length,
      correctAnswers,
      wrongAnswers,
      unanswered: unanswered + timedOutCount,
      score: activeQuiz.score,
      maxScore: validAnswers.length * 4,
      accuracy,
      timeLimitSeconds: activeQuiz.config.timeLimitMinutes > 0 ? activeQuiz.config.timeLimitMinutes * 60 : null,
      timeUsedSeconds: timeUsed,
      answers,
      mode: activeQuiz.config.mode,
    };

    // Update persistence with final stats
    setPersistence(prev => {
      let updated = {
        ...prev,
        testResults: [...prev.testResults, result],
      };

      // Update habit log
      updated = updateHabitLog(updated, validAnswers.length, correctAnswers, activeQuiz.config.subjectName, timeUsed);

      // Update topic expertise
      const topicGroups: Record<string, { total: number; attempted: number; correct: number }> = {};
      for (const q of activeQuiz.questions) {
        if (!topicGroups[q.topic_name]) {
          const allInTopic = persistence.subjects
            .find(s => s.name === activeQuiz.config.subjectName)
            ?.questions.filter(qq => qq.topic_name === q.topic_name).length || 0;
          topicGroups[q.topic_name] = { total: allInTopic, attempted: 0, correct: 0 };
        }
        const ans = activeQuiz.answers[q.id];
        if (ans && !ans.timedOut) {
          topicGroups[q.topic_name].attempted++;
          if (ans.isCorrect) topicGroups[q.topic_name].correct++;
        }
      }

      for (const [topic, stats] of Object.entries(topicGroups)) {
        updated = updateTopicExpertise(
          updated,
          topic,
          activeQuiz.config.subjectName,
          stats.total,
          stats.attempted,
          stats.correct
        );
      }

      // Add wrong answers to review queue
      for (const ans of validAnswers.filter(a => !a.isCorrect)) {
        const q = activeQuiz.questions.find(qq => qq.id === ans.questionId);
        if (q) {
          updated = addToReviewQueue(updated, {
            questionId: q.id,
            subjectName: activeQuiz.config.subjectName,
            topic: q.topic_name,
            question: q.question,
            correctAnswer: q.correct_answer,
            userAnswer: ans.selected,
            explanation: q.explanation,
            options: {
              a: q.option_a,
              b: q.option_b,
              c: q.option_c,
              d: q.option_d,
            },
          });
        }
      }

      return updated;
    });

    setLastResult(result);
    setLastQuizQuestions(activeQuiz.questions);
    setActiveQuiz(null);
    setCurrentView('results');
  }, [activeQuiz, persistence.subjects]);

  // Time up handler
  const handleTimeUp = useCallback(() => {
    handleCompleteQuiz();
  }, [handleCompleteQuiz]);

  // Retake
  const handleRetake = useCallback(() => {
    if (!lastResult) return;
    const subject = persistence.subjects.find(s => s.name === lastResult.subjectName);
    if (!subject) return;

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
  }, [lastResult, persistence.subjects, handleStartQuiz]);

  // Practice wrong answers
  const handlePracticeWrongAnswers = useCallback(() => {
    if (!lastResult) return;
    const wrongIds = lastResult.answers.filter(a => !a.isCorrect && !a.timedOut).map(a => a.questionId);
    const subject = persistence.subjects.find(s => s.name === lastResult.subjectName);
    if (!subject || wrongIds.length === 0) return;

    const wrongQuestions = subject.questions.filter(q => wrongIds.includes(q.id));
    if (wrongQuestions.length === 0) return;

    const now = Date.now();
    const quiz: ActiveQuiz = {
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
    };

    setActiveQuiz(quiz);
    setLastResult(null);
    setCurrentView('quiz');
  }, [lastResult, persistence.subjects]);

  // Export data
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

  // Import data
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

  // Navigate
  const handleNavigate = useCallback((view: string) => {
    setCurrentView(view);
    if (view !== 'quiz') {
      setActiveQuiz(null);
    }
  }, []);

  // Go home
  const handleGoHome = useCallback(() => {
    setCurrentView('home');
    setActiveQuiz(null);
    setLastResult(null);
  }, []);

  // Review queue handlers
  const handleMarkReviewed = useCallback((itemId: string) => {
    setPersistence(prev => markReviewItemDone(prev, itemId));
  }, []);

  const handleRemoveReviewItem = useCallback((itemId: string) => {
    setPersistence(prev => removeReviewItem(prev, itemId));
  }, []);

  const handleClearReviewQueue = useCallback(() => {
    setPersistence(prev => clearAllReviewQueue(prev));
  }, []);

  const handleUpdateReviewLimit = useCallback((limit: number) => {
    setPersistence(prev => ({ ...prev, reviewQueueLimit: limit }));
  }, []);

  // Update daily targets
  const handleUpdateTargets = useCallback((targets: DailyTargets) => {
    setPersistence(prev => ({ ...prev, dailyTargets: targets }));
  }, []);

  // Update rank sim configs
  const handleUpdateRankConfigs = useCallback((configs: RankSimConfig[]) => {
    setPersistence(prev => ({ ...prev, rankSimConfigs: configs }));
  }, []);

  // Render main content
  const renderContent = () => {
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
        />
      );
    }

    if (currentView === 'dashboard') {
      return <Dashboard persistence={persistence} onUpdateRankConfigs={handleUpdateRankConfigs} />;
    }

    if (currentView === 'habits') {
      return <HabitTracker persistence={persistence} />;
    }

    if (currentView === 'expertise') {
      return <ExpertiseMap persistence={persistence} />;
    }

    if (currentView === 'review') {
      return (
        <ReviewQueue
          persistence={persistence}
          onMarkReviewed={handleMarkReviewed}
          onRemoveItem={handleRemoveReviewItem}
          onClearAll={handleClearReviewQueue}
          onUpdateLimit={handleUpdateReviewLimit}
        />
      );
    }

    return <HomeView persistence={persistence} onNavigate={handleNavigate} onUpdateTargets={handleUpdateTargets} />;
  };

  return (
    <div className="flex min-h-screen bg-gray-950">
      {/* Mobile menu button */}
      <button
        onClick={() => setSidebarOpen(!sidebarOpen)}
        className="lg:hidden fixed top-4 left-4 z-50 p-2 bg-gray-800 rounded-lg border border-gray-700 text-gray-300 hover:text-white"
      >
        {sidebarOpen ? <X size={20} /> : <Menu size={20} />}
      </button>

      {/* Help button - top right */}
      <button
        onClick={() => setHelpOpen(true)}
        className="fixed top-4 right-4 z-50 p-2 bg-gray-800 rounded-lg border border-gray-700 text-gray-300 hover:text-brand-400 hover:border-brand-500 transition-colors flex items-center gap-2"
      >
        <HelpCircle size={20} />
        <span className="hidden sm:inline text-sm">Help</span>
      </button>

      {/* Help Modal */}
      <HelpModal isOpen={helpOpen} onClose={() => setHelpOpen(false)} />

      {/* Sidebar overlay on mobile */}
      {sidebarOpen && (
        <div
          className="lg:hidden fixed inset-0 bg-black/60 z-30"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* Sidebar */}
      <div className={`
        fixed lg:static z-40 transition-transform duration-300
        ${sidebarOpen ? 'translate-x-0' : '-translate-x-full lg:translate-x-0'}
      `}>
        <Sidebar
          subjects={persistence.subjects}
          onAddSubject={handleAddSubject}
          onRemoveSubject={handleRemoveSubject}
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

      <main className="flex-1 overflow-y-auto min-h-screen lg:ml-0">
        {renderContent()}
      </main>
    </div>
  );
}
