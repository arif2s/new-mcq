const prevQuiz = {
  questions: Array.from({ length: 10000 }, (_, i) => ({ id: `q${i}`, topic_name: "T", question: "Q", correct_answer: "A", explanation: "E", option_a: "A", option_b: "B", option_c: "C", option_d: "D" }))
};

const validAnswers = Array.from({ length: 10000 }, (_, i) => ({
  questionId: `q${i}`,
  isCorrect: false,
  selected: "a"
}));

function addToReviewQueue(updated, item) {
  return updated; // dummy
}

let updated = {};

console.time("Baseline (Array.find)");
for (let j = 0; j < 10; j++) {
  for (const ans of validAnswers) {
    if (!ans.isCorrect) {
      const q = prevQuiz.questions.find(qq => qq.id === ans.questionId);
      if (q) {
        updated = addToReviewQueue(updated, {
          questionId: q.id,
        });
      }
    }
  }
}
console.timeEnd("Baseline (Array.find)");

console.time("Optimized (Map)");
for (let j = 0; j < 10; j++) {
  const questionMap = new Map();
  for (const q of prevQuiz.questions) {
    questionMap.set(q.id, q);
  }
  for (const ans of validAnswers) {
    if (!ans.isCorrect) {
      const q = questionMap.get(ans.questionId);
      if (q) {
        updated = addToReviewQueue(updated, {
          questionId: q.id,
        });
      }
    }
  }
}
console.timeEnd("Optimized (Map)");
