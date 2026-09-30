import { useState, useEffect, useCallback, useRef } from 'react';
import { Clock, ChevronLeft, ChevronRight, Flag, CheckCircle, XCircle, AlertCircle, Coffee, PlayCircle } from 'lucide-react';
import type { ActiveQuiz, AnswerRecord } from '../types';

interface QuizViewProps {
  quiz: ActiveQuiz;
  onAnswer: (questionId: string, selected: string, timeSpentMs: number, timedOut: boolean) => void;
  onNext: () => void;
  onPrev: () => void;
  onComplete: () => void;
  onTimeUp: () => void;
  onUpdateTime: (seconds: number) => void;
  onQuestionTimeout: () => void;
  onResume: () => void;
  onResetQuestionTimer: () => void;
}

export default function QuizView({
  quiz,
  onAnswer,
  onNext,
  onPrev,
  onComplete,
  onTimeUp,
  onUpdateTime,
  onQuestionTimeout,
  onResume,
  onResetQuestionTimer,
}: QuizViewProps) {
  const [showExplanation, setShowExplanation] = useState(false);
  const [autoAdvanceTimer, setAutoAdvanceTimer] = useState<number | null>(null);
  const [pendingAutoAdvance, setPendingAutoAdvance] = useState(false);

  // Use ref to track the current question to detect changes
  const currentIndexRef = useRef(quiz.currentIndex);
  const autoAdvanceTimerRef = useRef<number | null>(null);
  const countdownRef = useRef<number | null>(null);

  const isTestMode = quiz.config.mode === 'test';
  const isTargetMode = quiz.config.mode === 'target';
  const currentQuestion = quiz.questions[quiz.currentIndex];
  const questionId = currentQuestion?.id;
  const answered = questionId ? quiz.answers[questionId] : undefined;
  const isAnswered = !!answered;
  const totalQuestions = quiz.questions.length;

  // Overall timer logic
  useEffect(() => {
    if (quiz.config.timeLimitMinutes <= 0 || quiz.isCompleted || quiz.isPaused) return;

    const interval = setInterval(() => {
      const elapsed = Math.floor((Date.now() - quiz.startTime) / 1000);
      const totalSeconds = quiz.config.timeLimitMinutes * 60;
      const remaining = totalSeconds - elapsed;

      if (remaining <= 0) {
        onTimeUp();
      } else {
        onUpdateTime(remaining);
      }
    }, 1000);

    return () => clearInterval(interval);
  }, [quiz.config.timeLimitMinutes, quiz.startTime, quiz.isCompleted, quiz.isPaused, onTimeUp, onUpdateTime]);

  // Question timeout logic
  useEffect(() => {
    if (quiz.config.questionTimeoutMinutes <= 0 || quiz.isCompleted || quiz.isPaused || isAnswered) return;

    const interval = setInterval(() => {
      const elapsed = Math.floor((Date.now() - quiz.questionStartTime) / 1000);
      const timeoutSeconds = quiz.config.questionTimeoutMinutes * 60;

      if (elapsed >= timeoutSeconds) {
        onQuestionTimeout();
      }
    }, 1000);

    return () => clearInterval(interval);
  }, [quiz.config.questionTimeoutMinutes, quiz.questionStartTime, quiz.isCompleted, quiz.isPaused, isAnswered, onQuestionTimeout]);

  // Reset states when question changes
  useEffect(() => {
    if (currentIndexRef.current !== quiz.currentIndex) {
      currentIndexRef.current = quiz.currentIndex;
      setShowExplanation(false);
      setPendingAutoAdvance(false);
      setAutoAdvanceTimer(null);

      // Clear any pending timers
      if (autoAdvanceTimerRef.current) {
        clearTimeout(autoAdvanceTimerRef.current);
        autoAdvanceTimerRef.current = null;
      }
      if (countdownRef.current) {
        clearInterval(countdownRef.current);
        countdownRef.current = null;
      }
    }
  }, [quiz.currentIndex]);

  // Auto-advance in Test/Target mode after answering
  useEffect(() => {
    if (!pendingAutoAdvance || quiz.isPaused) return;

    // Clear any existing timers
    if (autoAdvanceTimerRef.current) {
      clearTimeout(autoAdvanceTimerRef.current);
    }
    if (countdownRef.current) {
      clearInterval(countdownRef.current);
    }

    setAutoAdvanceTimer(2);

    // Countdown display
    countdownRef.current = window.setInterval(() => {
      setAutoAdvanceTimer(prev => {
        if (prev === null || prev <= 1) {
          return prev;
        }
        return prev - 1;
      });
    }, 1000);

    // Actual advance after 2 seconds
    autoAdvanceTimerRef.current = window.setTimeout(() => {
      // Clear countdown
      if (countdownRef.current) {
        clearInterval(countdownRef.current);
        countdownRef.current = null;
      }

      // Check what to do next
      if (isTargetMode && quiz.targetCorrect && quiz.correctCount >= quiz.targetCorrect) {
        onComplete();
      } else if (quiz.currentIndex < totalQuestions - 1) {
        onNext();
      } else if (!isTargetMode) {
        onComplete();
      } else {
        // Target mode but ran out of questions
        onComplete();
      }
    }, 2000);

    return () => {
      if (autoAdvanceTimerRef.current) {
        clearTimeout(autoAdvanceTimerRef.current);
        autoAdvanceTimerRef.current = null;
      }
      if (countdownRef.current) {
        clearInterval(countdownRef.current);
        countdownRef.current = null;
      }
    };
  }, [pendingAutoAdvance, quiz.isPaused, isTargetMode, quiz.targetCorrect, quiz.correctCount, quiz.currentIndex, totalQuestions, onNext, onComplete]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (autoAdvanceTimerRef.current) {
        clearTimeout(autoAdvanceTimerRef.current);
      }
      if (countdownRef.current) {
        clearInterval(countdownRef.current);
      }
    };
  }, []);

  const handleOptionClick = useCallback((option: string) => {
    if (isAnswered || quiz.isPaused) return;

    const timeSpentMs = Date.now() - quiz.questionStartTime;
    onAnswer(questionId, option, timeSpentMs, false);

    if (isTestMode || isTargetMode) {
      setPendingAutoAdvance(true);
    } else {
      setShowExplanation(true);
    }
  }, [isAnswered, quiz.isPaused, quiz.questionStartTime, questionId, onAnswer, isTestMode, isTargetMode]);

  const handleNext = useCallback(() => {
    if (quiz.currentIndex < totalQuestions - 1) {
      onNext();
    }
  }, [quiz.currentIndex, totalQuestions, onNext]);

  const handlePrev = useCallback(() => {
    if (quiz.currentIndex > 0) {
      onPrev();
    }
  }, [quiz.currentIndex, onPrev]);

  const handleSkipTimeout = useCallback(() => {
    const timeSpentMs = Date.now() - quiz.questionStartTime;
    onAnswer(questionId, '', timeSpentMs, true);
    onResume();
    if (quiz.currentIndex < totalQuestions - 1) {
      onNext();
    }
  }, [quiz.questionStartTime, questionId, onAnswer, onResume, onNext, quiz.currentIndex, totalQuestions]);

  if (!currentQuestion) return null;

  // Pause screen
  if (quiz.isPaused) {
    return (
      <div className="max-w-2xl mx-auto p-6 animate-fade-in">
        <div className="bg-gray-800/60 border border-warning-500/30 rounded-2xl p-8 text-center">
          <Coffee size={64} className="mx-auto text-warning-500 mb-4" />
          <h2 className="text-2xl font-bold text-gray-100 mb-2">Need a Break?</h2>
          <p className="text-gray-400 mb-6">
            No answer was given for {quiz.config.questionTimeoutMinutes} minutes.
            Take a moment if you need to - your progress is saved.
          </p>

          <div className="flex flex-col gap-3">
            <button
              onClick={() => {
                onResume();
                onResetQuestionTimer();
              }}
              className="w-full py-3 bg-brand-600 text-white font-bold rounded-xl hover:bg-brand-500 transition-all flex items-center justify-center gap-2"
            >
              <PlayCircle size={20} />
              Continue Quiz
            </button>
            <button
              onClick={handleSkipTimeout}
              className="w-full py-3 bg-gray-700 text-gray-300 font-medium rounded-xl hover:bg-gray-600 transition-all"
            >
              Skip This Question
            </button>
            <button
              onClick={onComplete}
              className="w-full py-2 text-gray-500 hover:text-gray-300 transition-colors text-sm"
            >
              End Quiz & See Results
            </button>
          </div>
        </div>
      </div>
    );
  }

  const options: { key: string; text: string }[] = [
    { key: 'A', text: currentQuestion.option_a },
    { key: 'B', text: currentQuestion.option_b },
    { key: 'C', text: currentQuestion.option_c },
    { key: 'D', text: currentQuestion.option_d },
  ];

  const correctAnswer = currentQuestion.correct_answer;

  const getOptionStyle = (optKey: string): string => {
    if (!isAnswered) {
      return 'bg-gray-800 border-gray-600 hover:border-brand-400 hover:bg-gray-750 cursor-pointer';
    }

    const ans = answered as AnswerRecord;
    if (ans.timedOut) {
      return 'bg-gray-800/50 border-gray-700 text-gray-500';
    }
    if (optKey === correctAnswer) {
      return 'bg-success-50/10 border-success-500 text-success-500';
    }
    if (optKey === ans.selected && !ans.isCorrect) {
      return 'bg-danger-50/10 border-danger-500 text-danger-500';
    }
    return 'bg-gray-800/50 border-gray-700 text-gray-500';
  };

  const getOptionIcon = (optKey: string) => {
    if (!isAnswered) return null;
    const ans = answered as AnswerRecord;
    if (ans.timedOut) return null;
    if (optKey === correctAnswer) return <CheckCircle size={20} className="text-success-500" />;
    if (optKey === ans.selected && !ans.isCorrect) return <XCircle size={20} className="text-danger-500" />;
    return null;
  };

  const formatTime = (seconds: number) => {
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
  };

  const timeRemaining = quiz.timeRemainingSeconds;
  const isTimeLow = timeRemaining !== null && timeRemaining < 60;

  // Progress
  const answeredCount = Object.keys(quiz.answers).length;
  const progressPercent = isTargetMode && quiz.targetCorrect
    ? (quiz.correctCount / quiz.targetCorrect) * 100
    : (answeredCount / totalQuestions) * 100;

  // Time spent on current question
  const currentQuestionTime = Math.floor((Date.now() - quiz.questionStartTime) / 1000);

  // Determine if showing feedback in test/target mode
  const showingFeedback = (isTestMode || isTargetMode) && isAnswered && pendingAutoAdvance;

  return (
    <div className="max-w-4xl mx-auto p-6 animate-fade-in">
      {/* Top Bar */}
      <div className="flex items-center justify-between mb-6 flex-wrap gap-3">
        <div className="flex items-center gap-4">
          <div className="bg-gray-800 rounded-xl px-4 py-2 flex items-center gap-2">
            <span className="text-brand-400 font-bold text-lg">{quiz.currentIndex + 1}</span>
            <span className="text-gray-500">/</span>
            <span className="text-gray-400">{isTargetMode ? '∞' : totalQuestions}</span>
          </div>

          <div className="flex items-center gap-3 text-sm">
            <span className="text-success-500 flex items-center gap-1">
              <CheckCircle size={14} /> {quiz.correctCount}
              {isTargetMode && quiz.targetCorrect && (
                <span className="text-gray-500">/{quiz.targetCorrect}</span>
              )}
            </span>
            <span className="text-danger-500 flex items-center gap-1">
              <XCircle size={14} /> {quiz.wrongCount}
            </span>
            <span className="text-gray-400">
              Score: <span className="text-brand-400 font-semibold">{quiz.score}</span>
            </span>
          </div>
        </div>

        <div className="flex items-center gap-3">
          {/* Mode indicator */}
          <span className={`px-3 py-1 rounded-full text-xs font-medium ${
            isTestMode ? 'bg-brand-500/20 text-brand-400' :
            isTargetMode ? 'bg-purple-500/20 text-purple-400' :
            'bg-success-500/20 text-success-400'
          }`}>
            {isTestMode ? '📝 Test' : isTargetMode ? '🎯 Target' : '📖 Learn'}
          </span>

          {/* Question time */}
          {!isAnswered && (
            <div className="bg-gray-800 rounded-lg px-3 py-1 text-xs text-gray-400">
              ⏱️ {formatTime(currentQuestionTime)}
            </div>
          )}

          {timeRemaining !== null && (
            <div className={`flex items-center gap-2 px-4 py-2 rounded-xl font-mono text-lg font-bold ${
              isTimeLow ? 'bg-danger-500/20 text-danger-500 animate-pulse' : 'bg-gray-800 text-gray-300'
            }`}>
              <Clock size={18} />
              {formatTime(timeRemaining)}
            </div>
          )}
        </div>
      </div>

      {/* Progress Bar */}
      <div className="w-full bg-gray-800 rounded-full h-2 mb-6">
        <div
          className={`h-2 rounded-full transition-all duration-500 ${
            isTargetMode ? 'bg-gradient-to-r from-purple-500 to-success-500' : 'bg-gradient-to-r from-brand-500 to-purple-500'
          }`}
          style={{ width: `${Math.min(progressPercent, 100)}%` }}
        />
      </div>

      {/* Target mode progress */}
      {isTargetMode && quiz.targetCorrect && (
        <div className="text-center mb-4">
          <p className="text-sm text-gray-400">
            {quiz.correctCount >= quiz.targetCorrect ? (
              <span className="text-success-500 font-bold">🎉 Target reached! Finishing up...</span>
            ) : (
              <>Get <span className="text-purple-400 font-bold">{quiz.targetCorrect - quiz.correctCount}</span> more correct to complete!</>
            )}
          </p>
        </div>
      )}

      {/* Question Card */}
      <div className="bg-gray-800/60 backdrop-blur-sm rounded-2xl border border-gray-700 p-8 mb-6">
        <div className="flex items-center gap-2 mb-4">
          <span className="px-3 py-1 bg-brand-600/20 text-brand-400 rounded-full text-xs font-medium">
            {currentQuestion.topic_name}
          </span>
          {quiz.config.useSpacedRepetition && (
            <span className="px-3 py-1 bg-purple-600/20 text-purple-400 rounded-full text-xs">
              🧠 SR
            </span>
          )}
        </div>

        <h2 className="text-xl font-semibold text-gray-100 leading-relaxed mb-8">
          {currentQuestion.question}
        </h2>

        {/* Options */}
        <div className="space-y-3">
          {options.map(opt => (
            <button
              key={opt.key}
              onClick={() => handleOptionClick(opt.key)}
              disabled={isAnswered}
              className={`option-btn w-full text-left px-5 py-4 rounded-xl border-2 transition-all flex items-center justify-between ${getOptionStyle(opt.key)}`}
            >
              <div className="flex items-center gap-4">
                <span className={`w-8 h-8 rounded-lg flex items-center justify-center text-sm font-bold ${
                  isAnswered && !answered?.timedOut && opt.key === correctAnswer
                    ? 'bg-success-500 text-white'
                    : isAnswered && answered && opt.key === (answered as AnswerRecord).selected && !(answered as AnswerRecord).isCorrect
                    ? 'bg-danger-500 text-white'
                    : 'bg-gray-700 text-gray-300'
                }`}>
                  {opt.key}
                </span>
                <span className="text-sm">{opt.text}</span>
              </div>
              {getOptionIcon(opt.key)}
            </button>
          ))}
        </div>
      </div>

      {/* Test/Target Mode: Auto-advance indicator */}
      {showingFeedback && (
        <div className="animate-fade-in bg-gray-800/60 border border-gray-700 rounded-xl p-4 mb-6 text-center">
          <p className="text-gray-400 text-sm">
            {answered?.timedOut ? '⏭️ Skipped (timed out)' :
             answered?.isCorrect ? '✅ Correct!' : '❌ Wrong! The correct answer was ' + correctAnswer}
            {answered && !answered.timedOut && answered.timeSpentMs && (
              <span className="text-gray-500 ml-2">
                ({(answered.timeSpentMs / 1000).toFixed(1)}s)
              </span>
            )}
            <span className="text-gray-500 ml-2">
              {isTargetMode && quiz.targetCorrect && quiz.correctCount >= quiz.targetCorrect
                ? 'Target reached!'
                : `Next in ${autoAdvanceTimer || 0}s...`}
            </span>
          </p>
        </div>
      )}

      {/* Learn Mode: Explanation (with HTML support) */}
      {!isTestMode && !isTargetMode && isAnswered && showExplanation && (
        <div className="animate-fade-in bg-brand-600/10 border border-brand-500/30 rounded-2xl p-6 mb-6">
          <div className="flex items-start gap-3">
            <AlertCircle size={20} className="text-brand-400 mt-0.5 flex-shrink-0" />
            <div>
              <h4 className="font-semibold text-brand-400 mb-2">
                Explanation
                <span className="text-gray-500 text-sm font-normal ml-2">
                  (answered in {((answered?.timeSpentMs || 0) / 1000).toFixed(1)}s)
                </span>
              </h4>
              <div
                className="text-gray-300 text-sm leading-relaxed explanation-html"
                dangerouslySetInnerHTML={{ __html: currentQuestion.explanation }}
              />
            </div>
          </div>
        </div>
      )}

      {/* Navigation - Only show in Learn mode or when not auto-advancing */}
      {!isTestMode && !isTargetMode && (
        <div className="flex items-center justify-between">
          <button
            onClick={handlePrev}
            disabled={quiz.currentIndex === 0}
            className="flex items-center gap-2 px-5 py-2.5 bg-gray-800 text-gray-300 rounded-xl
                       hover:bg-gray-700 transition-all disabled:opacity-30 disabled:cursor-not-allowed text-sm"
          >
            <ChevronLeft size={16} /> Previous
          </button>

          <div className="flex gap-2">
            {totalQuestions <= 20 ? (
              quiz.questions.map((q, i) => {
                const a = quiz.answers[q.id];
                return (
                  <div
                    key={i}
                    className={`w-3 h-3 rounded-full transition-all ${
                      i === quiz.currentIndex
                        ? 'bg-brand-500 scale-125'
                        : a
                        ? a.timedOut
                          ? 'bg-gray-500'
                          : a.isCorrect
                          ? 'bg-success-500'
                          : 'bg-danger-500'
                        : 'bg-gray-700'
                    }`}
                  />
                );
              })
            ) : (
              <span className="text-xs text-gray-500">
                {answeredCount} of {totalQuestions} answered
              </span>
            )}
          </div>

          {quiz.currentIndex < totalQuestions - 1 ? (
            <button
              onClick={handleNext}
              disabled={!isAnswered}
              className="flex items-center gap-2 px-5 py-2.5 bg-brand-600 text-white rounded-xl
                         hover:bg-brand-500 transition-all disabled:opacity-40 disabled:cursor-not-allowed text-sm"
            >
              Next <ChevronRight size={16} />
            </button>
          ) : (
            <button
              onClick={onComplete}
              disabled={!isAnswered}
              className="flex items-center gap-2 px-5 py-2.5 bg-gradient-to-r from-success-600 to-success-500 text-white rounded-xl
                         hover:from-success-500 hover:to-success-600 transition-all disabled:opacity-40 disabled:cursor-not-allowed text-sm font-semibold"
            >
              <Flag size={16} /> Complete
            </button>
          )}
        </div>
      )}
    </div>
  );
}
