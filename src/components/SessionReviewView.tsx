import { useState, useEffect } from 'react';
import { fetchSessionQuestions } from '../api';
import { ArrowLeft, CheckCircle, XCircle } from 'lucide-react';

interface SessionReviewViewProps {
  sessionId: string;
  onBack: () => void;
}

export default function SessionReviewView({ sessionId, onBack }: SessionReviewViewProps) {
  const [questions, setQuestions] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let mounted = true;
    fetchSessionQuestions(sessionId).then(data => {
      if (mounted) {
        setQuestions(data);
        setLoading(false);
      }
    });
    return () => { mounted = false; };
  }, [sessionId]);

  if (loading) {
    return <div className="p-8 text-center text-gray-400">Loading session details...</div>;
  }

  return (
    <div className="p-4 md:p-8 max-w-5xl mx-auto flex flex-col">
      <div className="flex justify-between items-center mb-6">
        <button onClick={onBack} className="flex items-center gap-2 text-gray-400 hover:text-white transition-colors">
          <ArrowLeft size={20} /> Back to Hub
        </button>
        <h2 className="text-xl font-bold text-gray-200">Session Review</h2>
      </div>

      <div className="space-y-4">
        {questions.map((q, index) => {
          const isCorrect = q.is_correct === 1;
          const userAns = q.selected_option;
          const correctAns = q.correct_answer;
          const optionsMap = {
            'A': q.opt_a,
            'B': q.opt_b,
            'C': q.opt_c,
            'D': q.opt_d,
          };
          return (
            <div key={index} className={`bg-gray-800 rounded-xl p-6 border ${isCorrect ? 'border-success-500/30' : 'border-danger-500/30'}`}>
              <div className="flex items-start gap-4">
                <div className="mt-1">
                  {isCorrect ? <CheckCircle className="text-success-500" size={24} /> : <XCircle className="text-danger-500" size={24} />}
                </div>
                <div className="flex-1">
                  <div className="flex justify-between mb-2 text-sm text-gray-400">
                    <span>Question {index + 1}</span>
                    <span className="bg-gray-700 px-2 py-0.5 rounded text-xs">{q.topic || 'General'}</span>
                  </div>
                  <p className="text-gray-200 mb-4">{q.question_text}</p>

                  <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mb-4">
                    {(['A', 'B', 'C', 'D'] as const).map(opt => {
                      const text = optionsMap[opt];
                      if (!text) return null;
                      const isUserOpt = userAns === opt;
                      const isCorrectOpt = correctAns === opt;

                      let bgClass = "bg-gray-900 border-gray-700 text-gray-400";
                      if (isCorrectOpt) bgClass = "bg-success-500/20 border-success-500/50 text-success-300";
                      else if (isUserOpt) bgClass = "bg-danger-500/20 border-danger-500/50 text-danger-300";

                      return (
                        <div key={opt} className={`p-3 rounded-lg border text-sm flex gap-3 ${bgClass}`}>
                          <span className="font-bold shrink-0">{opt}</span>
                          <span>{text}</span>
                        </div>
                      )
                    })}
                  </div>

                  {q.explanation && (
                    <div className="bg-brand-900/30 border border-brand-500/30 p-4 rounded-lg mt-4 text-sm text-gray-300">
                      <p className="font-semibold text-brand-400 mb-1">Explanation:</p>
                      <div dangerouslySetInnerHTML={{__html: q.explanation}}></div>
                    </div>
                  )}
                </div>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  );
}
