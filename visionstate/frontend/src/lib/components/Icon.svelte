<script lang="ts" module>
  // Every icon in the app, as stroke paths on a 16×16 grid.
  const ICONS = {
    plus: 'M8 3v10M3 8h10',
    upload: 'M8 10V2M5 5l3-3 3 3M2.5 10.5v2a1 1 0 0 0 1 1h9a1 1 0 0 0 1-1v-2',
    download: 'M8 2v8M5 7l3 3 3-3M2.5 10.5v2a1 1 0 0 0 1 1h9a1 1 0 0 0 1-1v-2',
    check: 'M3 8.5l3 3 7-7',
    x: 'M4 4l8 8M12 4l-8 8',
    pause: 'M6 4v8M10 4v8',
    play: 'M5 3.5v9l7.5-4.5z',
    camera: 'M2 5.5A1.5 1.5 0 0 1 3.5 4h1.8l1-1.5h3.4l1 1.5h1.8A1.5 1.5 0 0 1 14 5.5v6a1.5 1.5 0 0 1-1.5 1.5h-9A1.5 1.5 0 0 1 2 11.5zM8 10.8a2.3 2.3 0 1 0 0-4.6 2.3 2.3 0 0 0 0 4.6',
    trash: 'M3 4.5h10M6.5 4.5V3h3v1.5M4.5 4.5l.6 8.5h5.8l.6-8.5',
    refresh: 'M13 8a5 5 0 1 1-1.5-3.5M13 2.5v3h-3',
    chevron: 'M6 3l5 5-5 5',
    'chevron-down': 'M3 6l5 5 5-5',
    'chevron-up': 'M3 10l5-5 5 5',
    back: 'M10 3L5 8l5 5',
    alert: 'M8 2l6.5 11.5h-13zM8 6.5v3M8 11.5v.01',
    settings: 'M8 10a2 2 0 1 0 0-4 2 2 0 0 0 0 4M8 1.5v2M8 12.5v2M1.5 8h2M12.5 8h2M3.4 3.4l1.4 1.4M11.2 11.2l1.4 1.4M3.4 12.6l1.4-1.4M11.2 4.8l1.4-1.4',
    film: 'M2.5 3h11v10h-11zM5 3v10M11 3v10M2.5 6h2.5M2.5 10h2.5M11 6h2.5M11 10h2.5',
    frame: 'M2 5V2h3M11 2h3v3M14 11v3h-3M5 14H2v-3',
    eye: 'M1.5 8c1.8-3.3 4-5 6.5-5s4.7 1.7 6.5 5c-1.8 3.3-4 5-6.5 5S3.3 11.3 1.5 8zM8 10a2 2 0 1 0 0-4 2 2 0 0 0 0 4',
    bulb: 'M6 12.5h4M6.5 14.5h3M8 1.5a4.5 4.5 0 0 0-2.7 8.1c.4.3.7.8.7 1.4v1.5h4V11c0-.6.3-1.1.7-1.4A4.5 4.5 0 0 0 8 1.5',
    draw: 'M2 5V2h3M11 2h3v3M14 11v3h-3M5 14H2v-3M8 5.5v5M5.5 8h5',
    tag: 'M2 2.5v5l6.5 6.5 5-5L7 2.5zM5 5h.01',
  } as const;
  export type IconName = keyof typeof ICONS;
</script>

<script lang="ts">
  let { name, size = 16, strokeWidth = 1.6 }: { name: IconName; size?: number; strokeWidth?: number } = $props();
</script>

<svg
  width={size}
  height={size}
  viewBox="0 0 16 16"
  fill="none"
  stroke="currentColor"
  stroke-width={strokeWidth}
  stroke-linecap="round"
  stroke-linejoin="round"
  aria-hidden="true"
>
  <path d={ICONS[name]} />
</svg>
