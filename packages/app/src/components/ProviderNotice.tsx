/**
 * Tells the student plainly what AI is, or is not, configured.
 *
 * This exists because the honest default — no model installed — produces source
 * quoting rather than explanations, and the app must never let that pass as
 * tutoring.
 */
import { Button, Callout } from '@studypilot/ui';
import { CloudOff, Sparkles } from 'lucide-react';
import { Link } from 'react-router-dom';

import { useProviders } from '../hooks/queries';

export function ProviderNotice({ compact = false }: { compact?: boolean }) {
  const { data } = useProviders();
  if (!data) return null;
  if (data.active_is_generative) {
    const active = data.llm.find((item) => item.key === data.active_llm);
    if (!active?.sends_data_off_device) return null;
    return (
      <Callout tone="info" icon={<Sparkles size={14} />}>
        <strong>{active.label}</strong> is handling explanations. Your lecture text and questions
        are sent to that provider.
      </Callout>
    );
  }

  const installable = data.llm.filter((item) => item.key !== 'extractive');

  return (
    <Callout
      tone="caution"
      icon={<CloudOff size={14} />}
      title="No AI model is configured"
      action={
        compact ? null : (
          <Link to="/settings">
            <Button size="sm">Set one up</Button>
          </Link>
        )
      }
    >
      <p>
        Search, citations, quizzes and progress tracking all work. Explanations do not: answers
        quote the most relevant lines from your own documents instead of teaching them.
      </p>
      {!compact ? (
        <ul className="mt-1.5 space-y-0.5">
          {installable.map((item) => (
            <li key={item.key}>
              <span className="font-medium">{item.label}</span> — {item.detail}
            </li>
          ))}
        </ul>
      ) : null}
    </Callout>
  );
}
