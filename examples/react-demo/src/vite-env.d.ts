/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_DEMO_TOKEN?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
