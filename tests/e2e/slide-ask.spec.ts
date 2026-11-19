/**
 * The "+" beside the question box on a slide.
 *
 * A question can carry files, photos, a folder and a GitHub issue, and the
 * box offers commands that write a question for you. This run has no AI
 * model, so what is checked is the part that does not need one: the menu,
 * what is taken and what is refused, the commands, and that the question
 * shows what was sent with it and says plainly that it was not read.
 */
import { expect, test } from '@playwright/test';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const LECTURE = resolve(HERE, '../../fixtures/sample-lecture-cnn.pdf');

test.describe.configure({ mode: 'serial' });

let studyPath = '';

test.beforeAll(async ({ browser }) => {
  const page = await browser.newPage();
  const course = await (
    await page.request.post('/api/courses', { data: { title: 'Attachments course', code: 'ATT1' } })
  ).json();
  const lecture = await (
    await page.request.post(`/api/courses/${course.id}/documents`, {
      multipart: {
        file: { name: 'cnn.pdf', mimeType: 'application/pdf', buffer: readFileSync(LECTURE) },
        title: 'Convolutional networks',
        kind: 'lecture',
      },
    })
  ).json();
  expect(lecture.document?.id, 'the lecture was indexed').toBeTruthy();
  studyPath = `/study/${lecture.document.id}?page=3`;
  await page.close();
});

test('files, a command and a question go from the plus menu to the conversation', async ({
  page,
}) => {
  await page.goto(studyPath);
  const box = page.getByLabel('Ask about this slide');
  await expect(box).toBeVisible();

  // --- the menu -------------------------------------------------------------------
  const plus = page.getByRole('button', { name: 'Add files, photos and more' });
  await plus.click();
  const menu = page.getByRole('menu', { name: 'Add to this question' });
  await expect(menu.getByRole('menuitem')).toHaveText([
    /Add files or photos/,
    'Add folder',
    'Import GitHub issue',
    'Slash commands',
    'Plugins',
  ]);
  await page.keyboard.press('Escape');
  await expect(menu).toBeHidden();

  // --- attaching: what is taken, and what is refused by name -------------------------
  await page.locator('input[aria-label="Files or photos to attach"]').setInputFiles([
    { name: 'lab-note.md', mimeType: 'text/markdown', buffer: Buffer.from('# Lab note\nPadding.') },
    { name: 'deck.pptx', mimeType: 'application/octet-stream', buffer: Buffer.from('PK') },
  ]);
  const waiting = page.getByRole('list', { name: 'Attached to this question' });
  await expect(waiting.getByRole('listitem')).toHaveText(['lab-note.md']);
  await expect(page.getByText(/deck\.pptx cannot be read with a question/)).toBeVisible();
  await page.getByRole('button', { name: 'Remove lab-note.md' }).click();
  await expect(waiting).toBeHidden();

  // A folder brings its housekeeping file with it; that is left out without a word.
  const folder = test.info().outputPath('coursework');
  mkdirSync(folder, { recursive: true });
  writeFileSync(resolve(folder, '.DS_Store'), 'x');
  writeFileSync(resolve(folder, 'solver.py'), 'print("padding")\n');
  writeFileSync(resolve(folder, 'readme.md'), '# Read me\n');
  await page.locator('input[aria-label="Folder to attach"]').setInputFiles(folder);
  await expect(waiting.getByRole('listitem')).toHaveCount(2);
  await expect(waiting).toContainText('solver.py');
  await expect(waiting).toContainText('readme.md');

  // --- commands ---------------------------------------------------------------------
  await plus.click();
  await menu.getByRole('menuitem', { name: 'Slash commands' }).click();
  await expect(box).toHaveValue('/');
  await expect(box).toBeFocused();
  const commands = page.getByRole('list', { name: 'Commands' });
  await expect(commands.getByRole('listitem')).toHaveCount(7);
  await box.pressSequentially('qu');
  await expect(commands.getByRole('listitem')).toHaveText([/\/quiz/]);
  await commands.getByRole('button', { name: /\/quiz/ }).click();
  await expect(box).toHaveValue(/^Ask me three short questions that test this slide/);

  // --- sending ----------------------------------------------------------------------
  await box.fill('Why is padding necessary?');
  await page.getByRole('button', { name: 'Ask', exact: true }).click();
  await expect(page.getByText('Your questions on this slide')).toBeVisible({ timeout: 60_000 });
  const sent = page.getByRole('list', { name: 'Attached', exact: true });
  await expect(sent.getByRole('listitem')).toHaveCount(2);
  await expect(sent).toContainText('solver.py');
  await expect(sent).toContainText('readme.md');
  // No model here to read them, and the answer says so rather than pretending.
  await expect(page.getByText(/attachments were not read/).first()).toBeVisible({
    timeout: 60_000,
  });
  // The box is empty again, ready for the next question.
  await expect(box).toHaveValue('');
  await expect(waiting).toBeHidden();
});

test('on a phone the menu opens inside the window', async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 740 });
  await page.goto(studyPath);
  await page.getByRole('button', { name: 'Add files, photos and more' }).click();
  const menu = page.getByRole('menu', { name: 'Add to this question' });
  await expect(menu).toBeVisible();
  const box = await menu.boundingBox();
  expect(box?.x).toBeGreaterThanOrEqual(0);
  expect((box?.x ?? 0) + (box?.width ?? 0)).toBeLessThanOrEqual(360);
  expect(box?.y).toBeGreaterThanOrEqual(0);
  // Plugins live beside connectors on the website.
  await menu.getByRole('menuitem', { name: 'Plugins' }).click();
  await expect(page).toHaveURL(/\/claude\/extensions$/);
});
