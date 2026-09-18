<script setup lang="ts">
/**
 * Application shell.
 *
 * The account overlays live here, above the router view, because Plan_V4.5 §11.3
 * asks for sign-in to be part of the conversation page rather than a page of its
 * own: a signed-out visit still renders the terminal behind the modal, and no
 * "return to where you were" state has to be stored or restored.
 */
import { watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import LoginModal from "@/components/auth/LoginModal.vue";
import UserInfoModal from "@/components/auth/UserInfoModal.vue";
import { useAuthStore } from "@/stores/auth";

const auth = useAuthStore();
const route = useRoute();
const router = useRouter();

/**
 * A session can end while the user is on another page -- the token expires, or a
 * password change elsewhere revokes it. Falling back to the conversation page is
 * what puts the login overlay over a page that makes sense, instead of over the
 * operator console.
 */
watch(() => [auth.isAuthenticated, auth.isAdmin], () => {
  const destination = auth.isAuthenticated && auth.isAdmin ? "/admin" : "/";
  if (route.path !== destination) void router.replace(destination);
});
</script>

<template>
  <RouterView />
  <LoginModal v-if="!auth.isAuthenticated && auth.loginModalOpen" />
  <UserInfoModal v-else-if="auth.userModalOpen" />
</template>
