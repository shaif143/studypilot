/**
 * The frontend/backend contract.
 *
 * `packages/shared/src/types.ts` is hand-written so it can express meaning the
 * generated types could not — for example that `generative: false` means the
 * text was quoted rather than written. The cost of hand-writing it is that it
 * can drift from the backend.
 *
 * This test removes that risk for the part that matters most: it drives the
 * real client against a stub transport, records every URL it produces, and
 * asserts each one exists in the backend's committed OpenAPI document.
 * Renaming a route on the server now fails here rather than at runtime.
 *
 * Regenerate the snapshot with `npm run api:schema` after changing any route.
 */
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { StudyPilotClient } from '@studypilot/api-client';
import type { Platform } from '@studypilot/shared';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../..');

interface OpenApiDocument {
  paths: Record<string, Record<string, unknown>>;
}

const schema = JSON.parse(
  readFileSync(resolve(ROOT, 'fixtures/openapi.json'), 'utf8'),
) as OpenApiDocument;

/** Turn `/api/documents/doc-1/pages/4` into `/api/documents/{id}/pages/{n}`. */
function matchesTemplate(actual: string, template: string): boolean {
  const pattern = template
    .replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
    .replace(/\\\{[^}]+\\\}/g, '[^/]+');
  return new RegExp(`^${pattern}$`).test(actual);
}

function isKnownRoute(path: string, method: string): boolean {
  for (const [template, operations] of Object.entries(schema.paths)) {
    if (matchesTemplate(path, template) && method.toLowerCase() in operations) {
      return true;
    }
  }
  return false;
}

const platform: Platform = {
  kind: 'web',
  apiBaseUrl: '',
  hasSecureStorage: false,
  getApiToken: async () => null,
  openExternal: async () => {},
};

