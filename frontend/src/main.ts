import { createPinia } from "pinia";
import { createApp } from "vue";

import App from "./App.vue";
import router from "./router";
import { useAuthStore } from "./stores/auth";
import "./styles/terminal-tokens.css";
import "./assets/main.css";

const app = createApp(App);
const pinia = createPinia();

// The order of these four statements is load-bearing.
//
// `app.use(router)` resolves the *initial* route immediately, which runs the
// navigation guard. Installing it before the session had been restored meant a
// direct visit to /admin looked anonymous and was bounced to "/", so a signed-in
// reload of the console silently landed on the chat page.
//
// Pinia goes first because restoring a rejected session clears the conversation
// store, and that needs an active pinia. `bootstrap` never throws: a stale token
// simply leaves the store signed out and the login overlay takes over.
app.use(pinia);
await useAuthStore(pinia).bootstrap();
app.use(router);

app.mount("#app");
