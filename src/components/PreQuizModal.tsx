import { Play, BookOpen } from 'lucide-react';
import { QuizConfig } from '../types';

interface PreQuizModalProps {
  config: QuizConfig;
  onStudyFirst: () => void;
  onStartQuiz: () => void;
  onCancel: () => void;
}

export default function PreQuizModal({ config, onStudyFirst, onStartQuiz, onCancel }: PreQuizModalProps) {
  return (
    <div className="fixed inset-0 bg-black/80 z-[100] flex items-center justify-center p-4">
      <div className="bg-gray-900 border border-gray-700 rounded-2xl p-6 max-w-md w-full shadow-2xl animate-fade-in">
        <h2 className="text-xl font-bold text-gray-100 mb-2">Quiz Ready!</h2>
        <p className="text-gray-400 mb-6 text-sm">
          You are about to start a new quiz in {config.subjectName}. Would you like to study the related topics first, or start the MCQ directly?
        </p>

        <div className="flex flex-col gap-3">
          <button
            onClick={onStudyFirst}
            className="w-full flex items-center justify-center gap-2 px-6 py-4 bg-brand-600/20 text-brand-400 hover:bg-brand-600/30 hover:text-brand-300 rounded-xl transition-all font-medium border border-brand-500/30"
          >
            <BookOpen size={20} /> Study Topics First
          </button>

          <button
            onClick={onStartQuiz}
            className="w-full flex items-center justify-center gap-2 px-6 py-4 bg-gradient-to-r from-brand-600 to-purple-600 hover:from-brand-500 hover:to-purple-500 text-white rounded-xl transition-all font-medium shadow-lg hover:shadow-brand-500/25"
          >
            <Play size={20} /> Start MCQ Directly
          </button>

          <button
            onClick={onCancel}
            className="mt-2 w-full py-2 text-sm text-gray-500 hover:text-gray-300 transition-colors"
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}
