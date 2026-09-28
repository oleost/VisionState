<script lang="ts">
  let { size = 28 }: { size?: number } = $props();
</script>

<svg width={size} height={size} viewBox="0 0 28 28" fill="none" aria-hidden="true">
  <rect x="1" y="1" width="26" height="26" rx="8" fill="var(--c-accent)" />
  <path
    d="M6 14c2.4-4 5-6 8-6s5.6 2 8 6c-2.4 4-5 6-8 6s-5.6-2-8-6z"
    stroke="var(--c-accent-ink)"
    stroke-width="2"
    stroke-linejoin="round"
  />
  <circle cx="14" cy="14" r="3" fill="var(--c-accent-ink)" />
</svg>
