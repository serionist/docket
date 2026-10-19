import "./styles/app.css";
import { QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { installKeys } from "@/lib/keys";
import { installNonce } from "@/lib/nonce";
import { applyKeysCookie } from "@/lib/prefs";
import { createQueryClient } from "@/lib/query";
import { createAppRouter } from "@/router";

// Before anything renders: the scroll lock's style element takes the nonce, and the keys switch its cookie.
installNonce();
applyKeysCookie();
installKeys();

const router = createAppRouter();
const queries = createQueryClient();
const root = document.getElementById("root");
if (root) {
  createRoot(root).render(
    <StrictMode>
      <QueryClientProvider client={queries}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </StrictMode>,
  );
}
