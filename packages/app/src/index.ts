export { StudyPilotApp } from './StudyPilotApp';
export type { StudyPilotAppProps } from './StudyPilotApp';
export { AppProvider, useApp, useClient } from './context';
export * from './hooks/queries';

// Presentation pieces other surfaces (and tests) legitimately reuse.
export { GroundedAnswer } from './components/GroundedAnswer';
export type { GroundedAnswerProps } from './components/GroundedAnswer';
export { AnswerMeta } from './components/AnswerMeta';
export type { AnswerMetaProps } from './components/AnswerMeta';
export { CitationList } from './components/CitationList';
export { ProviderNotice } from './components/ProviderNotice';
export { TutorPanel } from './components/TutorPanel';
export type { TutorPanelProps } from './components/TutorPanel';
