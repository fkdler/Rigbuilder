<script setup lang="ts">
withDefaults(defineProps<{ monochrome?: boolean }>(), { monochrome: false });
</script>

<template>
  <div class="ascii-logo-large" :class="{ 'ascii-logo-large--white': monochrome }" aria-label="RigBuilder">
    <pre class="ascii-logo__art"><span class="logo-line">██████╗ ██╗ ██████╗ ██████╗ ██╗   ██╗██╗██╗     ██████╗ ███████╗██████╗</span>
<span class="logo-line">██╔══██╗██║██╔════╝ ██╔══██╗██║   ██║██║██║     ██╔══██╗██╔════╝██╔══██╗</span>
<span class="logo-line">██████╔╝██║██║  ███╗██████╔╝██║   ██║██║██║     ██║  ██║█████╗  ██████╔╝</span>
<span class="logo-line">██╔══██╗██║██║   ██║██╔══██╗██║   ██║██║██║     ██║  ██║██╔══╝  ██╔══██╗</span>
<span class="logo-line">██║  ██║██║╚██████╔╝██████╔╝╚██████╔╝██║███████╗██████╔╝███████╗██║  ██║</span>
<span class="logo-line">╚═╝  ╚═╝╚═╝ ╚═════╝ ╚═════╝  ╚═════╝ ╚═╝╚══════╝╚═════╝ ╚══════╝╚═╝  ╚═╝</span></pre>
  </div>
</template>

<style scoped>
.ascii-logo-large {
  user-select: none;
  padding: var(--space-lg) 0;
  overflow: hidden;
}

.ascii-logo__art {
  margin: 0;
  font-family: var(--font-mono);
  font-size: 0.45rem;
  line-height: 1.2;
  letter-spacing: -0.05em;
  white-space: pre;
  word-break: keep-all;
}

.logo-line { color: var(--color-accent); }

@media (max-width: 1280px) {
  .ascii-logo__art {
    font-size: 0.42rem;
  }
}

@media (max-width: 1024px) {
  .ascii-logo__art {
    font-size: 0.38rem;
  }
}
.ascii-logo-large--white .logo-line { background: none; color: #fff; -webkit-text-fill-color: #fff; animation: none; }
.ascii-logo-large--white::after { display: none; }
</style>
