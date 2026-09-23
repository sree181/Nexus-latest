import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { SignInGate } from "./components/SignInGate";
import { queryClient } from "./lib/queryClient";
import { router } from "./router";
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      {/* outside the router: with a provider configured there is nothing to
          route to until we know who is asking */}
      <SignInGate>
        <RouterProvider router={router} />
      </SignInGate>
    </QueryClientProvider>
  </React.StrictMode>,
);
