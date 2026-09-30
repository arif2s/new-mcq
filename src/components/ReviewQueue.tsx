import { useState, useMemo } from 'react';
import { CheckCircle, Circle, Trash2, AlertTriangle, BookOpen, Settings } from 'lucide-react';
import type { AppPersistence } from '../types';

interface ReviewQueueProps {
  persistence: AppPersistence;
  onMarkReviewed: (itemId: string) => void;
  onRemoveItem: (itemId: string) => void;
  onClearAll: () => void;
  onUpdateLimit: (limit: number) => void;
}

export default function ReviewQueue({
  persistence,
  onMarkReviewed,
  onRemoveItem,
  onClearAll,
  onUpdateLimit,
}: ReviewQueueProps) {
  const [showSettings, setShowSettings] = useState(false);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [filterSubject, setFilterSubject] = useState<string>('all');

  const { reviewQueue, reviewQueueLimit, totalReviewedEver } = persistence;

  // Memoize heavy filtering, mapping, and sorting operations to prevent
  // recalculation on simple state changes (like expanding an accordion item).
  const {
    unreviewedCount,
    reviewedInQueueCount,
    subjects,
    sortedItems,
    isAtLimit
  } = useMemo(() => {
    let unreviewed = 0;
    const subjectSet = new Set<string>();

    // Single pass for counts and unique subjects
    for (const item of reviewQueue) {
      if (!item.reviewed) unreviewed++;
      subjectSet.add(item.subjectName);
    }

    const reviewed = reviewQueue.length - unreviewed;
    const uniqueSubjects = Array.from(subjectSet);
    const atLimit = reviewQueue.length >= reviewQueueLimit;

    const filtered = filterSubject === 'all'
      ? reviewQueue
      : reviewQueue.filter(r => r.subjectName === filterSubject);

    // Fast O(N log N) sorting using pre-computed integers or fast parsing,
    // avoiding heavy object allocation (new Date) in the sort loop.
    const sorted = [...filtered].sort((a, b) => {
      if (a.reviewed !== b.reviewed) return a.reviewed ? 1 : -1;
      const timeA = a.addedTimestamp || Date.parse(a.dateAdded);
      const timeB = b.addedTimestamp || Date.parse(b.dateAdded);
      return timeB - timeA;
    });

    return {
      unreviewedCount: unreviewed,
      reviewedInQueueCount: reviewed,
      subjects: uniqueSubjects,
      sortedItems: sorted,
      isAtLimit: atLimit
    };
  }, [reviewQueue, filterSubject, reviewQueueLimit]);

  const optionLabels: Record<string, string> = { A: 'a', B: 'b', C: 'c', D: 'd' };

  return (
    <div className="max-w-4xl mx-auto p-6 animate-fade-in">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-3xl font-bold text-gray-100 flex items-center gap-3">
            <BookOpen size={32} className="text-brand-400" />
            Review Queue
          </h1>
          <p className="text-gray-400 mt-1">
            Questions you got wrong — review and clear them to improve
          </p>
        </div>
        <button
          onClick={() => setShowSettings(!showSettings)}
          className="p-2 bg-gray-800 rounded-lg text-gray-400 hover:text-white hover:bg-gray-700 transition-colors"
        >
          <Settings size={20} />
        </button>
      </div>

      {/* Limit Warning */}
      {isAtLimit && (
        <div className="bg-warning-500/10 border border-warning-500/30 rounded-xl p-4 mb-6 flex items-start gap-3">
          <AlertTriangle className="text-warning-500 flex-shrink-0 mt-0.5" size={20} />
          <div>
            <p className="text-warning-500 font-medium">Review Queue Full!</p>
            <p className="text-sm text-gray-400 mt-1">
              You've reached the limit of {reviewQueueLimit} items. Review and clear some questions to make room for new ones.
              Oldest items will be automatically removed when new wrong answers are added.
            </p>
          </div>
        </div>
      )}

      {/* Settings Panel */}
      {showSettings && (
        <div className="bg-gray-800/60 rounded-xl p-4 border border-gray-700 mb-6 animate-fade-in">
          <h3 className="text-sm font-semibold text-gray-300 mb-3">Settings</h3>
          <div className="flex items-center gap-4">
            <label className="text-sm text-gray-400">
              Queue Limit:
              <input
                type="number"
                min={10}
                max={200}
                value={reviewQueueLimit}
                onChange={(e) => onUpdateLimit(Math.max(10, Math.min(200, Number(e.target.value))))}
                className="ml-2 w-20 bg-gray-900 border border-gray-600 rounded px-2 py-1 text-gray-200"
              />
            </label>
          </div>
        </div>
      )}

      {/* Stats Bar */}
      <div className="grid grid-cols-3 gap-4 mb-6">
        <div className="bg-gray-800 rounded-xl p-4 text-center border border-gray-700">
          <p className="text-2xl font-bold text-warning-500">{unreviewedCount}</p>
          <p className="text-xs text-gray-400">To Review</p>
        </div>
        <div className="bg-gray-800 rounded-xl p-4 text-center border border-gray-700">
          <p className="text-2xl font-bold text-success-500">{totalReviewedEver}</p>
          <p className="text-xs text-gray-400">Total Reviewed</p>
        </div>
        <div className="bg-gray-800 rounded-xl p-4 text-center border border-gray-700">
          <p className="text-2xl font-bold text-gray-400">{reviewQueue.length}/{reviewQueueLimit}</p>
          <p className="text-xs text-gray-400">Queue Used</p>
        </div>
      </div>

      {/* Filters and Actions */}
      <div className="flex flex-wrap items-center justify-between gap-4 mb-6">
        <div className="flex items-center gap-2">
          <label className="text-sm text-gray-400">Filter:</label>
          <select
            value={filterSubject}
            onChange={(e) => setFilterSubject(e.target.value)}
            className="bg-gray-800 border border-gray-600 rounded-lg px-3 py-1.5 text-sm text-gray-200"
          >
            <option value="all">All Subjects</option>
            {subjects.map(s => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        </div>

        {reviewQueue.length > 0 && (
          <button
            onClick={() => {
              if (confirm(`Mark all ${unreviewedCount > 0 ? unreviewedCount + ' unreviewed ' : ''}items as reviewed and clear the queue?`)) {
                onClearAll();
              }
            }}
            className="px-4 py-2 bg-success-600/20 text-success-400 rounded-lg text-sm hover:bg-success-600/30 transition-colors flex items-center gap-2 font-medium"
          >
            <CheckCircle size={16} />
            Clear All ({reviewQueue.length})
          </button>
        )}
      </div>

      {/* Pending reviewed items notice */}
      {reviewedInQueueCount > 0 && unreviewedCount > 0 && (
        <div className="text-xs text-gray-500 mb-4 flex items-center gap-1">
          <CheckCircle size={12} className="text-success-500" />
          {reviewedInQueueCount} reviewed item{reviewedInQueueCount > 1 ? 's' : ''} still in queue — use Clear All to remove them
        </div>
      )}

      {/* Queue Items */}
      {sortedItems.length === 0 ? (
        <div className="text-center py-16 bg-gray-800/40 rounded-2xl border border-gray-700">
          <CheckCircle size={48} className="mx-auto text-success-500 mb-4" />
          <h2 className="text-xl font-semibold text-gray-200 mb-2">All Clear! 🎉</h2>
          <p className="text-gray-400 mb-1">
            {filterSubject === 'all'
              ? "No questions to review. Keep practicing!"
              : `No questions to review for ${filterSubject}.`}
          </p>
          {totalReviewedEver > 0 && (
            <p className="text-gray-500 text-sm">
              You've reviewed {totalReviewedEver} questions in total.
            </p>
          )}
        </div>
      ) : (
        <div className="space-y-3">
          {sortedItems.map((item, index) => (
            <div
              key={item.id}
              className={`bg-gray-800/60 rounded-xl border transition-all ${
                item.reviewed
                  ? 'border-success-500/30 opacity-60'
                  : 'border-gray-700 hover:border-gray-600'
              }`}
            >
              {/* Header */}
              <div
                className="p-4 flex items-start gap-3 cursor-pointer"
                onClick={() => setExpandedId(expandedId === item.id ? null : item.id)}
              >
                {/* Review checkbox */}
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    if (!item.reviewed) {
                      onMarkReviewed(item.id);
                    }
                  }}
                  disabled={item.reviewed}
                  className={`mt-0.5 transition-colors ${
                    item.reviewed
                      ? 'text-success-500 cursor-default'
                      : 'text-gray-600 hover:text-success-500'
                  }`}
                >
                  {item.reviewed ? <CheckCircle size={22} /> : <Circle size={22} />}
                </button>

                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="text-xs px-2 py-0.5 bg-brand-600/20 text-brand-400 rounded">
                      {item.subjectName}
                    </span>
                    <span className="text-xs px-2 py-0.5 bg-gray-700 text-gray-400 rounded">
                      {item.topic}
                    </span>
                    <span className="text-xs text-gray-500">
                      #{index + 1}
                    </span>
                  </div>
                  <p className="text-sm text-gray-200 line-clamp-2">{item.question}</p>
                  <div className="flex items-center gap-3 mt-2 text-xs">
                    <span className="text-danger-500">
                      Your answer: {item.userAnswer}
                    </span>
                    <span className="text-success-500">
                      Correct: {item.correctAnswer}
                    </span>
                  </div>
                </div>

                {/* Delete button */}
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    onRemoveItem(item.id);
                  }}
                  className="p-1.5 text-gray-500 hover:text-danger-500 hover:bg-danger-500/10 rounded transition-colors"
                >
                  <Trash2 size={16} />
                </button>
              </div>

              {/* Expanded Content */}
              {expandedId === item.id && (
                <div className="px-4 pb-4 border-t border-gray-700 pt-4 animate-fade-in">
                  <div className="grid grid-cols-2 gap-2 mb-4">
                    {(['A', 'B', 'C', 'D'] as const).map(opt => {
                      const optKey = optionLabels[opt] as keyof typeof item.options;
                      const isCorrect = opt === item.correctAnswer;
                      const isUserAnswer = opt === item.userAnswer;
                      return (
                        <div
                          key={opt}
                          className={`p-3 rounded-lg border text-sm ${
                            isCorrect
                              ? 'bg-success-500/10 border-success-500/30 text-success-400'
                              : isUserAnswer
                              ? 'bg-danger-500/10 border-danger-500/30 text-danger-400'
                              : 'bg-gray-900/50 border-gray-700 text-gray-400'
                          }`}
                        >
                          <span className="font-bold mr-2">{opt}:</span>
                          {item.options[optKey]}
                          {isCorrect && ' ✓'}
                          {isUserAnswer && !isCorrect && ' ✗'}
                        </div>
                      );
                    })}
                  </div>
                  <div className="bg-brand-600/10 border border-brand-500/30 rounded-lg p-4">
                    <p className="text-xs text-brand-400 font-medium mb-2">💡 Explanation:</p>
                    <div
                      className="text-sm text-gray-300 explanation-html"
                      dangerouslySetInnerHTML={{ __html: item.explanation }}
                    />
                  </div>
                  <p className="text-xs text-gray-500 mt-3">
                    Added: {new Date(item.dateAdded).toLocaleDateString()}
                  </p>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
