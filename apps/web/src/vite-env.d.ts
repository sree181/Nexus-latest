/** Build-time configuration Vite inlines into the bundle.
 *
 *  Declared rather than pulled from `vite/client` so the set stays a closed,
 *  typed list: anything reaching for a variable that is not here is a
 *  compile error instead of `undefined` at runtime. Nothing secret belongs
 *  in any of them — they ship to the browser.
 */
interface ImportMetaEnv {
  /** OIDC issuer, e.g. https://login.microsoftonline.com/<tenant>/v2.0.
   *  Unset means no provider, and the app runs unauthenticated and says so. */
  readonly VITE_OIDC_ISSUER?: string;
  /** This SPA's client id. A public client: there is no secret. */
  readonly VITE_OIDC_CLIENT_ID?: string;
  readonly VITE_OIDC_SCOPE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
