/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string;
  /** The Neon Auth branch URL. Absent when using the built-in accounts. */
  readonly VITE_NEON_AUTH_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

/** pdf.js ships its worker as an asset; Vite's ?url suffix needs declaring. */
declare module '*?url' {
  const url: string;
  export default url;
}
