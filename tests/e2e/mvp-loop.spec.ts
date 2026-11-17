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
  // A citation opens the cited slide in the study view, explained beside its original.
  await expect(page).toHaveURL(/\/study\/.+\?page=\d+/);

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
  // Let the screen finish its first load: choosing a file into an input the
  // next render replaces is how this step was flaky.
  await expect(page.getByText('No assignment briefs yet')).toBeVisible();
  await page.locator('input[type="file"]').setInputFiles(BRIEF);
  const uploadBrief = page.getByRole('button', { name: 'Upload brief' });
  await expect(uploadBrief).toBeEnabled();
  await uploadBrief.click();
  await expect(page.getByRole('button', { name: 'Draft a coaching plan' })).toBeEnabled({
    timeout: 60_000,
  });

  // --- the four models, the references and the two steps are all on the brief ----
  await expect(page.getByText('Work it out with all your models')).toBeVisible();
  const seats = page.getByRole('list', { name: 'Models' }).getByRole('listitem');
  await expect(seats).toHaveCount(4);
  await expect(seats).toContainText(['Claude', 'ChatGPT', 'Gemini', 'Local model']);
  // This run has no cloud model, so there is no one to discuss with: the two
  // steps are offered but cannot start, and the screen says what is missing.
  await expect(page.getByText(/A discussion needs at least two models/)).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Work out the step-by-step guide' }),
  ).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Complete the assignment' })).toBeDisabled();
  await page.getByRole('button', { name: /References:/ }).click();
  await expect(page.getByRole('checkbox', { name: /sample-lecture-cnn/ }).first()).toBeChecked();
  await page.getByRole('button', { name: /References:/ }).click();

  // --- a folder in the assignments section, and the brief filed in it ------------
  await page.getByRole('button', { name: 'New folder' }).click();
  await page.getByRole('textbox', { name: 'Folder name' }).fill('Coursework 1');
  await page.getByRole('button', { name: 'Create folder' }).click();
  await expect(page.getByRole('heading', { name: 'Coursework 1' })).toBeVisible();
  await expect(page.getByText('Empty. Open a brief and choose this folder for it.')).toBeVisible();
  await page.getByRole('combobox', { name: /^Folder for / }).selectOption({ label: 'Coursework 1' });
  await expect(page.getByText('Empty. Open a brief and choose this folder for it.')).toHaveCount(0);
  await expect(page.getByText('1 file', { exact: true })).toBeVisible();

  await page.getByRole('button', { name: 'Draft a coaching plan' }).click();
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
  await expect(page.getByText('Google Drive is not set up yet')).toBeVisible();
  await expect(page.getByText('This installation has no Google client')).toBeVisible();
  await page.getByRole('button', { name: 'Close' }).click();

  await page.goto('/settings');
  const card = page.locator('section, div').filter({ hasText: 'Google Drive' }).last();
  await expect(card.getByText('One-time setup')).toBeVisible();
  await expect(page.getByText('OAuth client ID', { exact: true }).last()).toBeVisible();
  // No client, no Connect button: nothing claims to work that cannot.
  await expect(page.getByRole('button', { name: 'Connect Google Drive' })).toHaveCount(0);
});

