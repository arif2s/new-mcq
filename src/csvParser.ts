import Papa from 'papaparse';
import type { QuizQuestion } from './types';

// Simple deterministic hash for stable question IDs
function simpleHash(str: string): string {
  let hash = 0;
  for (let i = 0; i < str.length; i++) {
    const char = str.charCodeAt(i);
    hash = ((hash << 5) - hash) + char;
    hash = hash & hash; // Convert to 32bit integer
  }
  return Math.abs(hash).toString(36);
}

interface RawRow {
  topic_name?: string;
  question?: string;
  option_a?: string;
  option_b?: string;
  option_c?: string;
  option_d?: string;
  correct_answer?: string;
  explanation?: string;
}

export function parseCSV(csvText: string): { questions: QuizQuestion[]; errors: string[] } {
  const errors: string[] = [];
  const result = Papa.parse<RawRow>(csvText, {
    header: true,
    skipEmptyLines: true,
    transformHeader: (header: string) => header.trim().toLowerCase(),
  });

  if (result.errors.length > 0) {
    errors.push(...result.errors.map(e => `Row ${e.row}: ${e.message}`));
  }

  const requiredColumns = ['topic_name', 'question', 'option_a', 'option_b', 'option_c', 'option_d', 'correct_answer', 'explanation'];
  const headers = result.meta.fields || [];
  const missing = requiredColumns.filter(col => !headers.includes(col));

  if (missing.length > 0) {
    errors.push(`Missing columns: ${missing.join(', ')}`);
    return { questions: [], errors };
  }

  const questions: QuizQuestion[] = [];

  result.data.forEach((row, index) => {
    const q = row.question?.trim();
    if (!q) {
      errors.push(`Row ${index + 1}: Empty question`);
      return;
    }

    const correct = (row.correct_answer || '').trim().toUpperCase();
    if (!['A', 'B', 'C', 'D'].includes(correct)) {
      errors.push(`Row ${index + 1}: Invalid correct_answer "${row.correct_answer}". Must be A, B, C, or D.`);
      return;
    }

    questions.push({
      id: `q_${index}_${simpleHash(q + correct + (row.option_a || ''))}`,
      topic_name: (row.topic_name || 'General').trim(),
      question: q,
      option_a: (row.option_a || '').trim(),
      option_b: (row.option_b || '').trim(),
      option_c: (row.option_c || '').trim(),
      option_d: (row.option_d || '').trim(),
      correct_answer: correct,
      explanation: (row.explanation || 'No explanation provided.').trim(),
    });
  });

  return { questions, errors };
}

export function generateSampleCSV(): string {
  const sample = [
    {
      topic_name: 'Python Basics',
      question: 'What is the output of print(2 ** 3)?',
      option_a: '6',
      option_b: '8',
      option_c: '9',
      option_d: '12',
      correct_answer: 'B',
      explanation: '2 ** 3 means 2 raised to the power of 3, which equals 8.',
    },
    {
      topic_name: 'Python Basics',
      question: 'Which keyword is used to create a function?',
      option_a: 'function',
      option_b: 'def',
      option_c: 'func',
      option_d: 'define',
      correct_answer: 'B',
      explanation: 'The "def" keyword is used to define functions in Python.',
    },
    {
      topic_name: 'Data Types',
      question: 'What data type is used for true/false values?',
      option_a: 'int',
      option_b: 'bool',
      option_c: 'str',
      option_d: 'float',
      correct_answer: 'B',
      explanation: 'The bool data type is used to represent true/false values.',
    },
    {
      topic_name: 'Data Types',
      question: 'Which of the following is a mutable data type?',
      option_a: 'tuple',
      option_b: 'string',
      option_c: 'list',
      option_d: 'int',
      correct_answer: 'C',
      explanation: 'Lists are mutable, meaning their elements can be changed after creation.',
    },
    {
      topic_name: 'Control Flow',
      question: 'What does the "break" statement do in a loop?',
      option_a: 'Skips to the next iteration',
      option_b: 'Exits the loop immediately',
      option_c: 'Restarts the loop',
      option_d: 'Does nothing',
      correct_answer: 'B',
      explanation: 'The break statement terminates the loop entirely and continues with the next statement after the loop.',
    },
  ];

  return Papa.unparse(sample);
}
