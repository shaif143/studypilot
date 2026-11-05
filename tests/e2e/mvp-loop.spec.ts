/**
 * The MVP loop, end to end, in a browser:
 *
 *   create a course → upload a lecture PDF → it indexes → ask a question →
 *   get an answer with a clickable citation → the citation opens the reader at
 *   the page → generate a quiz → answer it → it is marked → reload → the
 *   course, document and quiz history are still there.
 *
 * Runs against the offline providers, so the answer is an extractive quote
 * and the quiz is mechanical; the point is that every screen is wired to the
 * real API and nothing is a mock-up.
 */
import { expect, test } from '@playwright/test';
import { copyFileSync, mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const LECTURE = resolve(HERE, '../../fixtures/sample-lecture-cnn.pdf');

test.describe.configure({ mode: 'serial' });

test('a student can go from upload to a marked quiz and back', async ({ page }) => {
  // --- course ------------------------------------------------------------
  await page.goto('/courses');
  await page.getByRole('button', { name: 'New course' }).click();
  await page.getByLabel('Course title').fill('Deep Learning');
  await page.getByRole('button', { name: 'Create course' }).click();
  await expect(page.getByText('Deep Learning').first()).toBeVisible();

  // --- upload and index ---------------------------------------------------
  await page.goto('/library');
  await page.getByRole('button', { name: 'Upload', exact: true }).click();
  await page.locator('input[type="file"]').first().setInputFiles(LECTURE);
  await page.getByRole('button', { name: 'Upload and index' }).click();
  const openReader = page.getByRole('link', { name: 'Open reader' }).first();
  await expect(openReader).toBeVisible({ timeout: 60_000 });

  // --- reader shows the real PDF ----------------------------------------------
  await openReader.click();
  await expect(page).toHaveURL(/\/read\//);
  await expect(page.getByLabel('Page', { exact: true })).toHaveValue('1');
  const readerUrl = page.url();

  // --- a grounded, cited answer ---------------------------------------------
  await page.goto('/tutor');
  await page.getByPlaceholder(/Ask anything/).fill('Why is padding necessary?');
  const [asked] = await Promise.all([
    page.waitForResponse(
      (r) => r.request().method() === 'POST' && /\/api\/.*(ask|turn|chat|tutor)/.test(r.url()),
    ),
    page.getByRole('button', { name: 'Send' }).click(),
  ]);
  expect(asked.ok()).toBeTruthy();
  const citation = page.locator('button.sp-citation').first();
  await expect(citation).toBeVisible({ timeout: 60_000 });
  await citation.click();
  await expect(page).toHaveURL(/\/read\/.+\?page=\d+/);

  // --- a quiz, answered and marked ---------------------------------------------
  await page.goto('/quizzes');
  await page.getByRole('button', { name: 'Generate quiz' }).click();
  await expect(page.getByRole('button', { name: 'Submit for marking' })).toBeVisible({
    timeout: 60_000,
  });
  const radios = page.locator('input[type="radio"]');
  const names = new Set<string>();
  for (const radio of await radios.all()) {
    const name = (await radio.getAttribute('name')) ?? '';
    if (!names.has(name)) {
      names.add(name);
      await radio.check();
    }
  }
  for (const box of await page.locator('textarea').all()) await box.fill('padding');
  for (const box of await page.locator('input[type="text"], input[type="number"]').all()) {
    await box.fill('1');
  }
  await page.getByRole('button', { name: 'Submit for marking' }).click();
  await expect(page.getByText(/Marked: \d+ of \d+ marks/)).toBeVisible({ timeout: 60_000 });

  // --- it is all still there after a reload ------------------------------------
  await page.goto('/library');
  await expect(page.getByRole('link', { name: 'Open reader' }).first()).toBeVisible();
  await page.goto(readerUrl);
  await expect(page.getByLabel('Page', { exact: true })).toHaveValue('1');
  await page.goto('/quizzes');
  await expect(page.getByText(/Previous quizzes/i)).toBeVisible();
});

const DECK = resolve(HERE, '../../fixtures/sample-lecture-cnn.pptx');
const BRIEF = resolve(HERE, '../../fixtures/sample-assignment-brief.pdf');

test('a PowerPoint deck, the concept map and the assignment coach are all wired', async ({
  page,
}) => {
  // --- a .pptx is imported and read like a PDF ----------------------------
  await page.goto('/library');
  await page.getByRole('button', { name: 'Upload', exact: true }).click();
  await page.locator('input[type="file"]').first().setInputFiles(DECK);
  await page.getByRole('button', { name: 'Upload and index' }).click();
  const deckRow = page.getByText('sample-lecture-cnn', { exact: true }).first();
  await expect(deckRow).toBeVisible({ timeout: 60_000 });
  await page.getByRole('link', { name: 'Open reader' }).first().click();
  await expect(page).toHaveURL(/\/read\//);
  await expect(page.getByLabel('Page', { exact: true }).locator('option')).toHaveCount(9);

  // --- the concept map is drawn from the course's topics ---------------------
  await page.goto('/courses');
  await page.getByRole('link', { name: 'Map' }).first().click();
  await expect(page).toHaveURL(/\/courses\/.+\/map/);
  const diagram = page.getByTestId('mermaid-diagram');
  await expect(diagram.locator('svg')).toBeVisible({ timeout: 30_000 });
  expect(await diagram.locator('svg').innerHTML()).toContain('Padding');
  await expect(page.getByText('Not enough evidence yet').first()).toBeVisible();

  // --- an assignment brief gets a guide, and the coach asks for a model ---------
  await page.goto('/courses');
  await page.getByRole('link', { name: 'Assignments' }).first().click();
  await page.locator('input[type="file"]').setInputFiles(BRIEF);
  await page.getByRole('button', { name: /Upload/ }).first().click();
  await expect(page.getByRole('button', { name: 'Draft the guide' })).toBeEnabled({
    timeout: 60_000,
  });
  await page.getByRole('button', { name: 'Draft the guide' }).click();
  const checkButton = page.getByRole('button', { name: 'Check my attempt' }).first();
  await expect(checkButton).toBeVisible({ timeout: 60_000 });
  await checkButton.click();
  await page.getByPlaceholder(/What have you tried/).fill('I would use a 5x5 kernel and no padding.');
  await page.getByRole('button', { name: 'Check it' }).click();
  // No model is configured in this run: the coach must say so rather than pretend.
  await expect(page.getByText(/needs a language model/)).toBeVisible({ timeout: 30_000 });

  // --- cloud keys are entered on this machine, never shown back -----------------
  // (Nothing is saved here: that would write to the real Keychain of the
  // machine running the tests.)
  await page.goto('/settings');
  await expect(page.getByText('Keys for cloud providers')).toBeVisible();
  await expect(page.getByText('Claude (Anthropic) API key')).toBeVisible();
  await expect(page.getByPlaceholder('sk-ant-…')).toHaveAttribute('type', 'password');
});

test('a whole folder is uploaded at once and Google Drive is offered, not faked', async ({
  page,
}) => {
  // --- a folder: two lectures taken, a stray text file reported ----------------
  const folder = mkdtempSync(join(tmpdir(), 'studypilot-folder-'));
  const week1 = join(folder, 'week-1.pdf');
  const week2 = join(folder, 'week-2.pdf');
  copyFileSync(LECTURE, week1);
  copyFileSync(LECTURE, week2);
  writeFileSync(join(folder, 'notes.txt'), 'not a lecture');

  // A course of its own, so the spec also runs on an empty installation.
  await page.goto('/courses');
  await page.getByRole('button', { name: 'New course' }).click();
  await page.getByLabel('Course title').fill('Folder course');
  await page.getByRole('button', { name: 'Create course' }).click();
  await expect(page.getByText('Folder course').first()).toBeVisible();

  await page.goto('/library');
  await page.getByRole('button', { name: 'Upload', exact: true }).click();
  await page.getByLabel('Choose a folder').setInputFiles(folder);
  await expect(page.getByText('2 files,')).toBeVisible();
  await expect(page.getByText('notes.txt')).toBeVisible();
  await expect(page.getByText('Not a supported type.')).toBeVisible();
  // Both go into the new course; only the chooser needs telling when there
  // is more than one course.
  const courseSelect = page.getByLabel('Course', { exact: true });
  if (await courseSelect.count()) await courseSelect.selectOption({ label: 'Folder course' });
  await page.getByRole('button', { name: 'Upload and index 2 files' }).click();
  // The second copy is the same bytes: the app says so rather than duplicating.
  await expect(page.getByText(/of 2 files indexed, 1 not taken/)).toBeVisible({
    timeout: 90_000,
  });
  await expect(page.getByText('already here').first()).toBeVisible();

  // --- Drive: the app offers the connection and says what is missing -----------
  await page.getByRole('button', { name: 'Google Drive' }).click();
  await expect(page.getByText('Connect Google Drive first')).toBeVisible();
  await expect(page.getByText('not set up on this installation')).toBeVisible();
  await page.getByRole('button', { name: 'Close' }).click();

  await page.goto('/settings');
  const card = page.locator('section, div').filter({ hasText: 'Google Drive' }).last();
  await expect(card.getByText('One-time setup')).toBeVisible();
  await expect(page.getByText('OAuth client ID', { exact: true }).last()).toBeVisible();
  // No client, no Connect button: nothing claims to work that cannot.
  await expect(page.getByRole('button', { name: 'Connect Google Drive' })).toHaveCount(0);
});
