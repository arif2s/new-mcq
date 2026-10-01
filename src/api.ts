import { AppPersistence, TestResult } from './types';

const API_BASE = '/api';

export async function fetchState(): Promise<AppPersistence | null> {
  try {
    const res = await fetch(`${API_BASE}/state/`);
    if (!res.ok) return null;
    const data = await res.json();
    if (!data.app_persistence) return null;
    return data.app_persistence;
  } catch (e) {
    console.error('Failed to fetch state', e);
    return null;
  }
}

export async function saveState(state: AppPersistence): Promise<void> {
  try {
    await fetch(`${API_BASE}/state/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key: 'app_persistence', value: state }),
    });
  } catch (e) {
    console.error('Failed to save state', e);
  }
}

export async function saveSession(result: TestResult, questions: any[]): Promise<void> {
  try {
    await fetch(`${API_BASE}/sessions/save`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...result, questions }),
    });
  } catch (e) {
    console.error('Failed to save session', e);
  }
}

export async function fetchSessionHistory(): Promise<any[]> {
  try {
    const res = await fetch(`${API_BASE}/sessions/history`);
    if (!res.ok) return [];
    return await res.json();
  } catch (e) {
    console.error('Failed to fetch history', e);
    return [];
  }
}

export async function fetchSessionQuestions(sessionId: string): Promise<any[]> {
  try {
    const res = await fetch(`${API_BASE}/sessions/${sessionId}/questions`);
    if (!res.ok) return [];
    return await res.json();
  } catch (e) {
    console.error('Failed to fetch session questions', e);
    return [];
  }
}

export async function fetchSessionTopics(sessionId: string): Promise<any[]> {
  try {
    const res = await fetch(`${API_BASE}/sessions/${sessionId}/topics`);
    if (!res.ok) return [];
    return await res.json();
  } catch (e) {
    console.error('Failed to fetch session topics', e);
    return [];
  }
}
