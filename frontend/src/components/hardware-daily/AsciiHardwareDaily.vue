<template>
  <div class="daily-logo" aria-label="HARDWARE DAILY">
    <pre class="daily-logo__art"><span>██╗  ██╗ █████╗ ██████╗ ██████╗ ██╗    ██╗ █████╗ ██████╗ ███████╗</span>
<span>██║  ██║██╔══██╗██╔══██╗██╔══██╗██║    ██║██╔══██╗██╔══██╗██╔════╝</span>
<span>███████║███████║██████╔╝██║  ██║██║ █╗ ██║███████║██████╔╝█████╗</span>
<span>██╔══██║██╔══██║██╔══██╗██║  ██║██║███╗██║██╔══██║██╔══██╗██╔══╝</span>
<span>██║  ██║██║  ██║██║  ██║██████╔╝╚███╔███╔╝██║  ██║██║  ██║███████╗</span>
<span>╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝╚═════╝  ╚══╝╚══╝ ╚═╝  ╚═╝╚═╝  ╚═╝╚══════╝</span></pre>
    <pre class="daily-logo__art daily-logo__art--daily"><span>██████╗  █████╗ ██╗██╗  ██╗   ██╗</span>
<span>██╔══██╗██╔══██╗██║██║  ╚██╗ ██╔╝</span>
<span>██║  ██║███████║██║██║   ╚████╔╝</span>
<span>██║  ██║██╔══██║██║██║    ╚██╔╝</span>
<span>██████╔╝██║  ██║██║███████╗██║</span>
<span>╚═════╝ ╚═╝  ╚═╝╚═╝╚══════╝╚═╝</span></pre>
  </div>
</template>

<style scoped>
.daily-logo { display: grid; gap: 5px; overflow: hidden; user-select: none; }
.daily-logo__art { margin: 0; color: var(--color-accent); font-family: var(--font-mono); font-size: clamp(.56rem, 1.05vw, .86rem); line-height: 1.2; letter-spacing: -.05em; white-space: pre; word-break: keep-all; }
.daily-logo__art--daily { font-size: clamp(.6rem, 1.12vw, .92rem); }
@media (max-width: 760px) {
  .daily-logo__art { font-size: clamp(.32rem, 1.36vw, .43rem); }
  .daily-logo__art--daily { font-size: clamp(.35rem, 1.48vw, .47rem); }
}
</style>
