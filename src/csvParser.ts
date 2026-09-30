import Papa from 'papaparse';
import type { QuizQuestion, OptionKey } from './types';

// Improved deterministic hash (cyrb53) for stable, collision-resistant IDs
function generateStableId(str: string, seed = 0): string {
  let h1 = 0xdeadbeef ^ seed, h2 = 0x41c6ce57 ^ seed;
  for (let i = 0, ch; i < str.length; i++) {
    ch = str.charCodeAt(i);
    h1 = Math.imul(h1 ^ ch, 2654435761);
    h2 = Math.imul(h2 ^ ch, 1597334677);
  }
  h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909);
  h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909);
  return (4294967296 * (2097151 & h2) + (h1 >>> 0)).toString(36);
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
    // Safely strip BOM (\ufeff) to prevent required column mismatches on the first header
    transformHeader: (header: string) => header.replace(/^\ufeff/, '').trim().toLowerCase(),
  });

  if (result.errors.length > 0) {
    errors.push(...result.errors.map(e => `Row ${e.row ?? 'Unknown'}: ${e.message}`));
  }

  const requiredColumns = ['topic_name', 'question', 'option_a', 'option_b', 'option_c', 'option_d', 'correct_answer', 'explanation'];
  const headers = result.meta.fields || [];
  const missing = requiredColumns.filter(col => !headers.includes(col));

  if (missing.length > 0) {
    errors.push(`Missing columns: ${missing.join(', ')}`);
    return { questions: [], errors };
  }

  const questions: QuizQuestion[] = [];

  for (let index = 0; index < result.data.length; index++) {
    const row = result.data[index];
    const q = row.question?.trim();

    if (!q) {
      errors.push(`Row ${index + 1}: Empty question`);
      continue;
    }

    const correct = (row.correct_answer || '').trim().toUpperCase();
    if (!['A', 'B', 'C', 'D'].includes(correct)) {
      errors.push(`Row ${index + 1}: Invalid correct_answer "${row.correct_answer}". Must be A, B, C, or D.`);
      continue;
    }

    const optA = (row.option_a || '').trim();
    const optB = (row.option_b || '').trim();
    const optC = (row.option_c || '').trim();
    const optD = (row.option_d || '').trim();

    // Hash complete row contents to guarantee stable ID uniqueness
    const hashInput = `${q}|${correct}|${optA}|${optB}|${optC}|${optD}`;
    const stableId = `q_${index}_${generateStableId(hashInput)}`;

    questions.push({
      id: stableId,
      topic_name: (row.topic_name || 'General').trim(),
      question: q,
      option_a: optA,
      option_b: optB,
      option_c: optC,
      option_d: optD,
      correct_answer: correct as OptionKey,
      explanation: (row.explanation || 'No explanation provided.').trim(),
    });
  }

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
