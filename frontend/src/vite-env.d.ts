/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** API/SSE 根路径覆盖;设为 http://localhost:8000/api 可绕过 Vite 代理直连后端。 */
  readonly VITE_API_BASE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
