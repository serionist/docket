/**
 * The style nonce the server writes into the document for each response (DR-0706). The page's CSP lets a style
 * element in only with it: the three Radix inserts, the scroll lock's (through get-nonce, set once at start) and
 * Select's and ScrollArea's (passed to their viewports by the wrappers in components/ui).
 */
import { setNonce } from "get-nonce";

// The value the build leaves in place of a nonce; the service writes a fresh one over it for each response.
export const PLACEHOLDER = "docket-nonce";

let read: string | undefined;

/** The document's nonce, read once from its meta element; undefined where there is none to read. */
export function nonce(): string | undefined {
  if (read === undefined) {
    const found = document.querySelector<HTMLMetaElement>('meta[name="csp-nonce"]')?.content;
    read = found && found !== PLACEHOLDER ? found : "";
  }
  return read || undefined;
}

/** The nonce for a component that inserts a style element. */
export function useNonce(): string | undefined {
  return nonce();
}

/** Gives the scroll lock's style element the nonce, before anything locks the scroll. */
export function installNonce(): void {
  const found = nonce();
  if (found) setNonce(found);
}

/** Forgets the nonce read, so a test can give the document another. */
export function resetNonce(): void {
  read = undefined;
}
