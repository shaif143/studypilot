/**
 * Reading the two text-shaped teaching blocks: ```steps and ```quiz.
 *
 * Both are plain text with a line convention rather than JSON, so that the
 * LaTeX inside them needs no escaping. A block that does not follow the
 * convention yields nothing and the caller shows the text as it is.
 */

export interface Step {
  title: string;
  body: string;
}

/** `## Title` starts a step; everything up to the next one is its Markdown. */
export function parseSteps(source: string): Step[] {
  const steps: Step[] = [];
  let current: Step | null = null;
  for (const line of source.replace(/\r\n/g, '\n').split('\n')) {
    const heading = /^#{1,3}\s+(.+?)\s*$/.exec(line);
    if (heading) {
      if (current) steps.push(current);
      current = { title: (heading[1] ?? '').replace(/^step\s*\d+\s*[:.)-]\s*/i, ''), body: '' };
    } else if (current) {
      current.body += `${line}\n`;
    }
  }
  if (current) steps.push(current);
  return steps
    .map((step) => ({ title: step.title.trim().slice(0, 160), body: step.body.trim() }))
    .filter((step) => step.title.length > 0)
    .slice(0, 12);
}

export interface CheckItem {
  question: string;
  answer: string;
}

/** `Q:` starts a question and `A:` its answer; either may run over several lines. */
export function parseQuiz(source: string): CheckItem[] {
  const items: CheckItem[] = [];
  let current: CheckItem | null = null;
  let target: 'question' | 'answer' = 'question';
  for (const line of source.replace(/\r\n/g, '\n').split('\n')) {
    const question = /^\s*(?:\d+[.)]\s*)?Q\d*\s*[:.)]\s*(.*)$/i.exec(line);
    const answer = /^\s*A\d*\s*[:.)]\s*(.*)$/i.exec(line);
    if (question) {
      if (current) items.push(current);
      current = { question: question[1] ?? '', answer: '' };
      target = 'question';
    } else if (answer && current) {
      current.answer = answer[1] ?? '';
      target = 'answer';
    } else if (current) {
      current[target] += `\n${line}`;
    }
  }
  if (current) items.push(current);
  return items
    .map((item) => ({ question: item.question.trim(), answer: item.answer.trim() }))
    .filter((item) => item.question.length > 0 && item.answer.length > 0)
    .slice(0, 8);
}

export type CalloutTone = 'key' | 'mistake' | 'exam' | 'class';

/** The tone of a quote line that opens with one of the known labels. */
export function calloutTone(text: string): CalloutTone | null {
  const start = text.trim().slice(0, 40).toLowerCase();
  if (start.startsWith('key idea')) return 'key';
  if (start.startsWith('common mistake')) return 'mistake';
  if (start.startsWith('exam tip')) return 'exam';
  if (start.startsWith('in class')) return 'class';
  return null;
}
