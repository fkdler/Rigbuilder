import { createRouter, createWebHistory } from "vue-router";

import ConversationTerminal from "@/views/ConversationTerminal.vue";
import { useAuthStore } from "@/stores/auth";

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: "/",
      name: "conversation-terminal",
      component: ConversationTerminal,
    },
    {
      // Keep the administrator workspace separate from the conversation surface.
      path: "/admin",
      name: "admin-dashboard",
      component: () => import("@/views/AdminWorkspace.vue"),
      meta: { requiresAdmin: true },
    },
  ],
});

/**
 * There is no login route, so a signed-out visitor is sent to the conversation page
 * -- the only page the application shows while signed out. Login opens on demand.
 * Returning `true` for "/" avoids a navigation loop.
 */
router.beforeEach((to) => {
  const auth = useAuthStore();
  if (!auth.isAuthenticated) return to.path === "/" ? true : { path: "/" };
  if (to.meta.requiresAdmin && !auth.isAdmin) return { path: "/" };
  if (auth.isAdmin && to.path === "/") return { path: "/admin" };
  return true;
});

export default router;