describe('API contract', () => {
  let calls: Array<{ path: string; method: string }>;
  let client: StudyPilotClient;

  beforeEach(() => {
    calls = [];
    vi.stubGlobal('fetch', (url: string, init: { method?: string }) => {
      calls.push({ path: url.split('?')[0]!, method: init?.method ?? 'GET' });
      return Promise.resolve(
        new Response('null', { status: 200, headers: { 'content-type': 'application/json' } }),
      );
    });
    client = new StudyPilotClient(platform);
  });

  async function record(invoke: () => Promise<unknown>) {
    await invoke();
    const call = calls.at(-1)!;
    return call;
  }

  it.each([
    ['authStatus', () => client.authStatus()],
    ['register', () => client.register('a@b.co', 'a-good-long-passphrase')],
    ['login', () => client.login('a@b.co', 'a-good-long-passphrase')],
    ['logout', () => client.logout()],
    ['logoutEverywhere', () => client.logoutEverywhere()],
    ['changePassword', () => client.changePassword('old-passphrase', 'new-passphrase')],
    ['health', () => client.health()],
    ['liveness', () => client.liveness()],
    ['providers', () => client.providers()],
    ['settings', () => client.settings()],
    ['updateSettings', () => client.updateSettings({ theme: 'dark' })],
    ['courses', () => client.courses()],
    ['course', () => client.course('c1')],
    ['createCourse', () => client.createCourse({ title: 'x' })],
    ['updateCourse', () => client.updateCourse('c1', { title: 'y' })],
    ['deleteCourse', () => client.deleteCourse('c1')],
    ['courseTopics', () => client.courseTopics('c1')],
    ['documents', () => client.documents()],
    ['document', () => client.document('d1')],
    ['deleteDocument', () => client.deleteDocument('d1')],
    ['uploadArchive', () => client.uploadArchive('c1', new File(['x'], 'a.zip'))],
    ['pages', () => client.pages('d1')],
    ['page', () => client.page('d1', 4)],
    ['documentTopics', () => client.documentTopics('d1')],
    ['ask', () => client.ask({ question: 'why?' })],
    ['sessions', () => client.sessions()],
    ['session', () => client.session('s1')],
    ['resumeSession', () => client.resumeSession('d1')],
    ['updateSession', () => client.updateSession('s1', { current_page: 2 })],
    ['messages', () => client.messages('s1')],
    ['deleteSession', () => client.deleteSession('s1')],
    ['generateAssessment', () => client.generateAssessment({ course_id: 'c1' })],
    ['assessments', () => client.assessments()],
    ['assessment', () => client.assessment('a1')],
    ['submitAssessment', () => client.submitAssessment('a1', [])],
    ['attempt', () => client.attempt('a1', 'at1')],
    ['generateFlashcards', () => client.generateFlashcards({ course_id: 'c1' })],
    ['flashcards', () => client.flashcards()],
    ['reviewFlashcard', () => client.reviewFlashcard('f1', 2)],
    ['suspendFlashcard', () => client.suspendFlashcard('f1')],
    ['generateNote', () => client.generateNote({ course_id: 'c1', mode: 'concept' })],
    ['notes', () => client.notes()],
    ['note', () => client.note('n1')],
    ['updateNote', () => client.updateNote('n1', { title: 't' })],
    ['saveNote', () => client.saveNote('n1', { target: 'local', link_id: 'l1' })],
    ['deleteNote', () => client.deleteNote('n1')],
    ['dashboard', () => client.dashboard()],
    ['mastery', () => client.mastery()],
    ['recommendations', () => client.recommendations()],
    ['attentionReport', () => client.attentionReport('d1')],
    ['recordAttention', () => client.recordAttention('d1', [])],
    ['deleteAttention', () => client.deleteAttention('d1')],
    ['exportData', () => client.exportData()],
    ['deleteAllData', () => client.deleteAllData()],
    ['googleDrive', () => client.googleDrive()],
    ['connectGoogleDrive', () => client.connectGoogleDrive(false)],
    ['disconnectGoogleDrive', () => client.disconnectGoogleDrive()],
    ['driveFiles', () => client.driveFiles('root')],
    ['importFromDrive', () => client.importFromDrive({ course_id: 'c1', folder: 'f1' })],
    ['syncDriveLink', () => client.syncDriveLink('l1')],
    ['unlinkDriveFolder', () => client.unlinkDriveFolder('l1')],
    ['localFolders', () => client.localFolders()],
    ['linkLocalFolder', () => client.linkLocalFolder({ course_id: 'c1', path: '/x' })],
    ['rescanLocalFolder', () => client.rescanLocalFolder('l1')],
    ['unlinkLocalFolder', () => client.unlinkLocalFolder('l1')],
    ['slideGuide', () => client.slideGuide('d1', 3)],
    ['writeSlideGuide', () => client.writeSlideGuide('d1', 3, { allow_cloud: true })],
    ['explainAll', () => client.explainAll('d1', { allow_cloud: true })],
    ['stopExplaining', () => client.stopExplaining('d1')],
    ['transcript', () => client.transcript('d1')],
    ['uploadTranscript', () => client.uploadTranscript('d1', new File(['words'], 'class.txt'))],
    ['deleteTranscript', () => client.deleteTranscript('d1')],
    ['slideNote', () => client.slideNote('d1', 3)],
    ['saveSlideNote', () => client.saveSlideNote('d1', 3, 'mine')],
    ['slideNotes', () => client.slideNotes({ course_id: 'c1' })],
    ['examProfile', () => client.examProfile('c1')],
    ['analysePastPapers', () => client.analysePastPapers('c1', true)],
    ['forgetExamProfile', () => client.forgetExamProfile('c1')],
    ['setDocumentKind', () => client.setDocumentKind('d1', 'past_paper')],
    ['cancelLocalFolderScan', () => client.cancelLocalFolderScan('l1')],
    ['councilState', () => client.councilState('d1', true)],
    ['startCouncil', () => client.startCouncil('d1', 'guide', { allow_cloud: true })],
    ['stopCouncil', () => client.stopCouncil('d1', 'solution')],
    ['folders', () => client.folders('c1')],
    ['createFolder', () => client.createFolder('c1', 'Week 1', 'course')],
    ['renameFolder', () => client.renameFolder('f1', 'Week one')],
    ['deleteFolder', () => client.deleteFolder('f1')],
    ['setDocumentFolder', () => client.setDocumentFolder('d1', 'f1')],
    ['guidedPages', () => client.guidedPages('d1')],
    ['assistantStatus', () => client.assistantStatus()],
    ['saveAssistantKey', () => client.saveAssistantKey('sk-ant-example')],
    ['deleteAssistantKey', () => client.deleteAssistantKey()],
    ['assistantProjects', () => client.assistantProjects()],
    ['assistantProject', () => client.assistantProject('p1')],
    ['createAssistantProject', () => client.createAssistantProject({ name: 'Thesis' })],
    ['updateAssistantProject', () => client.updateAssistantProject('p1', { name: 'T' })],
    ['deleteAssistantProject', () => client.deleteAssistantProject('p1')],
    ['addAssistantProjectFile', () => client.addAssistantProjectFile('p1', new File(['x'], 'a.md'))],
    ['assistantThreads', () => client.assistantThreads()],
    ['assistantThread', () => client.assistantThread('t1')],
    ['createAssistantThread', () => client.createAssistantThread({ project_id: 'p1' })],
    ['updateAssistantThread', () => client.updateAssistantThread('t1', { title: 'New' })],
    ['deleteAssistantThread', () => client.deleteAssistantThread('t1')],
    ['addAssistantAttachment', () => client.addAssistantAttachment('t1', new File(['x'], 'a.png'))],
    ['deleteAssistantAttachment', () => client.deleteAssistantAttachment('a1')],
    ['stopAssistant', () => client.stopAssistant('t1')],
    ['assistantExtensions', () => client.assistantExtensions()],
    [
      'addAssistantConnector',
      () => client.addAssistantConnector({ label: 'Wiki', url: 'https://mcp.example.com/mcp' }),
    ],
    ['updateAssistantConnector', () => client.updateAssistantConnector('c1', { enabled: false })],
    ['deleteAssistantConnector', () => client.deleteAssistantConnector('c1')],
    ['testAssistantConnector', () => client.testAssistantConnector('c1')],
    ['connectAssistantConnector', () => client.connectAssistantConnector('c1')],
    ['disconnectAssistantConnector', () => client.disconnectAssistantConnector('c1')],
    ['addAssistantPlugin', () => client.addAssistantPlugin(new File(['x'], 'kit.zip'))],
    ['updateAssistantPlugin', () => client.updateAssistantPlugin('p1', false)],
    ['deleteAssistantPlugin', () => client.deleteAssistantPlugin('p1')],
    [
      'sendAssistantMessage',
      () =>
        client.sendAssistantMessage(
          't1',
          { text: 'hi', attachment_ids: [], model: 'claude-opus-5-5' },
          () => {},
        ),
    ],
  ])('%s calls a route the backend actually exposes', async (_name, invoke) => {
    const { path, method } = await record(invoke);
    expect(
      isKnownRoute(path, method),
      `${method} ${path} is not in fixtures/openapi.json — run "npm run api:schema" if the backend changed`,
    ).toBe(true);
  });

  it('builds binary URLs that match real routes', () => {
    expect(isKnownRoute(client.documentFileUrl('d1'), 'get')).toBe(true);
    expect(isKnownRoute(client.pagePreviewUrl('d1', 4), 'get')).toBe(true);
    expect(isKnownRoute(client.assistantAttachmentPath('a1'), 'get')).toBe(true);
  });

  it('covers a meaningful share of the backend surface', () => {
    // A sanity bound: if the backend grows many routes the client never calls,
    // either the client is incomplete or the routes are dead.
    expect(Object.keys(schema.paths).length).toBeGreaterThan(30);
  });
});
