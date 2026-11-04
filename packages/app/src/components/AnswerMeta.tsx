/**
 * The honesty strip under every answer.
 *
 * It states three things the student cannot otherwise know: whether a language
 * model wrote the text or it was quoted from their material, whether the
 * retrieved evidence actually supports it, and which provider ran — including
 * whether that provider sent their work off the device.
 */
import { Badge, Callout } from '@studypilot/ui';
import { AlertTriangle, CloudOff, FileText, Quote, ShieldCheck, Sparkles } from 'lucide-react';

export interface AnswerMetaProps {
  grounded: boolean;
  generative: boolean;
  abstained: boolean;
  provider: string;
  model: string;
  evidenceSufficient: boolean;
  evidenceNote?: string;
  sourceCount: number;
  warnings?: string[];
  inputTokens?: number | null;
  outputTokens?: number | null;
}

const PROVIDER_LABELS: Record<string, string> = {
  extractive: 'Quoted from your material',
  ollama: 'Local model',
  anthropic: 'Anthropic (cloud)',
  openai: 'OpenAI (cloud)',
  abstain: 'Declined to answer',
  unavailable: 'Provider unavailable',
  policy: 'Blocked by your policy',
};

export function AnswerMeta({
  grounded,
  generative,
  abstained,
  provider,
  model,
  evidenceSufficient,
  evidenceNote,
  sourceCount,
  warnings = [],
  inputTokens,
  outputTokens,
}: AnswerMetaProps) {
  const providerLabel = PROVIDER_LABELS[provider] ?? provider;
  const tokens =
    inputTokens || outputTokens
      ? `${inputTokens ?? 0} in / ${outputTokens ?? 0} out tokens`
      : null;

  return (
    <div className="mt-3 space-y-2">
      <div className="flex flex-wrap items-center gap-1.5">
        {abstained ? (
          <Badge tone="caution" icon={<AlertTriangle size={11} />}>
            Did not answer
          </Badge>
        ) : grounded ? (
          <Badge
            tone="grounded"
            icon={<ShieldCheck size={11} />}
            title="Supported by your material, with at least one verified citation."
          >
            Grounded in {sourceCount} source{sourceCount === 1 ? '' : 's'}
          </Badge>
        ) : (
          <Badge
            tone="caution"
            icon={<AlertTriangle size={11} />}
            title="Check this against the cited pages before relying on it."
          >
            Not fully grounded
          </Badge>
        )}

        {generative ? (
          <Badge tone="info" icon={<Sparkles size={11} />} title={model || undefined}>
            Written by a model
          </Badge>
        ) : (
          <Badge
            tone="neutral"
            icon={<Quote size={11} />}
            title="These words come from your own documents. Nothing was rewritten."
          >
            Quoted, not explained
          </Badge>
        )}

        <Badge
          tone="neutral"
          icon={provider === 'extractive' || provider === 'ollama' ? <CloudOff size={11} /> : undefined}
          title={model || undefined}
        >
          {providerLabel}
        </Badge>

        {tokens ? (
          <Badge tone="neutral" icon={<FileText size={11} />}>
            {tokens}
          </Badge>
        ) : null}
      </div>

      {!evidenceSufficient && evidenceNote ? (
        <Callout tone="caution" icon={<AlertTriangle size={14} />}>
          {evidenceNote}
        </Callout>
      ) : null}

      {warnings.length > 0 ? (
        <Callout tone="info" icon={<AlertTriangle size={14} />}>
          <ul className="space-y-0.5">
            {warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </Callout>
      ) : null}
    </div>
  );
}
