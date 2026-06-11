// 平台化:当前上下文(项目 / 版本)。
//
// 入口模型(集成内网系统):内网维护项目与版本,登录后选定**项目**并跳转到本平台,
// URL 带 `?project=<id>`。本平台不登录、不建/管项目与版本(内网真相源),只接收并展示。
//   · 项目 = 内网跳转锁定,只读(从 URL 读一次,落 localStorage 续存)。
//   · 版本 = 在本平台内选择(版本列表来自后端 VersionRow),作用于版本级页面。
// 身份(X-User / token)留壳给 M4 IDaaS,本期不传。

const PROJECT_KEY = "tagent.project";
const VERSION_KEY = "tagent.version";

// 进入时若 URL 带 ?project=,以它为准并落库(覆盖旧值);本地开发手动拼此参数。
(function initProjectFromUrl() {
  try {
    const p = new URLSearchParams(window.location.search).get("project");
    if (p) localStorage.setItem(PROJECT_KEY, p);
  } catch {
    /* SSR / 无 window 时忽略 */
  }
})();

export function getProjectId(): string {
  return localStorage.getItem(PROJECT_KEY) || "";
}

export function setProjectId(pid: string): void {
  if (pid) localStorage.setItem(PROJECT_KEY, pid);
  else localStorage.removeItem(PROJECT_KEY);
}

export function getVersionId(): string {
  return localStorage.getItem(VERSION_KEY) || "";
}

export function setVersionId(vid: string): void {
  if (vid) localStorage.setItem(VERSION_KEY, vid);
  else localStorage.removeItem(VERSION_KEY);
}

/** 鉴权 header 占位:M4 接 IDaaS 后填真实身份;本期单机/过渡期不带,后端隐式放行。 */
export function authHeaders(): Record<string, string> {
  return {};
}

/** 给列表类请求附加 project_id query(已选项目时);未选则原样(单机/平台管理员看全部)。 */
export function withProject(path: string): string {
  const pid = getProjectId();
  if (!pid) return path;
  const sep = path.includes("?") ? "&" : "?";
  return `${path}${sep}project_id=${encodeURIComponent(pid)}`;
}
