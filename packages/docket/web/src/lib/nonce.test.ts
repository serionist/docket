import { getNonce } from "get-nonce";
import { afterEach, describe, expect, it } from "vitest";
import { installNonce, nonce, PLACEHOLDER, resetNonce } from "./nonce";

function meta(content: string): void {
  const element = document.createElement("meta");
  element.name = "csp-nonce";
  element.content = content;
  document.head.append(element);
}

afterEach(() => {
  document.head.replaceChildren();
  resetNonce();
});

describe("the style nonce", () => {
  it("is the one the server wrote into the document, given to the scroll lock's style element", () => {
    meta("bm9uY2Utb2YtdGhlLXRlc3Q=");
    installNonce();
    expect(nonce()).toBe("bm9uY2Utb2YtdGhlLXRlc3Q=");
    expect(getNonce()).toBe("bm9uY2Utb2YtdGhlLXRlc3Q=");
  });

  it("is none where the build's placeholder stands, as under the dev server", () => {
    meta(PLACEHOLDER);
    expect(nonce()).toBeUndefined();
  });

  it("is none in a document without the element", () => {
    expect(nonce()).toBeUndefined();
  });
});
