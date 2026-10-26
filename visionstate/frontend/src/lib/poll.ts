// Polling that rests while the page is hidden (a background tab, the Home Assistant app put away).

/** Calls `fn` every `ms` while the page is visible, and when it is shown again after a poll was
 * missed (not more often than every `ms`). Returns a function that stops it. */
export function pollWhileVisible(ms: number, fn: () => void): () => void {
  let last = Date.now();
  const run = () => {
    last = Date.now();
    fn();
  };
  const timer = setInterval(() => {
    if (document.visibilityState === 'visible') run();
  }, ms);
  const onVisible = () => {
    if (document.visibilityState === 'visible' && Date.now() - last >= ms) run();
  };
  document.addEventListener('visibilitychange', onVisible);
  return () => {
    clearInterval(timer);
    document.removeEventListener('visibilitychange', onVisible);
  };
}
