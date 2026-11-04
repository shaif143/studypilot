/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

/** pdf.js ships its worker as an asset; Vite's ?url suffix needs declaring. */
declare module '*?url' {
  const url: string;
  export default url;
}
