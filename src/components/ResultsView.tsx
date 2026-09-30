import { Trophy, Target, CheckCircle, XCircle, RotateCcw, BookOpen, TrendingUp, AlertTriangle, Clock, Brain } from 'lucide-react';
import type { TestResult, QuizQuestion, TopicExpertise, RankSimConfig } from '../types';
import { simulateRank } from '../storage';
import RankSimCard from './RankSimCard';

interface ResultsViewProps {
  result: TestResult;
  questions: QuizQuestion[];
  topicExpertise: TopicExpertise[];
  rankConfigs: RankSimConfig[];
  onRetake: () => void;
  onGoHome: () => void;
  onPracticeWrong: () => void;
}

export default function ResultsView({
  result,
  questions,
  topicExpertise,
  rankConfigs,
  onRetake,
  onGoHome,
  onPracticeWrong,
}: ResultsViewProps) {
  const accuracy = result.accuracy;
  const grade =
    accuracy >= 90 ? { label: 'A+', color: 'text-success-500', bg: 'bg-success-500/20' } :
    accuracy >= 80 ? { label: 'A', color: 'text-success-500', bg: 'bg-success-500/20' } :
    accuracy >= 70 ? { label: 'B', color: 'text-brand-400', bg: 'bg-brand-500/20' } :
    accuracy >= 60 ? { label: 'C', color: 'text-warning-500', bg: 'bg-warning-500/20' } :
    accuracy >= 50 ? { label: 'D', color: 'text-warning-600', bg: 'bg-warning-500/20' } :
    { label: 'F', color: 'text-danger-500', bg: 'bg-danger-500/20' };

  // Calculate timing stats (excluding timed out questions)
  const validAnswers = result.answers.filter(a => !a.timedOut);
  const timings = validAnswers.map(a => a.timeSpentMs || 0).filter(t => t > 0);
  const avgTimeMs = timings.length > 0 ? timings.reduce((a, b) => a + b, 0) / timings.length : 0;
  const minTimeMs = timings.length > 0 ? Math.min(...timings) : 0;
  const maxTimeMs = timings.length > 0 ? Math.max(...timings) : 0;

  // Distraction analysis - detect when response times started increasing
  const distractionAnalysis = analyzeDistraction(validAnswers);

  // Topic-level analysis
  const topicStats: Record<string, { total: number; correct: number; wrong: number; avgTime: number }> = {};
  for (const answer of result.answers) {
    if (answer.timedOut) continue;
    const q = questions.find(qq => qq.id === answer.questionId);
    if (!q) continue;
    if (!topicStats[q.topic_name]) {
      topicStats[q.topic_name] = { total: 0, correct: 0, wrong: 0, avgTime: 0 };
    }
    topicStats[q.topic_name].total++;
    topicStats[q.topic_name].avgTime += (answer.timeSpentMs || 0);
    if (answer.isCorrect) topicStats[q.topic_name].correct++;
    else topicStats[q.topic_name].wrong++;
  }
  // Calculate average times
  for (const topic of Object.keys(topicStats)) {
    if (topicStats[topic].total > 0) {
      topicStats[topic].avgTime /= topicStats[topic].total;
    }
  }

  const weakTopics = Object.entries(topicStats)
    .filter(([_, s]) => s.total > 0 && (s.correct / s.total) < 0.7)
    .sort((a, b) => (a[1].correct / a[1].total) - (b[1].correct / b[1].total));

  const strongTopics = Object.entries(topicStats)
    .filter(([_, s]) => s.total > 0 && (s.correct / s.total) >= 0.7)
    .sort((a, b) => (b[1].correct / b[1].total) - (a[1].correct / a[1].total));

  const wrongAnswers = result.answers.filter(a => !a.isCorrect && !a.timedOut);
  const timedOutCount = result.answers.filter(a => a.timedOut).length;

  const formatTime = (ms: number) => {
    if (ms < 1000) return `${Math.round(ms)}ms`;
    if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
    const mins = Math.floor(ms / 60000);
    const secs = Math.round((ms % 60000) / 1000);
    return `${mins}m ${secs}s`;
  };

  return (
    <div className="max-w-4xl mx-auto p-6 animate-fade-in">
      {/* Hero */}
      <div className="text-center mb-8">
        <div className={`inline-flex items-center justify-center w-24 h-24 rounded-full ${grade.bg} mb-4`}>
          <span className={`text-4xl font-black ${grade.color}`}>{grade.label}</span>
        </div>
        <h1 className="text-3xl font-bold text-gray-100 mb-2">
          {accuracy >= 70 ? '🎉 Great Job!' : accuracy >= 50 ? '💪 Keep Practicing!' : '📚 Time to Study!'}
        </h1>
        <p className="text-gray-400">
          {result.mode === 'learn' ? 'Learning Session' : result.mode === 'target' ? 'Target Mode' : 'Test'} completed for <span className="text-brand-400 font-medium">{result.subjectName}</span>
        </p>
      </div>

      {/* Score Cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
        <div className="bg-gray-800 rounded-xl p-4 text-center border border-gray-700">
          <Trophy size={24} className="mx-auto text-warning-500 mb-2" />
          <p className="text-2xl font-bold text-gray-100">{result.score}</p>
          <p className="text-xs text-gray-400">Score</p>
        </div>
        <div className="bg-gray-800 rounded-xl p-4 text-center border border-gray-700">
          <Target size={24} className="mx-auto text-brand-400 mb-2" />
          <p className="text-2xl font-bold text-gray-100">{accuracy.toFixed(1)}%</p>
          <p className="text-xs text-gray-400">Accuracy</p>
        </div>
        <div className="bg-gray-800 rounded-xl p-4 text-center border border-gray-700">
          <CheckCircle size={24} className="mx-auto text-success-500 mb-2" />
          <p className="text-2xl font-bold text-gray-100">{result.correctAnswers}</p>
          <p className="text-xs text-gray-400">Correct</p>
        </div>
        <div className="bg-gray-800 rounded-xl p-4 text-center border border-gray-700">
          <XCircle size={24} className="mx-auto text-danger-500 mb-2" />
          <p className="text-2xl font-bold text-gray-100">{result.wrongAnswers}</p>
          <p className="text-xs text-gray-400">Wrong</p>
        </div>
      </div>

      {/* Rank Simulation */}
      {rankConfigs.length > 0 && result.totalQuestions > 0 && (
        <div className="mb-8">
          {rankConfigs.map((config, i) => {
            const sim = simulateRank(config, result.correctAnswers, result.wrongAnswers, result.totalQuestions);
            return (
              <RankSimCard
                key={i}
                config={config}
                sim={sim}
                testQuestions={result.totalQuestions}
                correct={result.correctAnswers}
                wrong={result.wrongAnswers}
              />
            );
          })}
        </div>
      )}

      {/* Timing Stats */}
      <div className="bg-gray-800/60 rounded-xl p-6 border border-gray-700 mb-8">
        <h3 className="text-lg font-semibold text-gray-200 mb-4 flex items-center gap-2">
          <Clock size={18} className="text-brand-400" />
          Time Analysis
        </h3>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
          <div className="bg-gray-900/50 rounded-lg p-3 text-center">
            <p className="text-xl font-bold text-brand-400">{formatTime(avgTimeMs)}</p>
            <p className="text-xs text-gray-400">Avg per Question</p>
          </div>
          <div className="bg-gray-900/50 rounded-lg p-3 text-center">
            <p className="text-xl font-bold text-success-500">{formatTime(minTimeMs)}</p>
            <p className="text-xs text-gray-400">Fastest</p>
          </div>
          <div className="bg-gray-900/50 rounded-lg p-3 text-center">
            <p className="text-xl font-bold text-warning-500">{formatTime(maxTimeMs)}</p>
            <p className="text-xs text-gray-400">Slowest</p>
          </div>
          <div className="bg-gray-900/50 rounded-lg p-3 text-center">
            <p className="text-xl font-bold text-gray-300">{Math.floor(result.timeUsedSeconds / 60)}m {result.timeUsedSeconds % 60}s</p>
            <p className="text-xs text-gray-400">Total Time</p>
          </div>
        </div>

        {timedOutCount > 0 && (
          <p className="text-xs text-warning-500 mt-2">
            ⏰ {timedOutCount} question(s) were skipped due to timeout and excluded from timing stats.
          </p>
        )}
      </div>

      {/* Distraction Analysis */}
      {distractionAnalysis.detected && (
        <div className="bg-warning-500/10 border border-warning-500/30 rounded-xl p-6 mb-8">
          <h3 className="text-lg font-semibold text-warning-500 mb-3 flex items-center gap-2">
            <Brain size={18} />
            Focus Analysis
          </h3>
          <p className="text-gray-300 text-sm mb-3">
            {distractionAnalysis.message}
          </p>
          <div className="bg-gray-900/50 rounded-lg p-4">
            <div className="grid grid-cols-2 gap-4 text-sm">
              <div>
                <p className="text-gray-400">First {distractionAnalysis.beforeCount} questions:</p>
                <p className="text-success-500 font-medium">{formatTime(distractionAnalysis.avgBefore)} avg</p>
              </div>
              <div>
                <p className="text-gray-400">After question {distractionAnalysis.afterQuestion}:</p>
                <p className="text-warning-500 font-medium">{formatTime(distractionAnalysis.avgAfter)} avg</p>
              </div>
            </div>
            <p className="text-xs text-gray-500 mt-3">
              💡 Consider taking breaks every {distractionAnalysis.beforeCount} questions to maintain focus.
            </p>
          </div>
        </div>
      )}

      {/* Topic Analysis */}
      <div className="grid md:grid-cols-2 gap-6 mb-8">
        {/* Weak Topics */}
        <div className="bg-gray-800/60 rounded-xl p-6 border border-gray-700">
          <h3 className="text-lg font-semibold text-gray-200 mb-4 flex items-center gap-2">
            <AlertTriangle size={18} className="text-warning-500" />
            Topics to Focus On
          </h3>
          {weakTopics.length === 0 ? (
            <p className="text-gray-500 text-sm">All topics look good! 🎉</p>
          ) : (
            <div className="space-y-3">
              {weakTopics.map(([topic, stats]) => {
                const pct = (stats.correct / stats.total) * 100;
                return (
                  <div key={topic}>
                    <div className="flex justify-between text-sm mb-1">
                      <span className="text-gray-300">{topic}</span>
                      <span className="text-danger-500">{pct.toFixed(0)}% ({stats.correct}/{stats.total})</span>
                    </div>
                    <div className="flex items-center gap-2">
                      <div className="flex-1 bg-gray-700 rounded-full h-2">
                        <div className="bg-danger-500 h-2 rounded-full" style={{ width: `${pct}%` }} />
                      </div>
                      <span className="text-xs text-gray-500">{formatTime(stats.avgTime)}</span>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* Strong Topics */}
        <div className="bg-gray-800/60 rounded-xl p-6 border border-gray-700">
          <h3 className="text-lg font-semibold text-gray-200 mb-4 flex items-center gap-2">
            <TrendingUp size={18} className="text-success-500" />
            Strong Topics
          </h3>
          {strongTopics.length === 0 ? (
            <p className="text-gray-500 text-sm">Keep practicing to build strength! 💪</p>
          ) : (
            <div className="space-y-3">
              {strongTopics.map(([topic, stats]) => {
                const pct = (stats.correct / stats.total) * 100;
                return (
                  <div key={topic}>
                    <div className="flex justify-between text-sm mb-1">
                      <span className="text-gray-300">{topic}</span>
                      <span className="text-success-500">{pct.toFixed(0)}% ({stats.correct}/{stats.total})</span>
                    </div>
                    <div className="flex items-center gap-2">
                      <div className="flex-1 bg-gray-700 rounded-full h-2">
                        <div className="bg-success-500 h-2 rounded-full" style={{ width: `${pct}%` }} />
                      </div>
                      <span className="text-xs text-gray-500">{formatTime(stats.avgTime)}</span>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>

      {/* All Answers with Timing */}
      <div className="bg-gray-800/60 rounded-xl p-6 border border-gray-700 mb-8">
        <h3 className="text-lg font-semibold text-gray-200 mb-4 flex items-center gap-2">
          <BookOpen size={18} className="text-brand-400" />
          {result.mode === 'test' || result.mode === 'target' ? 'All Questions & Explanations' : `Review Wrong Answers (${wrongAnswers.length})`}
        </h3>
        <div className="space-y-4 max-h-[500px] overflow-y-auto">
          {(result.mode === 'test' || result.mode === 'target' ? result.answers : wrongAnswers).map((ans, i) => {
            const q = questions.find(qq => qq.id === ans.questionId);
            if (!q) return null;
            const optionsMap: Record<string, string> = {
              A: q.option_a, B: q.option_b, C: q.option_c, D: q.option_d
            };
            return (
              <div key={i} className={`bg-gray-900/50 rounded-lg p-4 border ${
                ans.timedOut ? 'border-gray-600' :
                ans.isCorrect ? 'border-success-500/30' : 'border-danger-500/30'
              }`}>
                <div className="flex items-start justify-between mb-2">
                  <p className="text-sm text-gray-300 font-medium flex-1">
                    <span className="text-gray-500 mr-2">Q{i + 1}.</span>
                    {q.question}
                  </p>
                  <div className="flex items-center gap-2 ml-2 flex-shrink-0">
                    <span className="text-xs text-gray-500 bg-gray-800 px-2 py-0.5 rounded">
                      ⏱️ {formatTime(ans.timeSpentMs || 0)}
                    </span>
                    <span className={ans.timedOut ? 'text-gray-500' : ans.isCorrect ? 'text-success-500' : 'text-danger-500'}>
                      {ans.timedOut ? '⏭️' : ans.isCorrect ? '✓' : '✗'}
                    </span>
                  </div>
                </div>
                {ans.timedOut ? (
                  <p className="text-xs text-gray-500">Skipped (timed out)</p>
                ) : (
                  <>
                    <div className="flex flex-wrap gap-2 text-xs mb-3">
                      <span className={ans.isCorrect ? 'text-success-500' : 'text-danger-500'}>
                        Your answer: {ans.selected} - {optionsMap[ans.selected]}
                      </span>
                      {!ans.isCorrect && (
                        <span className="text-success-500">
                          Correct: {q.correct_answer} - {optionsMap[q.correct_answer]}
                        </span>
                      )}
                    </div>
                    <div className="bg-gray-800/50 rounded-lg p-3 border border-gray-700">
                      <p className="text-xs text-brand-400 font-medium mb-1">💡 Explanation:</p>
                      <div
                        className="text-xs text-gray-400 explanation-html"
                        dangerouslySetInnerHTML={{ __html: q.explanation }}
                      />
                    </div>
                  </>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* Expertise Levels */}
      {topicExpertise.length > 0 && (
        <div className="bg-gray-800/60 rounded-xl p-6 border border-gray-700 mb-8">
          <h3 className="text-lg font-semibold text-gray-200 mb-4">📊 Your Expertise Levels</h3>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
            {topicExpertise
              .filter(e => e.subject === result.subjectName)
              .map(exp => {
                const levelColors: Record<string, string> = {
                  beginner: 'border-danger-500 text-danger-500',
                  intermediate: 'border-warning-500 text-warning-500',
                  advanced: 'border-brand-400 text-brand-400',
                  expert: 'border-success-500 text-success-500',
                };
                return (
                  <div
                    key={exp.topic}
                    className={`p-3 rounded-lg border bg-gray-900/50 ${levelColors[exp.level] || 'border-gray-600 text-gray-400'}`}
                  >
                    <p className="text-xs text-gray-400 mb-1">{exp.topic}</p>
                    <p className="text-sm font-bold capitalize">{exp.level}</p>
                    <p className="text-xs mt-1">{exp.accuracy.toFixed(0)}% accuracy</p>
                    {exp.trend !== 'stable' && (
                      <p className="text-xs mt-0.5">
                        {exp.trend === 'improving' ? '📈 Improving' : '📉 Declining'}
                      </p>
                    )}
                  </div>
                );
              })}
          </div>
        </div>
      )}

      {/* Action Buttons */}
      <div className="flex flex-wrap gap-4 justify-center">
        <button
          onClick={onRetake}
          className="flex items-center gap-2 px-6 py-3 bg-brand-600 text-white rounded-xl hover:bg-brand-500 transition-all font-medium"
        >
          <RotateCcw size={18} /> Retake Test
        </button>
        {wrongAnswers.length > 0 && (
          <button
            onClick={onPracticeWrong}
            className="flex items-center gap-2 px-6 py-3 bg-purple-600 text-white rounded-xl hover:bg-purple-500 transition-all font-medium"
          >
            🧠 Practice Wrong Answers
          </button>
        )}
        <button
          onClick={onGoHome}
          className="flex items-center gap-2 px-6 py-3 bg-gray-700 text-gray-200 rounded-xl hover:bg-gray-600 transition-all font-medium"
        >
          🏠 Go Home
        </button>
      </div>
    </div>
  );
}

// Analyze distraction patterns
function analyzeDistraction(answers: { timeSpentMs: number; isCorrect: boolean }[]): {
  detected: boolean;
  message: string;
  beforeCount: number;
  afterQuestion: number;
  avgBefore: number;
  avgAfter: number;
} {
  if (answers.length < 10) {
    return { detected: false, message: '', beforeCount: 0, afterQuestion: 0, avgBefore: 0, avgAfter: 0 };
  }

  // Calculate rolling average and detect significant increase
  const windowSize = Math.min(10, Math.floor(answers.length / 3));
  let bestSplitIndex = -1;
  let maxRatio = 1.5; // Need at least 50% increase to flag

  for (let i = windowSize; i < answers.length - windowSize; i++) {
    const before = answers.slice(0, i);
    const after = answers.slice(i);

    const avgBefore = before.reduce((a, b) => a + (b.timeSpentMs || 0), 0) / before.length;
    const avgAfter = after.reduce((a, b) => a + (b.timeSpentMs || 0), 0) / after.length;

    const ratio = avgAfter / avgBefore;
    if (ratio > maxRatio) {
      maxRatio = ratio;
      bestSplitIndex = i;
    }
  }

  if (bestSplitIndex === -1) {
    return { detected: false, message: '', beforeCount: 0, afterQuestion: 0, avgBefore: 0, avgAfter: 0 };
  }

  const before = answers.slice(0, bestSplitIndex);
  const after = answers.slice(bestSplitIndex);
  const avgBefore = before.reduce((a, b) => a + (b.timeSpentMs || 0), 0) / before.length;
  const avgAfter = after.reduce((a, b) => a + (b.timeSpentMs || 0), 0) / after.length;

  return {
    detected: true,
    message: `Your response time increased by ${((maxRatio - 1) * 100).toFixed(0)}% after question ${bestSplitIndex}. This might indicate fatigue or distraction.`,
    beforeCount: bestSplitIndex,
    afterQuestion: bestSplitIndex,
    avgBefore,
    avgAfter,
  };
}
