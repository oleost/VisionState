<script lang="ts">
  // Before face recognition is turned on for a sensor: what it does, what is stored and what is
  // downloaded, said plainly. Nothing is downloaded until the user confirms. The model's name,
  // licence, size and source come from the backend (GET /settings).
  import { size } from '../format';
  import type { FaceModelInfo } from '../types';
  import ConsentSheet from './ConsentSheet.svelte';

  let {
    name,
    model,
    onclose,
    onconfirm,
  }: {
    /** The sensor's name ("" while it is being made). */
    name: string;
    model: FaceModelInfo | null;
    onclose: () => void;
    onconfirm: () => void;
  } = $props();
</script>

<ConsentSheet
  title={`Recognise faces${name ? ` on ${name}` : ''}?`}
  confirm={model?.installed ? 'Turn on' : 'Download and turn on'}
  {onclose}
  {onconfirm}
>
  <li>
    Faces are looked for only in the people this sensor finds, and compared with faces <strong>you</strong> name — the
    people are shared: those you named on other cameras are known here too.
  </li>
  <li>
    Stored on this Home Assistant only, in the app's own storage (not the media folder, and in its backups): a small
    picture of each face you teach. Faces of people you have not named are not kept, apart from the normal history
    frames. The people you teach should know about it.
  </li>
  {#if model}
    <li>
      Needs the face model <em>{model.name}</em> with {model.detector.name} ({model.license} and {model.detector.license}{model.installed
        ? ', already downloaded'
        : `, ${size(model.size)} downloaded once from Hugging Face`}). {model.data} {model.detector.data}
    </li>
  {/if}
  <li>Cameras that see a street or a neighbour's door may need their consent where you live.</li>
  <li>It is not a lock: a photo of a face looks like that face. Do not unlock doors or disarm alarms on it alone.</li>
</ConsentSheet>
