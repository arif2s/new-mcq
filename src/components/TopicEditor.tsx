import React, { useState, useEffect, useRef } from 'react';
import { X, Check, Search } from 'lucide-react';

interface QuestionWithTopics {
  question: string;
  option_a: string;
  option_b: string;
  option_c: string;
  option_d: string;
  correct_answer: string;
  explanation: string;
  topics: string[];
}

interface TopicEditorProps {
  questions: QuestionWithTopics[];
  onConfirm: (finalQuestions: QuestionWithTopics[]) => void;
  onCancel: () => void;
}

interface TopicState {
  originalTopic: string;
  currentTopic: string;
  excluded: boolean;
  isEditing: boolean;
}

export default function TopicEditor({ questions, onConfirm, onCancel }: TopicEditorProps) {
  // Map questions to a flat structure we can easily render, or keep per question
  // Actually, a list of unique topics might be easier, but the API expects topics per question.
  // We'll manage topics as a state: questionIndex -> list of TopicState

  const [qStates, setQStates] = useState<TopicState[][]>(() =>
    questions.map(q =>
      q.topics.map(t => ({
        originalTopic: t,
        currentTopic: t,
        excluded: false,
        isEditing: false
      }))
    )
  );

  const [existingNotes, setExistingNotes] = useState<Set<string>>(new Set());

  // Detect duplicates across all questions to mark them dark yellow
  // A duplicate is any topic that appears more than once in the entire set (case-insensitive)

  useEffect(() => {
    const fetchExisting = async () => {
      try {
        const res = await fetch('/api/mcq/topics_status', { // we need an endpoint or we fetch all
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ topics: questions.flatMap(q => q.topics) })
        });
        const data = await res.json();
        if (data.status === 'success') {
          setExistingNotes(new Set(data.existing_topics.map((t: string) => t.toLowerCase())));
        }
      } catch (e) {
        console.error(e);
      }
    };
    fetchExisting();
  }, [questions]);

  // Handle single click to toggle exclusion, double click to edit
  let clickTimeout = useRef<NodeJS.Timeout | null>(null);

  const handleClick = (qIndex: number, tIndex: number) => {
    if (clickTimeout.current) {
      clearTimeout(clickTimeout.current);
      clickTimeout.current = null;
      // Double click
      setQStates(prev => {
        const next = [...prev];
        next[qIndex] = [...next[qIndex]];
        next[qIndex][tIndex] = { ...next[qIndex][tIndex], isEditing: true, excluded: false };
        return next;
      });
    } else {
      // Single click wait
      clickTimeout.current = setTimeout(() => {
        clickTimeout.current = null;
        setQStates(prev => {
          const next = [...prev];
          next[qIndex] = [...next[qIndex]];
          next[qIndex][tIndex] = { ...next[qIndex][tIndex], excluded: !next[qIndex][tIndex].excluded };
          return next;
        });
      }, 250);
    }
  };

  const handleEditChange = (qIndex: number, tIndex: number, newValue: string) => {
    setQStates(prev => {
      const next = [...prev];
      next[qIndex] = [...next[qIndex]];
      next[qIndex][tIndex] = { ...next[qIndex][tIndex], currentTopic: newValue };
      return next;
    });
  };

  const handleEditKeyDown = (e: React.KeyboardEvent, qIndex: number, tIndex: number) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      setQStates(prev => {
        const next = [...prev];
        next[qIndex] = [...next[qIndex]];
        next[qIndex][tIndex] = { ...next[qIndex][tIndex], isEditing: false };
        return next;
      });
    }
  };

  const handleEditBlur = (qIndex: number, tIndex: number) => {
    setQStates(prev => {
      const next = [...prev];
      next[qIndex] = [...next[qIndex]];
      next[qIndex][tIndex] = { ...next[qIndex][tIndex], isEditing: false };
      return next;
    });
  };

  const getCounts = () => {
    const counts: Record<string, number> = {};
    qStates.forEach(q => {
      q.forEach(t => {
        if (!t.excluded) {
          const lower = t.currentTopic.toLowerCase();
          counts[lower] = (counts[lower] || 0) + 1;
        }
      });
    });
    return counts;
  };

  const counts = getCounts();

  const handleConfirm = () => {
    const finalQuestions = questions.map((q, i) => ({
      ...q,
      topics: qStates[i].filter(t => !t.excluded).map(t => t.currentTopic).filter(t => t.trim() !== '')
    }));
    onConfirm(finalQuestions);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 animate-fade-in">
      <div className="bg-gray-900 border border-gray-700 rounded-xl w-full max-w-4xl max-h-[90vh] flex flex-col shadow-2xl">

        <div className="p-4 border-b border-gray-700 flex justify-between items-center bg-gray-800 rounded-t-xl">
          <div className="flex items-center gap-2">
            <Search className="text-brand-400" size={20} />
            <h2 className="text-lg font-bold text-gray-200">Review & Edit Extracted Topics</h2>
          </div>
          <button onClick={onCancel} className="text-gray-400 hover:text-white transition-colors">
            <X size={24} />
          </button>
        </div>

        <div className="p-4 overflow-y-auto flex-1 space-y-4">
          <p className="text-sm text-gray-400 mb-4">
            Topics have been extracted automatically.
            <br/><strong className="text-gray-300">Single-click</strong> a topic to exclude it (strikethrough).
            <br/><strong className="text-gray-300">Double-click</strong> a topic to edit its name (press Enter to save).
            <br/><span className="text-green-700 font-bold">Dark Green</span>: Notes already exist. <span className="text-yellow-700 font-bold">Dark Yellow</span>: Duplicate topic in this batch.
          </p>

          {questions.map((q, qIndex) => (
            <div key={qIndex} className="bg-gray-800 p-3 rounded-lg border border-gray-700">
              <div className="text-sm text-gray-300 mb-2 line-clamp-2" title={q.question}>
                {qIndex + 1}. {q.question}
              </div>
              <div className="flex flex-wrap gap-2">
                {qStates[qIndex].map((t, tIndex) => {

                  let bgColor = 'bg-gray-700 hover:bg-gray-600 text-gray-200';
                  let strike = '';

                  if (t.excluded) {
                    bgColor = 'bg-red-900/40 text-gray-500 hover:bg-red-900/60';
                    strike = 'line-through';
                  } else {
                    const lower = t.currentTopic.toLowerCase();
                    const isDup = counts[lower] > 1;
                    const hasNotes = existingNotes.has(lower);

                    if (hasNotes) {
                      bgColor = 'bg-green-900/60 text-green-200 hover:bg-green-800/80 border border-green-800';
                    } else if (isDup) {
                      bgColor = 'bg-yellow-900/50 text-yellow-200 hover:bg-yellow-800/70 border border-yellow-800';
                    }
                  }

                  if (t.isEditing) {
                    return (
                      <textarea
                        key={tIndex}
                        autoFocus
                        rows={1}
                        className="px-2 py-1 text-xs rounded-full border border-brand-500 bg-gray-900 text-white focus:outline-none focus:ring-1 focus:ring-brand-500 resize-none min-w-[100px]"
                        value={t.currentTopic}
                        onChange={(e) => handleEditChange(qIndex, tIndex, e.target.value)}
                        onKeyDown={(e) => handleEditKeyDown(e, qIndex, tIndex)}
                        onBlur={() => handleEditBlur(qIndex, tIndex)}
                      />
                    );
                  }

                  return (
                    <button
                      key={tIndex}
                      onClick={() => handleClick(qIndex, tIndex)}
                      className={`px-3 py-1 text-xs rounded-full cursor-pointer transition-colors ${bgColor} ${strike}`}
                    >
                      {t.currentTopic}
                    </button>
                  );
                })}
              </div>
            </div>
          ))}
        </div>

        <div className="p-4 border-t border-gray-700 flex justify-end gap-3 bg-gray-800 rounded-b-xl">
          <button
            onClick={onCancel}
            className="px-4 py-2 text-sm text-gray-400 hover:text-white transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleConfirm}
            className="px-6 py-2 text-sm bg-brand-600 hover:bg-brand-500 text-white rounded-lg flex items-center gap-2 font-medium transition-all"
          >
            <Check size={16} /> Confirm & Queue
          </button>
        </div>

      </div>
    </div>
  );
}
