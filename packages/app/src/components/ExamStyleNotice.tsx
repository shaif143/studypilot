/**
 * Says, where practice is generated, whether it will follow the course's
 * past papers — and lets that be switched off for one go.
 *
 * With papers analysed, questions take the real exam's forms and marks and
 * concentrate on the topics most likely to come up. Without them, this points
 * at where to add some, once, quietly.
 */
import { Callout } from '@studypilot/ui';
import { GraduationCap } from 'lucide-react';
import { Link } from 'react-router-dom';

import { useExamProfile } from '../hooks/queries';

export function useExamStyleAvailable(courseId: string | undefined): boolean {
  const profile = useExamProfile(courseId);
  const body = profile.data?.profile;
  return Boolean(body && (body.topics.length > 0 || Object.keys(body.question_mix).length > 0));
}

export function ExamStyleNotice({
  courseId,
  enabled,
  onChange,
  className,
}: {
  courseId: string | undefined;
  enabled: boolean;
  onChange: (next: boolean) => void;
  className?: string;
}) {
  const profile = useExamProfile(courseId);
  if (!courseId || !profile.data) return null;
  const body = profile.data.profile;

  if (!body) {
    return (
      <p className={`text-[0.75rem] text-[var(--text-secondary)] ${className ?? ''}`}>
        {profile.data.papers.length > 0
          ? 'This course has past papers that have not been analysed yet. '
          : 'Want questions like the real exam? '}
        <Link to={`/courses/${courseId}/exams`} className="text-[var(--text-accent)] underline">
          {profile.data.papers.length > 0 ? 'Analyse them' : 'Add past exam papers'}
        </Link>
        .
      </p>
    );
  }

  const top = body.topics.slice(0, 4).map((topic) => topic.topic);
  return (
    <Callout
      tone={enabled ? 'accent' : 'neutral'}
      className={className}
      icon={<GraduationCap size={14} />}
      title={enabled ? 'Following your past papers' : 'Not following your past papers'}
    >
      <div className="flex flex-wrap items-start gap-3">
        <p className="min-w-0 flex-1">
          {enabled ? (
            <>
              {body.format.summary || 'Question forms and marks match the real exam.'}
              {top.length > 0 ? ` Most likely topics first: ${top.join(', ')}.` : ''}{' '}
              <Link to={`/courses/${courseId}/exams`} className="underline">
                See the forecast
              </Link>
            </>
          ) : (
            'A general mix from the whole course, in the question types you pick.'
          )}
        </p>
        <label className="flex shrink-0 cursor-pointer items-center gap-1.5 text-[0.75rem] font-medium text-[var(--text-primary)]">
          <input
            type="checkbox"
            className="accent-[var(--color-accent-600)]"
            checked={enabled}
            onChange={(event) => onChange(event.target.checked)}
          />
          Follow my past papers
        </label>
      </div>
    </Callout>
  );
}
