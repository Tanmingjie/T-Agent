// 平台化 T-P11:当前用户 / 当前项目的本地会话(localStorage)。
// 一期 header 透传用户名(X-User);后端单机模式(未配 AuthProvider)忽略它仍可用。
// M4 接 IDaaS 后换成真实登录态,这层接口不变。

const USER_KEY = "tagent.user";
const PROJECT_KEY = "tagent.project";

export function getUser(): string {
  return localStorage.getItem(USER_KEY) || "";
}

export function setUser(user: string): void {
  if (user) localStorage.setItem(USER_KEY, user);
  else localStorage.removeItem(USER_KEY);
}

export function getProjectId(): string {
  return localStorage.getItem(PROJECT_KEY) || "";
}

export function setProjectId(pid: string): void {
  if (pid) localStorage.setItem(PROJECT_KEY, pid);
  else localStorage.removeItem(PROJECT_KEY);
}

/** 注入到每个请求的鉴权 header(空用户则不带,留给单机模式隐式放行)。 */
export function authHeaders(): Record<string, string> {
  const u = getUser();
  return u ? { "X-User": u } : {};
}

/** 给列表类请求附加 project_id query(已选项目时);未选则原样(单机/平台管理员看全部)。 */
export function withProject(path: string): string {
  const pid = getProjectId();
  if (!pid) return path;
  const sep = path.includes("?") ? "&" : "?";
  return `${path}${sep}project_id=${encodeURIComponent(pid)}`;
}
