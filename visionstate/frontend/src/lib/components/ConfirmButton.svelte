<script lang="ts">
  // Two-step button for destructive actions: first click arms it, second click confirms.
  import type { Snippet } from 'svelte';

  let {
    onconfirm,
    confirmLabel = 'Click again to confirm',
    class: className = 'btn danger',
    disabled = false,
    children,
  }: {
    onconfirm: () => void;
    confirmLabel?: string;
    class?: string;
    disabled?: boolean;
    children: Snippet;
  } = $props();

  let armed = $state(false);
  let timer: ReturnType<typeof setTimeout>;

  function click() {
    if (armed) {
      armed = false;
      clearTimeout(timer);
      onconfirm();
    } else {
      armed = true;
      timer = setTimeout(() => (armed = false), 4000);
    }
  }
</script>

<button type="button" class={className} {disabled} onclick={click}>
  {#if armed}{confirmLabel}{:else}{@render children()}{/if}
</button>
