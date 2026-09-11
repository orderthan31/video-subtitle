let csrfToken = '';
let generation = 0;
export const sessionEvents = new EventTarget();

export function setCsrfToken(token = '') {
  csrfToken = token;
  generation++;
}

export async function authenticatedFetch(url: string, init: RequestInit = {}, login = false) {
  const current = generation;
  const headers = new Headers(init.headers);
  if (csrfToken && !login) headers.set('X-Session-Token', csrfToken);
  if (csrfToken && !['GET', 'HEAD', 'OPTIONS'].includes((init.method || 'GET').toUpperCase())) {
    headers.set('X-CSRF-Token', csrfToken);
  }
  const response = await fetch(url, {...init, headers, credentials: 'include', cache: 'no-store'});
  if (response.status === 401 && !login && current === generation && csrfToken) {
    setCsrfToken();
    sessionEvents.dispatchEvent(new Event('expired'));
  }
  return response;
}
