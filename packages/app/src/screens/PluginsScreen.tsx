/**
 * Plugins, in the Mac app.
 *
 * On the website plugins live beside connectors in the Claude chat. The Mac
 * app has no chat, but a plugin's commands work in every question box, so the
 * same list is reachable here from the box's "+" menu.
 */
import { Spinner } from '@studypilot/ui';

import { ErrorPanel, PageBody, PageHeader } from '../components/AppShell';
import { Plugins } from '../components/assistant/ExtensionsView';
import { describeError, useAssistantExtensions } from '../hooks/queries';

export function PluginsScreen() {
  const extensions = useAssistantExtensions();

  return (
    <>
      <PageHeader
        title="Plugins"
        description="Add a plugin and type a slash in any question box to use its commands."
      />
      <PageBody>
        <div className="mx-auto w-full max-w-3xl">
          {extensions.isPending ? (
            <div className="flex justify-center py-16 text-[var(--text-muted)]">
              <Spinner size={16} />
            </div>
          ) : extensions.isError ? (
            <ErrorPanel
              message={describeError(extensions.error)}
              onRetry={() => void extensions.refetch()}
            />
          ) : (
            <Plugins plugins={extensions.data.plugins} />
          )}
        </div>
      </PageBody>
    </>
  );
}
