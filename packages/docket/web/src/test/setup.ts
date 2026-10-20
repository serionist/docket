import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(() => {
  cleanup();
  document.documentElement.removeAttribute("data-keys");
  document.documentElement.removeAttribute("data-theme");
});

// jsdom lays nothing out, so it has no scrolling into view; the pages ask for it as the selection moves.
if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => undefined;

// jsdom has no ResizeObserver; cmdk's list, under the who fields, sizes itself with one.
if (!("ResizeObserver" in globalThis)) {
  globalThis.ResizeObserver = class {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  };
}
