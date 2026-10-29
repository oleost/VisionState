<script lang="ts">
  // The stream a sound sensor listens to: a camera's RTSP stream (with its microphone switched on),
  // a go2rtc restream, or an HTTP audio stream such as an ESP32 microphone sending WAV.
  let { source = $bindable('') }: { source: string } = $props();
</script>

<label class="field">
  Stream with sound
  <input
    class="input mono"
    bind:value={source}
    placeholder="rtsp://user:pass@192.168.1.20:554/stream1"
    autocomplete="off"
    autocapitalize="off"
    spellcheck="false"
  />
</label>
<p class="xsmall muted">
  A camera's RTSP stream (switch its microphone on in the camera), a go2rtc restream
  (<span class="mono">rtsp://homeassistant.local:8554/name</span>: one connection to the camera for everyone), or an HTTP
  audio stream such as an ESP32 microphone sending WAV (<span class="mono">http://esp32.local/sound.wav</span>). A camera's
  sub stream is enough: the sound is the same, and clips with video stay small.
</p>
