/**
 * A dialog opened by a key or a page's own button, with no trigger of its own, gives the focus back to what had it
 * when it opened, so the keys and Tab carry on from there; Radix's own return goes to a trigger it doesn't have.
 * Given to a dialog's onCloseAutoFocus.
 */
export function returnFocus(event: Event, opener: Element | null): void {
  if (opener instanceof HTMLElement && opener.isConnected && opener !== document.body) {
    event.preventDefault();
    opener.focus({ preventScroll: true });
  }
}