test('a course opens as slides, a slide opens explained beside its original', async ({ page }) => {
  // --- the dashboard keeps only the courses, folded ---------------------------
  await page.goto('/');
  await expect(page.getByText('Indexed passages')).toHaveCount(0);
  await expect(page.getByText('Cards due')).toHaveCount(0);
  const accordion = page.getByRole('button', { name: /^Courses/ });
  await expect(accordion).toHaveAttribute('aria-expanded', 'false');
  await accordion.click();
  await expect(accordion).toHaveAttribute('aria-expanded', 'true');
  // The panel the button controls, not "Continue studying", which names the course too.
  const panel = page.locator(`[id="${await accordion.getAttribute('aria-controls')}"]`);
  await panel.getByRole('link', { name: /Deep Learning/ }).first().click();
  await expect(page).toHaveURL(/\/courses\/[0-9a-f]+$/);

  // --- Open on a course card lands on the same slide list ----------------------
  await page.goto('/courses');
  await page.getByRole('link', { name: 'Open', exact: true }).first().click();
  await expect(page).toHaveURL(/\/courses\/[0-9a-f]+$/);
  await expect(page.getByText(/slides\. Open a lecture, then a slide/)).toBeVisible();
  // The sidebar is the dashboard, the courses and the study tools: files and
  // questions are reached from the course they belong to.
  const nav = page.getByRole('navigation');
  await expect(nav.getByRole('link', { name: 'Lecture Library' })).toHaveCount(0);
  await expect(nav.getByRole('link', { name: 'AI Tutor' })).toHaveCount(0);
  await expect(page.getByRole('link', { name: 'Ask about this course' })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Past exams' })).toBeVisible();

  // --- a folder in the course: made here, and a lecture filed in it ---------------
  await page.getByRole('button', { name: 'New folder' }).click();
  await page.getByRole('textbox', { name: 'Folder name' }).fill('Week 7');
  await page.getByRole('button', { name: 'Create folder' }).click();
  const week = page.getByRole('region', { name: 'Folder Week 7' });
  await expect(week.getByRole('heading', { name: 'Week 7' })).toBeVisible();
  await expect(week.getByRole('link', { name: 'Add files here' })).toHaveAttribute(
    'href',
    /\/library\?course=.+&folder=.+/,
  );

  // --- a lecture starts folded, with "Explain all slides" beside its arrow -------
  const lecture = page.getByRole('button', { name: /^Open / }).first();
  await expect(lecture).toHaveAttribute('aria-expanded', 'false');
  await expect(page.getByRole('link', { name: /Padding/ })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Explain all slides' }).first()).toBeVisible();
  await lecture.click();
  await expect(page.getByText('Class video transcription').first()).toBeVisible();
  // Filed in the folder from inside the open lecture; it moves under the folder's heading.
  await page
    .getByRole('combobox', { name: /^Folder for / })
    .first()
    .selectOption({ label: 'Week 7' });
  await expect(week.getByRole('button', { name: /^Open |^Close / }).first()).toBeVisible();
  await week.getByRole('button', { name: /^Open / }).first().click();
  const padding = page.getByRole('link', { name: /Padding/ }).first();
  await expect(padding).toBeVisible({ timeout: 30_000 });
  await padding.click();
  await expect(page).toHaveURL(/\/study\/[0-9a-f]+\?page=4/);

  // --- the study view: explanation, original, and the prompt -------------------
  await expect(page.getByLabel('Original slide')).toBeVisible();
  await expect(page.getByLabel('Slide', { exact: true })).toHaveValue('4');
  // No model in this run, so what comes back is the slide's own text, said to be that.
  await expect(page.getByText(/slide’s own text|slide's own text/).first()).toBeVisible({
    timeout: 30_000,
  });
  await expect(page.getByText('Overview').first()).toBeVisible();

  await page.getByLabel('Ask about this slide').fill('Why is padding necessary?');
  await page.getByRole('button', { name: 'Ask', exact: true }).click();
  await expect(page.getByText('Your questions on this slide')).toBeVisible({ timeout: 60_000 });
  await expect(page.locator('button.sp-citation').first()).toBeVisible({ timeout: 60_000 });

  // --- the sidebar folds away and comes back, and the choice is remembered --------
  const sidebar = page.getByRole('complementary', { name: 'Main navigation' });
  const askBox = page.getByLabel('Ask about this slide');
  await expect(sidebar).toBeVisible();
  await expect(askBox).toBeInViewport({ ratio: 1 }); // the bottom bar is fully on screen
  await page.getByRole('button', { name: 'Hide sidebar' }).click();
  await expect(sidebar).toBeHidden();
  await expect(askBox).toBeInViewport({ ratio: 1 });
  await page.reload();
  await expect(page.getByLabel('Original slide')).toBeVisible();
  await expect(sidebar).toBeHidden();
  await page.keyboard.press('ControlOrMeta+Backslash');
  await expect(sidebar).toBeVisible();
  await page.keyboard.press('ControlOrMeta+Backslash');
  await page.getByRole('button', { name: 'Show sidebar' }).click();
  await expect(sidebar).toBeVisible();
  await expect(page.getByRole('button', { name: 'Hide sidebar' })).toHaveAttribute(
    'aria-expanded',
    'true',
  );

  // --- a note typed under the original is kept with the slide --------------------
  const note = page.getByRole('textbox', { name: 'Your notes on this slide' });
  await note.fill('Same padding keeps the size.');
  await expect(page.getByText('Saved', { exact: true })).toBeVisible({ timeout: 15_000 });

  // --- "Study this" goes straight to practice on this lecture --------------------
  await page.getByRole('button', { name: 'Study this' }).click();
  await expect(page.getByRole('menuitem', { name: /Flashcards/ })).toBeVisible();
  await expect(page.getByRole('menuitem', { name: /Mock exam/ })).toBeVisible();
  await expect(page.getByRole('menuitem', { name: /My notes/ })).toBeVisible();
  await expect(page.getByRole('menuitem', { name: /Quiz/ })).toHaveAttribute(
    'href',
    /\/quizzes\?course=.+&document=.+&from=4&to=4/,
  );
  await page.keyboard.press('Escape');

  // --- arrows turn slides; the question stays with the slide it was asked on ---
  await page.getByRole('button', { name: 'Next slide' }).click();
  await expect(page).toHaveURL(/page=5/);
  await expect(page.getByText('Your questions on this slide')).toHaveCount(0);
  await expect(note).toHaveValue('');
  await page.getByRole('button', { name: 'Previous slide' }).click();
  await expect(note).toHaveValue('Same padding keeps the size.');

  // --- past papers have a place of their own in the course ----------------------
  await page.getByRole('link', { name: /All slides/ }).click();
  await page.getByRole('link', { name: 'Past exams' }).click();
  await expect(page).toHaveURL(/\/courses\/[0-9a-f]+\/exams$/);
  await expect(page.getByRole('heading', { name: 'Past exam papers' })).toBeVisible();
  await expect(page.getByText('No forecast without papers')).toBeVisible();
});
