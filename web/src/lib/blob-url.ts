// Blob URLs returned by the API point straight at the backend
// (`{PUBLIC_BASE_URL}/blobs/{org_id}/{key}`), and that route requires an auth
// header. A bare `<img src>` / `<video src>` cannot attach one, so every such
// request 401s and nothing renders.
//
// `blobUrl()` rewrites those absolute URLs to a same-origin path handled by
// `src/app/blobs/[...path]/route.ts`, which re-issues the request server-side
// with the auth headers attached. Pure string transform: everything from
// `/blobs/` onwards is kept. Non-string input (the API returns `null` for
// `primary_asset_url` / `logo_url`) passes straight through.
export function blobUrl<T extends string | null | undefined>(url: T): T {
  if (typeof url !== 'string') return url;
  const i = url.indexOf('/blobs/');
  return (i === -1 ? url : url.slice(i)) as T;
}
