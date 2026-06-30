import React from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import { ThemeProvider } from "./context/ThemeContext";
import { I18nProvider } from "./lib/i18n";
import "./style.css";

const favicon = document.querySelector<HTMLLinkElement>("link[rel='icon']") ?? document.createElement("link");
favicon.rel = "icon";
favicon.type = "image/png";
favicon.href = "/logo/logolight.png";
document.head.appendChild(favicon);

createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <ThemeProvider>
      <I18nProvider>
        <App />
      </I18nProvider>
    </ThemeProvider>
  </React.StrictMode>,
);
