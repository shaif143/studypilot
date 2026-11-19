/**
 * Nothing sticks out, at any window size.
 *
 * A layout that is sized by the window, and not by the room the page really
 * has beside the sidebar, looks right on the author's screen and pushes a
 * button out of its card on a smaller one. That is how a course's delete
 * button came to sit outside its card. This walks every main screen at a
 * range of widths, from a small phone to a wide monitor and including the
 * Mac window's minimum, and asks the page itself whether anything is wider
 * than the box it is in.
 *
 * What counts as sticking out:
 *  - the page scrolls sideways;
 *  - an element's content is wider than the element, and the element neither
 *    scrolls nor clips (a table in its own scrolling box is fine; a row of
 *    buttons spilling out of a card is not);
 *  - an element lies partly outside the window with nothing clipping it.
 */
import { expect, test } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const LECTURE = resolve(HERE, '../../fixtures/sample-lecture-cnn.pdf');

/** Small phone, phone, tablet, the Mac window's minimum, its default, a wide monitor. */
const WIDTHS = [360, 420, 768, 960, 1100, 1320, 1700];

test.describe.configure({ mode: 'serial' });

let routes: string[] = [];

test.beforeAll(async ({ browser }) => {
  // Long names on purpose: they are what breaks a layout.
  const page = await browser.newPage();
  const api = page.request;
  const course = await (
    await api.post('/api/courses', {
      data: {
        title: 'Signals, Audio Processing and Pattern Recognition',
        code: 'EEEM030',
        institution: 'University of Somewhere With A Long Name',
        description: 'Speech and audio processing, feature extraction, and recognition.',
      },
    })
  ).json();
  const upload = async (title: string, kind: string) =>
    (
      await api.post(`/api/courses/${course.id}/documents`, {
        multipart: {
          file: { name: `${title}.pdf`, mimeType: 'application/pdf', buffer: readFileSync(LECTURE) },
          title,
          kind,
        },
      })
    ).json();
  const lecture = await upload('Lecture 7 Convolutional Neural Networks and Their Applications', 'lecture');
  await api.post(`/api/courses/${course.id}/folders`, {
    data: { name: 'Week 1 Introduction and Background Reading', section: 'course' },
  });
  await api.post('/api/flashcards/generate', { data: { course_id: course.id } });
  // Answered, so the analytics screen has estimates to draw and not only its empty state.
  const quiz = await (
    await api.post('/api/assessments/generate', { data: { course_id: course.id, kind: 'quiz' } })
  ).json();
  await api.post(`/api/assessments/${quiz.id}/submit`, {
    data: {
      responses: (quiz.questions as Array<{ id: string }>).map((question) => ({
        question_id: question.id,
        answer: 'convolution shares weights across positions',
      })),
    },
  });
  await api.post('/api/notes/generate', { data: { course_id: course.id, mode: 'concept' } });
  await api.post('/api/assistant/projects', {
    data: {
      name: 'Dissertation on trajectory planning for medical robots',
      description: 'Notes, drafts and reading, with a description long enough to wrap.',
    },
  });
  expect(lecture.document?.id, 'the lecture was indexed').toBeTruthy();
  await page.close();

  routes = [
    '/',
    '/courses',
    `/courses/${course.id}`,
    `/study/${lecture.document.id}?page=3`,
    `/courses/${course.id}/assignments`,
    `/courses/${course.id}/map`,
    `/courses/${course.id}/exams`,
    `/library?course=${course.id}`,
    `/read/${lecture.document.id}`,
    `/tutor?course=${course.id}`,
    '/flashcards',
    '/quizzes',
    '/exams',
    '/notes',
    '/analytics',
    '/settings',
    '/claude',
    '/claude/projects',
    '/claude/extensions',
    '/plugins',
  ];
});

for (const width of WIDTHS) {
  test(`nothing sticks out of its box at ${width}px wide`, async ({ page }) => {
    await page.setViewportSize({ width, height: 800 });
    await page.goto('/courses');
    await expect(page.getByRole('heading', { name: 'My Courses' })).toBeVisible();

    const problems = await page.evaluate(async (paths) => {
      const audit = (): string[] => {
        const windowWidth = document.documentElement.clientWidth;
        const describe = (el: Element) =>
          `<${el.tagName.toLowerCase()} class="${(el.getAttribute('class') ?? '').slice(0, 70)}"> ` +
          `"${((el as HTMLElement).innerText ?? '').trim().slice(0, 50).replace(/\n/g, ' ')}"`;
        const clippedByAncestor = (el: Element) => {
          for (let parent = el.parentElement; parent; parent = parent.parentElement) {
            if (getComputedStyle(parent).overflowX !== 'visible') return true;
          }
          return false;
        };
        const hits: Array<{ el: Element; message: string }> = [];
        if (document.documentElement.scrollWidth > windowWidth + 1) {
          hits.push({ el: document.body, message: 'the page scrolls sideways' });
        }
        for (const el of document.querySelectorAll('body *')) {
          // Drawn things size themselves; a closed dialog is not on screen.
          if (el.closest('.katex, svg, canvas, dialog:not([open])')) continue;
          const style = getComputedStyle(el);
          if (style.display === 'none' || style.display === 'inline') continue;
          if (style.visibility === 'hidden') continue;
          const box = el.getBoundingClientRect();
          if (!box.width || !box.height) continue;
          if (
            el.scrollWidth > el.clientWidth + 1 &&
            style.overflowX === 'visible' &&
            el.clientWidth > 0
          ) {
            hits.push({
              el,
              message: `content ${el.scrollWidth - el.clientWidth}px wider than ${describe(el)}`,
            });
          } else if ((box.right > windowWidth + 1 || box.left < -1) && !clippedByAncestor(el)) {
            hits.push({ el, message: `outside the window: ${describe(el)}` });
          }
        }
        // The innermost offender is the cause; everything around it only inherits it.
        const causes = hits.filter(
          (hit) => !hits.some((other) => other.el !== hit.el && hit.el.contains(other.el)),
        );
        return [...new Set(causes.map((hit) => hit.message))].slice(0, 6);
      };

      const found: Record<string, string[]> = {};
      for (const path of paths) {
        window.history.pushState({}, '', path);
        window.dispatchEvent(new PopStateEvent('popstate'));
        await new Promise((done) => setTimeout(done, 1200));
        const here = audit();
        if (here.length > 0) found[path] = here;
      }
      return found;
    }, routes);

    expect(problems, `at ${width}px`).toEqual({});
  });
}

test('on a narrow window the sidebar slides over the page and closes when used', async ({
  page,
}) => {
  await page.setViewportSize({ width: 420, height: 800 });
  await page.goto('/courses');
  const sidebar = page.getByRole('complementary', { name: 'Main navigation' });
  await expect(sidebar).toBeHidden();
  // The page has the whole window, not what a column beside it would leave.
  expect((await page.locator('main').boundingBox())?.width).toBe(420);

  await page.getByRole('button', { name: 'Show sidebar' }).click();
  await expect(sidebar).toBeVisible();
  await sidebar.getByRole('link', { name: 'Settings' }).click();
  await expect(page).toHaveURL(/\/settings$/);
  await expect(sidebar).toBeHidden();

  // Widened, it is a column again and the page sits beside it.
  await page.setViewportSize({ width: 1100, height: 800 });
  await expect(sidebar).toBeVisible();
  expect((await page.locator('main').boundingBox())?.width).toBe(1100 - 240);
});
