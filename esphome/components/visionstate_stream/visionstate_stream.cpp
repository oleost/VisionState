#include "visionstate_stream.h"

#ifdef USE_ESP32

#include <esp_http_server.h>

#include <algorithm>
#include <cmath>
#include <cstring>

#include "esphome/core/alloc_helpers.h"
#include "esphome/core/application.h"
#include "esphome/core/hal.h"
#include "esphome/core/log.h"

#ifdef USE_VISIONSTATE_CAMERA
#include <driver/gpio.h>
#include <esp_attr.h>
#include <esp_camera.h>
#endif
#ifdef USE_VISIONSTATE_FLASH
#include <esp_heap_caps.h>
#include <jpeg_decoder.h>
#endif

namespace esphome::visionstate_stream {

static const char *const TAG = "visionstate_stream";

static const uint32_t SAMPLE_RATE = 16000;
static const size_t BYTES_PER_SECOND = SAMPLE_RATE * 2;     // mono, 16 bits
static const size_t CHUNK_BYTES = 1024;                     // 32 ms
static const uint32_t READ_WAIT_MS = 250;                   // a read that waits longer gives up for now
static const uint32_t SILENT_GIVE_UP_MS = 10000;            // no sound from the microphone this long: close
static const uint32_t IMAGE_TIMEOUT_MS = 5000;
// The main loop is woken, and the picture asked for again, this often while it is waited for (the
// first request after a picture was handed out is lost). A fresh snapshot waits mostly for the
// camera: its next picture comes no sooner than 1 / max_framerate after the last.
static const uint32_t WAKE_EVERY_MS = 10;
static const uint32_t LISTENER_TASK_STACK = 4096;
// The low-pass filter before taking a 48 kHz microphone down to 16 kHz: a windowed sinc, its edge
// just below the new Nyquist frequency (8 kHz), so nothing above it folds back into what is heard.
static const size_t DECIMATE_TAPS = 48;
static const float DECIMATE_CUTOFF_HZ = 7200.0f;
static const int32_t CLIP_LEVEL = 32700;  // a sample this loud counts as clipped
#ifdef USE_VISIONSTATE_CAMERA
// Pictures thrown away after a change, before the snapshot: the exposure is right in the next one,
// a new picture size needs a few (measured on an ESP32-CAM, 2026-11-05), and so does a new clock.
static const uint8_t SETTLE_PICTURES = 1;
static const uint8_t SETTLE_PICTURES_RESIZED = 3;
static const uint8_t SETTLE_PICTURES_CLOCK = 3;
// Before those, the pictures the camera took before the change and keeps: as many as it has frame
// buffers (frame_buffer_count, set by set_kept_pictures): one waits in ESPHome's queue, a second
// buffer holds the newest. They come at once (2026-11-16, two buffers: after 7 and 42 ms).
// The picture sizes a snapshot can ask for (?size=), up to the one the camera's buffers were made for.
struct PictureSize {
  const char *name;
  framesize_t size;
};
static const PictureSize PICTURE_SIZES[] = {{"640x480", FRAMESIZE_VGA},
                                            {"800x600", FRAMESIZE_SVGA},
                                            {"1024x768", FRAMESIZE_XGA},
                                            {"1280x1024", FRAMESIZE_SXGA},
                                            {"1600x1200", FRAMESIZE_UXGA}};
// How long one of the sensor's lines takes at a 20 MHz clock (twice as long at 10 MHz), in µs: an
// exposure in milliseconds becomes lines by it. Measured on an ESP32-CAM with an OV3660 (2026-11-16):
// a 1600x1200 picture every 70 ms at 20 MHz and every 143 ms at 10 MHz, 1248 lines each. Other
// chips, and smaller pictures: about.
static const float LINE_US_20MHZ = 56.0f;
static const int MAX_EXPOSURE_LINES = 1200;
// A camera that gives no picture this long after start-up is not working: the device restarts once
// (an ESP32-CAM's OV2640 gave none after a firmware install, until it was restarted: issue #86). The
// restart is remembered over it, in memory that survives a restart but not a power cut, so it
// happens once and not in a loop.
static const uint32_t NO_PICTURE_RESTART_MS = 30000;
static const uint32_t RESTARTED_MAGIC = 0x56534352;  // "VSCR"
static RTC_NOINIT_ATTR uint32_t restarted_for_camera;
#endif
#ifdef USE_VISIONSTATE_FLASH
// A snapshot with the flash (?flash=<ms>): pictures are taken and thrown away for that long after it
// came on, so the exposure and the white balance have caught up with it (one taken while the light
// still changed was dark or had a colour cast), at most this long. On an ESP32-CAM at 1600x1200 a
// picture takes 0.2-0.5 s, and the first one after the flash came on was lit already (2026-11-15).
static const uint32_t FLASH_SETTLE_MAX_MS = 10000;
static const uint32_t FLASH_ON_TIMEOUT_MS = 1000;  // the main loop switches it on
// How strong the flash is (&flash_level=<1-100>, percent of full) when a snapshot does not say: 35 %
// (the most light without the white of a meter's face burning out, measured close up, 2026-11-20).
// Full is too much close up: the meter's glass reflects it into the lens and the bright parts burn
// out, the LED gets hot and the board draws a lot (issue #86). VisionState asks for each sensor's.
static const int FLASH_LEVEL_DEFAULT = 35;

// How light a picture is: the mean of its pixels (0-255), decoded at 1/8 of its size (a 1600x1200
// picture as 200x150: quick, and little memory). -1 when it cannot be decoded.
static int picture_brightness(const uint8_t *jpeg, size_t length) {
  esp_jpeg_image_cfg_t cfg = {};
  cfg.indata = const_cast<uint8_t *>(jpeg);
  cfg.indata_size = length;
  cfg.out_format = JPEG_IMAGE_FORMAT_RGB888;
  cfg.out_scale = JPEG_IMAGE_SCALE_1_8;
  esp_jpeg_image_output_t info = {};
  if (esp_jpeg_get_image_info(&cfg, &info) != ESP_OK || info.output_len == 0)
    return -1;
  auto *out = static_cast<uint8_t *>(heap_caps_malloc(info.output_len, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
  if (out == nullptr)
    out = static_cast<uint8_t *>(malloc(info.output_len));  // NOLINT(cppcoreguidelines-no-malloc)
  if (out == nullptr)
    return -1;
  cfg.outbuf = out;
  cfg.outbuf_size = info.output_len;
  int brightness = -1;
  if (esp_jpeg_decode(&cfg, &info) == ESP_OK) {
    uint64_t sum = 0;
    for (size_t i = 0; i < info.output_len; i++)
      sum += out[i];
    brightness = static_cast<int>(sum / info.output_len);
  }
  free(out);  // NOLINT(cppcoreguidelines-no-malloc)
  return brightness;
}
#endif
// Sockets beside the listeners' (as __init__.py counts them): a snapshot, and an answer to a third
// listener, at the same time.
static const uint8_t SPARE_SOCKETS = 2;

// Every answer that is not a stream closes its connection: a client keeping it open (HTTP keep-alive:
// Home Assistant, a media player, VisionState's snapshots) would hold one of the few sockets, and with
// them all held no one else could connect. The server closes it once the handler has returned.
static esp_err_t answered(httpd_req_t *req) {
  httpd_sess_trigger_close(req->handle, httpd_req_to_sockfd(req));
  return ESP_OK;
}

static esp_err_t not_found(httpd_req_t *req, httpd_err_code_t err) {
  httpd_resp_set_hdr(req, "Connection", "close");
  httpd_resp_send_err(req, err, nullptr);
  return answered(req);
}

static void put_u32(uint8_t *p, uint32_t v) {
  p[0] = v & 0xff;
  p[1] = (v >> 8) & 0xff;
  p[2] = (v >> 16) & 0xff;
  p[3] = (v >> 24) & 0xff;
}
static void put_u16(uint8_t *p, uint16_t v) {
  p[0] = v & 0xff;
  p[1] = (v >> 8) & 0xff;
}

/// A WAV header for a stream with no end: the sizes are the largest there are, as FFmpeg expects.
static void wav_header(uint8_t *h) {
  memcpy(h, "RIFF", 4);
  put_u32(h + 4, 0xFFFFFFFF);
  memcpy(h + 8, "WAVEfmt ", 8);
  put_u32(h + 16, 16);              // fmt chunk size
  put_u16(h + 20, 1);               // PCM
  put_u16(h + 22, 1);               // mono
  put_u32(h + 24, SAMPLE_RATE);
  put_u32(h + 28, BYTES_PER_SECOND);
  put_u16(h + 32, 2);               // bytes per frame
  put_u16(h + 34, 16);              // bits per sample
  memcpy(h + 36, "data", 4);
  put_u32(h + 40, 0xFFFFFFFF);
}

void VisionStateStream::set_credentials(const std::string &username, const std::string &password) {
  this->password_ = password;
  std::string pair = username + ":" + password;
  this->expected_auth_ = "Basic " + base64_encode(reinterpret_cast<const uint8_t *>(pair.data()), pair.size());
}

void VisionStateStream::setup() {
#ifdef USE_VISIONSTATE_CAMERA
  this->image_ready_ = xSemaphoreCreateBinary();
  if (this->camera_ != nullptr)
    this->camera_->add_listener(this);
#if defined(USE_VISIONSTATE_CAMERA_POWER_DOWN) && defined(USE_OTA_STATE_LISTENER)
  ota::get_global_ota_callback()->add_global_state_listener(this);
#endif
#endif
#ifdef USE_VISIONSTATE_MICROPHONE
  if (this->decimate_ > 1) {
    const float pi = 3.14159265f;
    float fc = DECIMATE_CUTOFF_HZ / static_cast<float>(SAMPLE_RATE_OUT * this->decimate_);  // per input sample
    float sum = 0.0f;
    this->taps_.resize(DECIMATE_TAPS);
    for (size_t i = 0; i < DECIMATE_TAPS; i++) {
      float m = static_cast<float>(i) - (DECIMATE_TAPS - 1) / 2.0f;
      float sinc = m == 0.0f ? 2.0f * fc : sinf(2.0f * pi * fc * m) / (pi * m);
      float window = 0.54f - 0.46f * cosf(2.0f * pi * static_cast<float>(i) / (DECIMATE_TAPS - 1));  // Hamming
      this->taps_[i] = sinc * window;
      sum += this->taps_[i];
    }
    for (auto &tap : this->taps_)
      tap /= sum;  // unity gain for what is kept
    this->history_.assign(DECIMATE_TAPS, 0.0f);
  }
  this->microphone_source_->add_data_callback([this](const std::vector<uint8_t> &data) {
    if (this->decimate_ <= 1) {
      this->deliver_(data.data(), data.size());
      return;
    }
    // 48 kHz in: every sample into the filter, every third filtered one out.
    const auto *in = reinterpret_cast<const int16_t *>(data.data());
    size_t count = data.size() / 2;
    this->out_.resize((count / this->decimate_ + 1) * 2);
    auto *out = reinterpret_cast<int16_t *>(this->out_.data());
    size_t n = 0;
    size_t taps = this->taps_.size();
    for (size_t i = 0; i < count; i++) {
      this->history_[this->history_pos_] = in[i];
      this->history_pos_ = this->history_pos_ + 1 == taps ? 0 : this->history_pos_ + 1;
      if (++this->phase_ < this->decimate_)
        continue;
      this->phase_ = 0;
      float acc = 0.0f;
      size_t pos = this->history_pos_;  // the oldest sample
      for (size_t k = 0; k < taps; k++) {
        acc += this->taps_[k] * this->history_[pos];
        pos = pos + 1 == taps ? 0 : pos + 1;
      }
      out[n++] = static_cast<int16_t>(std::max(-32768.0f, std::min(32767.0f, acc)));
    }
    this->deliver_(this->out_.data(), n * 2);
  });
#endif

  httpd_config_t config = HTTPD_DEFAULT_CONFIG();
  config.server_port = this->port_;
  config.ctrl_port = this->port_;
  config.max_open_sockets = this->max_listeners_ + SPARE_SOCKETS;
  config.backlog_conn = 2;
  config.lru_purge_enable = false;  // a listener is never pushed off for a newcomer
  config.max_uri_handlers = 4;
  if (httpd_start(&this->httpd_, &config) != ESP_OK) {
    ESP_LOGE(TAG, "Could not start the HTTP server on port %u", this->port_);
    this->mark_failed();
    return;
  }

  httpd_uri_t audio = {
      .uri = "/audio.wav",
      .method = HTTP_GET,
      .handler = [](httpd_req_t *req) { return static_cast<VisionStateStream *>(req->user_ctx)->handle_audio_(req); },
      .user_ctx = this,
  };
  httpd_register_uri_handler(this->httpd_, &audio);
  httpd_uri_t snapshot = {
      .uri = "/snapshot.jpg",
      .method = HTTP_GET,
      .handler =
          [](httpd_req_t *req) { return static_cast<VisionStateStream *>(req->user_ctx)->handle_snapshot_(req); },
      .user_ctx = this,
  };
  httpd_register_uri_handler(this->httpd_, &snapshot);
  httpd_register_err_handler(this->httpd_, HTTPD_404_NOT_FOUND, not_found);
  httpd_register_err_handler(this->httpd_, HTTPD_405_METHOD_NOT_ALLOWED, not_found);
}

#ifdef USE_VISIONSTATE_MICROPHONE
/// 16 kHz sound to every listener (silence while paused or switched off), counted for the diagnostics.
void VisionStateStream::deliver_(const uint8_t *data, size_t len) {
  static const uint8_t SILENCE[256] = {};
  const auto *samples = reinterpret_cast<const int16_t *>(data);
  size_t count = len / 2;
  bool silent = this->paused_ || !this->stream_enabled_;
  LockGuard guard(this->lock_);
  if (!silent) {
    for (size_t i = 0; i < count; i++) {
      int32_t s = samples[i];
      int32_t a = s < 0 ? -s : s;
      this->sum_squares_ += static_cast<double>(s) * s;
      this->peak_ = std::max(this->peak_, a);
      if (a >= CLIP_LEVEL)
        this->clipped_++;
    }
    this->samples_ += count;
  }
  for (auto *listener : this->listeners_) {
    // A full buffer drops its oldest sound: counted, the listener is not keeping up (Wi-Fi).
    size_t room = listener->buffer->free();
    if (room < len)
      this->dropped_bytes_ += len - room;
    if (!silent) {
      listener->buffer->write(data, len);
      continue;
    }
    // As much silence instead, so the stream keeps its pace and the listener stays.
    for (size_t left = len; left > 0;) {
      size_t n = std::min(left, sizeof(SILENCE));
      listener->buffer->write(SILENCE, n);
      left -= n;
    }
  }
}

static float dbfs(double value) {
  // dBFS (0 is the loudest there is), in whole numbers so Home Assistant does not store every wobble.
  if (value <= 0)
    return -96.0f;
  return std::max(-96.0f, roundf(20.0f * log10f(static_cast<float>(value / 32768.0))));
}

void VisionStateStream::publish_diagnostics_() {
  double sum_squares;
  uint32_t samples, clipped, dropped;
  int32_t peak;
  {
    LockGuard guard(this->lock_);
    sum_squares = this->sum_squares_;
    samples = this->samples_;
    peak = this->peak_;
    clipped = this->clipped_;
    dropped = this->dropped_bytes_;
    this->sum_squares_ = 0;
    this->samples_ = 0;
    this->peak_ = 0;
    this->clipped_ = 0;
    this->dropped_bytes_ = 0;
  }
  if (this->level_sensor_ != nullptr)
    this->level_sensor_->publish_state(samples > 0 ? dbfs(sqrt(sum_squares / samples)) : NAN);
  if (this->peak_sensor_ != nullptr)
    this->peak_sensor_->publish_state(samples > 0 ? dbfs(peak) : NAN);
  if (this->clipped_sensor_ != nullptr)
    this->clipped_sensor_->publish_state(clipped);
  if (this->dropped_sensor_ != nullptr)
    this->dropped_sensor_->publish_state(static_cast<float>(dropped) * 1000.0f / BYTES_PER_SECOND);  // ms of sound
}
#endif

void VisionStateStream::loop() {
#ifdef USE_VISIONSTATE_CAMERA
  // The camera after start-up: a picture by now (an idle one comes every idle_framerate), or a
  // restart, once.
  if (this->camera_ != nullptr && !this->camera_checked_) {
    if (this->camera_gave_picture_) {
      this->camera_checked_ = true;
      if (restarted_for_camera == RESTARTED_MAGIC)
        ESP_LOGI(TAG, "The camera works after the restart");
      restarted_for_camera = 0;
    } else if (millis() > NO_PICTURE_RESTART_MS) {
      this->camera_checked_ = true;
      if (restarted_for_camera != RESTARTED_MAGIC) {
        ESP_LOGW(TAG, "The camera gave no picture in %" PRIu32 " s after start-up: restarting once",
                 NO_PICTURE_RESTART_MS / 1000);
        restarted_for_camera = RESTARTED_MAGIC;
        App.safe_reboot();
      } else {
        ESP_LOGE(TAG, "The camera gives no picture, also after a restart: check its cable and power");
        restarted_for_camera = 0;
      }
    } else {
      this->camera_->request_image(camera::IDLE);  // asked for, so that a working camera says so soon
    }
  }
#endif
#ifdef USE_VISIONSTATE_FLASH
  this->apply_flash_();
#endif
  uint8_t count = this->listener_count_;
#ifdef USE_VISIONSTATE_MICROPHONE
  // The microphone runs only while someone listens.
  if (count > 0 && !this->microphone_wanted_) {
    this->microphone_source_->start();
    this->microphone_wanted_ = true;
  } else if (count == 0 && this->microphone_wanted_) {
    this->microphone_source_->stop();
    this->microphone_wanted_ = false;
  }
  // Diagnostics while someone listens (nothing to measure otherwise), every diagnostics interval.
  if (count > 0 && millis() - this->last_diagnostics_ >= this->diagnostics_ms_) {
    this->last_diagnostics_ = millis();
    if (this->level_sensor_ != nullptr || this->peak_sensor_ != nullptr || this->clipped_sensor_ != nullptr ||
        this->dropped_sensor_ != nullptr)
      this->publish_diagnostics_();
  }
#endif
  if (this->listeners_sensor_ != nullptr && count != this->published_count_) {
    this->published_count_ = count;
    this->listeners_sensor_->publish_state(count);
  }
}

void VisionStateStream::dump_config() {
  ESP_LOGCONFIG(TAG,
                "VisionState stream:\n"
                "  Port: %u\n"
                "  Login: %s\n"
                "  Listeners at most: %u\n"
                "  Buffer: %" PRIu32 " ms",
                this->port_, this->expected_auth_.empty() ? "none" : "user name and password", this->max_listeners_,
                this->buffer_ms_);
#ifdef USE_VISIONSTATE_MICROPHONE
  ESP_LOGCONFIG(TAG, "  Sound: /audio.wav (16 kHz, mono, 16 bits)%s", this->decimate_ > 1 ? ", from 48 kHz" : "");
#endif
#ifdef USE_VISIONSTATE_CAMERA
  ESP_LOGCONFIG(TAG, "  Picture: /snapshot.jpg%s", this->fresh_snapshot_ ? " (fresh)" : "");
#endif
}

void VisionStateStream::on_shutdown() {
#ifdef USE_VISIONSTATE_CAMERA
  this->stopping_.store(true);
#endif
  if (this->httpd_ != nullptr) {
    httpd_stop(this->httpd_);
    this->httpd_ = nullptr;
  }
}

bool VisionStateStream::authorized_(httpd_req_t *req) {
  if (this->expected_auth_.empty())
    return true;
  size_t len = httpd_req_get_hdr_value_len(req, "Authorization");
  if (len == this->expected_auth_.size()) {
    std::string got(len + 1, '\0');
    if (httpd_req_get_hdr_value_str(req, "Authorization", got.data(), got.size()) == ESP_OK) {
      // Compare every byte, so how long the answer takes does not tell how much was right.
      uint8_t diff = 0;
      for (size_t i = 0; i < len; i++)
        diff |= static_cast<uint8_t>(got[i] ^ this->expected_auth_[i]);
      if (diff == 0)
        return true;
    }
  }
  httpd_resp_set_status(req, "401 Unauthorized");
  httpd_resp_set_hdr(req, "Connection", "close");
  httpd_resp_set_hdr(req, "WWW-Authenticate", "Basic realm=\"VisionState\"");
  httpd_resp_sendstr(req, "Unauthorized\n");
  return false;
}

esp_err_t VisionStateStream::handle_audio_(httpd_req_t *req) {
  if (!this->authorized_(req))
    return answered(req);
#ifndef USE_VISIONSTATE_MICROPHONE
  return not_found(req, HTTPD_404_NOT_FOUND);
#else
  if (this->listener_count_ >= this->max_listeners_) {
    httpd_resp_set_status(req, "409 Conflict");
    httpd_resp_set_hdr(req, "Connection", "close");
    httpd_resp_sendstr(req, "Too many listeners\n");
    return answered(req);
  }
  size_t bytes = std::max<size_t>(CHUNK_BYTES * 2, BYTES_PER_SECOND * this->buffer_ms_ / 1000);
  bytes -= bytes % 2;
  std::shared_ptr<ring_buffer::RingBuffer> buffer = ring_buffer::RingBuffer::create(bytes);
  if (buffer == nullptr) {
    httpd_resp_set_hdr(req, "Connection", "close");
    httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Out of memory");
    return answered(req);
  }
  auto *listener = new Listener();
  listener->buffer = buffer;
  listener->parent = this;
  if (httpd_req_async_handler_begin(req, &listener->req) != ESP_OK) {
    delete listener;
    httpd_resp_set_hdr(req, "Connection", "close");
    httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Could not start the stream");
    return answered(req);
  }
  {
    LockGuard guard(this->lock_);
    this->listeners_.push_back(listener);
    this->listener_count_ = this->listeners_.size();
  }
  App.wake_loop_threadsafe();  // loop() starts the microphone: now, not when the loop next wakes
  if (xTaskCreate(&VisionStateStream::listener_task_, "vs_listener", LISTENER_TASK_STACK, listener, 5, nullptr) !=
      pdPASS) {
    ESP_LOGE(TAG, "Could not start a task for the listener");
    httpd_req_t *async_req = listener->req;
    this->remove_listener_(listener);
    httpd_resp_send_err(async_req, HTTPD_500_INTERNAL_SERVER_ERROR, "Could not start the stream");
    httpd_req_async_handler_complete(async_req);
  }
  return ESP_OK;
#endif
}

void VisionStateStream::remove_listener_(Listener *listener) {
  LockGuard guard(this->lock_);
  auto it = std::find(this->listeners_.begin(), this->listeners_.end(), listener);
  if (it != this->listeners_.end())
    this->listeners_.erase(it);
  this->listener_count_ = this->listeners_.size();
  delete listener;
  App.wake_loop_threadsafe();  // loop() stops the microphone when the last one left
}

void VisionStateStream::listener_task_(void *arg) {
  auto *listener = static_cast<Listener *>(arg);
  VisionStateStream *self = listener->parent;
  httpd_req_t *req = listener->req;
  ESP_LOGD(TAG, "A listener connected");

  httpd_resp_set_type(req, "audio/wav");
  httpd_resp_set_hdr(req, "Cache-Control", "no-store");
  uint8_t header[44];
  wav_header(header);
  esp_err_t res = httpd_resp_send_chunk(req, reinterpret_cast<const char *>(header), sizeof(header));

  uint8_t chunk[CHUNK_BYTES];
  uint32_t last_sound = millis();
  while (res == ESP_OK) {
    size_t got = listener->buffer->read(chunk, sizeof(chunk), pdMS_TO_TICKS(READ_WAIT_MS));
    if (got == 0) {
      if (millis() - last_sound > SILENT_GIVE_UP_MS) {
        ESP_LOGW(TAG, "No sound from the microphone for %" PRIu32 " s: closing the stream", SILENT_GIVE_UP_MS / 1000);
        break;
      }
      continue;
    }
    got -= got % 2;  // whole samples only (the rest stays in the buffer)
    last_sound = millis();
    res = httpd_resp_send_chunk(req, reinterpret_cast<const char *>(chunk), got);
  }

  ESP_LOGD(TAG, "A listener left");
  self->remove_listener_(listener);
  httpd_resp_send_chunk(req, nullptr, 0);
  httpd_req_async_handler_complete(req);
  vTaskDelete(nullptr);
}

#ifdef USE_VISIONSTATE_FLASH
// In the main loop: the flash on while a snapshot wants it, and off when none does any more, only
// if it was switched on here (one already on, switched by someone else, is left alone).
void VisionStateStream::apply_flash_() {
  if (this->flash_ == nullptr)
    return;
  bool wanted = this->flash_holders_.load() > 0;
  int level = this->flash_level_.load();
  if (wanted && !this->flash_lit_.load()) {
    if (!this->flash_->remote_values.is_on()) {
      this->flash_->turn_on().set_brightness(level / 100.0f).set_transition_length(0).perform();
      this->flash_ours_ = true;
      this->flash_level_lit_ = level;
    }
    this->flash_lit_.store(true);
  } else if (wanted && this->flash_ours_ && level != this->flash_level_lit_) {
    // Another snapshot wants it stronger or weaker while it is on: the one asked last.
    this->flash_->turn_on().set_brightness(level / 100.0f).set_transition_length(0).perform();
    this->flash_level_lit_ = level;
  } else if (!wanted && this->flash_lit_.load()) {
    if (this->flash_ours_)
      this->flash_->turn_off().set_transition_length(0).perform();
    this->flash_ours_ = false;
    this->flash_lit_.store(false);
  }
}
#endif

#ifdef USE_VISIONSTATE_CAMERA
// A broken picture is taken again this often at most (then sent as it is: VisionState knows it too).
static const uint8_t BROKEN_RETRIES = 2;

/// Where a JPEG's picture ends: just after the end marker that closes its scan data (an EXIF
/// thumbnail's, before the scan, does not count); 0 when there is none. As imaging.jpeg_end in the app.
static size_t jpeg_end(const uint8_t *data, size_t size) {
  size_t i = 2;
  while (i + 1 < size) {
    if (data[i] != 0xFF)
      return 0;
    uint8_t marker = data[i + 1];
    if (marker == 0xFF) {  // fill bytes before a marker
      i += 1;
      continue;
    }
    if (marker == 0xD9)
      return i + 2;
    if (marker == 0x01 || (marker >= 0xD0 && marker <= 0xD7)) {
      i += 2;
      continue;
    }
    if (i + 3 >= size)
      return 0;
    i += 2 + ((static_cast<size_t>(data[i + 2]) << 8) | data[i + 3]);
    if (marker != 0xDA)
      continue;
    // Scan data up to the next marker: an 0xFF in it is followed by 0x00, a restart marker or more 0xFF.
    while (true) {
      const auto *found = static_cast<const uint8_t *>(i < size ? memchr(data + i, 0xFF, size - i) : nullptr);
      if (found == nullptr)
        return 0;
      i = found - data;
      if (i + 1 >= size)
        return 0;
      uint8_t following = data[i + 1];
      if (following == 0x00 || (following >= 0xD0 && following <= 0xD7)) {
        i += 2;
      } else if (following == 0xFF) {
        i += 1;
      } else {
        break;
      }
    }
  }
  return 0;
}

static bool contains(const uint8_t *data, size_t size, uint8_t first, uint8_t second) {
  for (size_t i = 0; i + 1 < size; i++) {
    if (data[i] == first && data[i + 1] == second)
      return true;
  }
  return false;
}

/// A JPEG whose picture ends early and is followed by the rest of an older one: the camera stopped
/// sending part way, and the driver handed out the buffer up to the end of what was in it before.
/// As imaging.jpeg_broken in the app (a thumbnail, several pictures or padding are not broken).
static bool jpeg_broken(const uint8_t *data, size_t size) {
  if (size < 4 || data[0] != 0xFF || data[1] != 0xD8)
    return false;
  size_t end = jpeg_end(data, size);
  if (end == 0)
    return false;
  return contains(data + end, size - end, 0xFF, 0xD9) && !contains(data + end, size - end, 0xFF, 0xD8);
}

/// What a snapshot asks for: ?size=1600x1200&quality=8&clock=20&exposure=auto|<ms>&gain=4 (each
/// may be left out). Values it does not know are left out, with a warning.
static CameraSettings asked_settings(const char *query) {
  CameraSettings asked;
  char value[16];
  if (httpd_query_key_value(query, "size", value, sizeof(value)) == ESP_OK) {
    for (const auto &size : PICTURE_SIZES) {
      if (strcmp(value, size.name) == 0)
        asked.size = size.size;
    }
    if (asked.size < 0)
      ESP_LOGW(TAG, "Unknown picture size: %s", value);
  }
  if (httpd_query_key_value(query, "quality", value, sizeof(value)) == ESP_OK) {
    int quality = atoi(value);
    if (quality >= 6 && quality <= 63) {
      asked.quality = quality;
    } else {
      ESP_LOGW(TAG, "JPEG quality out of 6-63: %s", value);
    }
  }
  if (httpd_query_key_value(query, "clock", value, sizeof(value)) == ESP_OK) {
    int mhz = atoi(value);
    if (mhz == 20 || mhz == 10) {
      asked.clock_mhz = mhz;
    } else {
      ESP_LOGW(TAG, "Unknown camera clock: %s MHz", value);
    }
  }
  if (httpd_query_key_value(query, "exposure", value, sizeof(value)) == ESP_OK) {
    int ms = strcmp(value, "auto") == 0 ? 0 : atoi(value);
    if (ms >= 0 && ms <= 1000) {
      asked.exposure_ms = ms;
    } else {
      ESP_LOGW(TAG, "Unknown exposure: %s", value);
    }
  }
  if (httpd_query_key_value(query, "gain", value, sizeof(value)) == ESP_OK) {
    int gain = atoi(value);
    for (int i = 0; i <= GAINCEILING_128X; i++) {
      if (gain == (2 << i))
        asked.gain = i;
    }
    if (asked.gain < 0)
      ESP_LOGW(TAG, "Unknown gain ceiling: %s", value);
  }
  if (httpd_query_key_value(query, "ae", value, sizeof(value)) == ESP_OK) {
    char *end = nullptr;
    long level = strtol(value, &end, 10);
    if (end != value && *end == '\0' && level >= -2 && level <= 2) {
      asked.ae_level = static_cast<int>(level);
    } else {
      ESP_LOGW(TAG, "Exposure level out of -2 to 2: %s", value);
    }
  }
  return asked;
}

/// How the camera is set up, written as a snapshot asks for it (X-VisionState-Camera-Settings).
static std::string settings_text(const CameraSettings &now) {
  const char *size = "unknown";
  for (const auto &s : PICTURE_SIZES) {
    if (s.size == now.size)
      size = s.name;
  }
  char exposure[12] = "unknown";
  if (now.exposure_ms > 0) {
    snprintf(exposure, sizeof(exposure), "%d", now.exposure_ms);
  } else if (now.exposure_ms == 0) {
    snprintf(exposure, sizeof(exposure), "auto");
  }
  char ae[8] = "unknown";
  if (now.ae_level != AE_LEVEL_UNKNOWN)
    snprintf(ae, sizeof(ae), "%d", now.ae_level);
  char text[128];
  snprintf(text, sizeof(text), "size=%s&quality=%d&clock=%d&exposure=%s&gain=%d&ae=%s", size, now.quality,
           now.clock_mhz, exposure, now.gain >= 0 ? 2 << now.gain : -1, ae);
  return text;
}

void VisionStateStream::on_camera_image(const std::shared_ptr<camera::CameraImage> &image) {
  this->camera_gave_picture_ = true;
  if (image->was_requested_by(camera::WEB_REQUESTER))
    ESP_LOGD(TAG, "A picture for the snapshot: %u bytes%s", (unsigned) image->get_data_length(),
             this->waiting_for_image_ ? "" : " (no longer waited for)");
  if (this->waiting_for_image_ && image->was_requested_by(camera::WEB_REQUESTER)) {
    this->image_ = image;
    this->waiting_for_image_ = false;
    xSemaphoreGive(this->image_ready_);
  }
}

// The gain ceiling (?gain=; gainceiling_t: 0 is 2x, 6 is 128x). The driver's set_gainceiling writes
// that number as it is into the OV3660's and OV5640's ceiling register (0x3A18-19), which holds the
// gain itself in 1/16 (they start at 0xF8, 15.5x): a ceiling of 0 to 6/16, under 1x, so the automatic
// gain never brightened a picture (an OV3660, 2026-11-17: as dark at 2x as at 128x). Those two get
// the gain in 1/16; others (the OV2640 has a scale of its own) go through the driver.
static void set_gain_ceiling(sensor_t *s, int level) {
  if (s->id.PID == OV3660_PID || s->id.PID == OV5640_PID) {
    int sixteenths = std::min((2 << level) * 16, 0x3FF);  // 10 bits: 128x becomes about 64x
    s->set_reg(s, 0x3A18, 0x03, sixteenths >> 8);
    s->set_reg(s, 0x3A19, 0xFF, sixteenths & 0xFF);
  } else {
    s->set_gainceiling(s, static_cast<gainceiling_t>(level));
  }
}

uint8_t VisionStateStream::apply_settings_(const CameraSettings &asked) {
  sensor_t *s = esp_camera_sensor_get();
  if (s == nullptr)
    return 0;  // the camera has not started yet
  if (this->largest_ < 0)
    this->largest_ = s->status.framesize;  // as it started: its buffers were made for this size
  CameraSettings &now = this->settings_;
  uint8_t settle = 0;
  bool timing = false;  // the clock changed, and with it the length of a line: a fixed exposure is set again
  if (asked.clock_mhz > 0 && asked.clock_mhz != now.clock_mhz) {
    // The clock runs on the camera's own LEDC timer (0); the flash has a timer of its own.
    s->set_xclk(s, LEDC_TIMER_0, asked.clock_mhz);
    now.clock_mhz = asked.clock_mhz;
    settle = std::max(settle, SETTLE_PICTURES_CLOCK);
    timing = true;
  }
  if (asked.size >= 0 && asked.size != now.size) {
    int size = asked.size;
    if (size > this->largest_) {
      ESP_LOGW(TAG, "The camera's buffers were made for a smaller picture: the largest it can give is taken");
      size = this->largest_;
    }
    if (size != now.size) {
      s->set_framesize(s, static_cast<framesize_t>(size));
      now.size = size;
      settle = std::max(settle, SETTLE_PICTURES_RESIZED);
    }
  }
  if (asked.quality > 0 && asked.quality != now.quality) {
    s->set_quality(s, asked.quality);
    now.quality = asked.quality;
    settle = std::max(settle, SETTLE_PICTURES);
  }
  if (asked.gain >= 0 && asked.gain != now.gain) {
    set_gain_ceiling(s, asked.gain);
    now.gain = asked.gain;
    settle = std::max(settle, SETTLE_PICTURES);
  }
  if (asked.exposure_ms >= 0 && (asked.exposure_ms != now.exposure_ms || (timing && asked.exposure_ms > 0))) {
    if (asked.exposure_ms == 0) {
      s->set_exposure_ctrl(s, 1);  // its level is the sensor's own (?ae=), or as the device's file set it
    } else {
      // Fixed, so something moving stays sharp; the automatic gain keeps it bright.
      float line_us = LINE_US_20MHZ * 20.0f / static_cast<float>(now.clock_mhz > 0 ? now.clock_mhz : 20);
      int lines = static_cast<int>(lroundf(static_cast<float>(asked.exposure_ms) * 1000.0f / line_us));
      s->set_exposure_ctrl(s, 0);
      s->set_aec_value(s, std::max(1, std::min(lines, MAX_EXPOSURE_LINES)));
    }
    now.exposure_ms = asked.exposure_ms;
    settle = std::max(settle, SETTLE_PICTURES);
  }
  // How bright the automatic exposure aims (?ae=, -2 to 2): the target it settles in. Not asked for
  // (an older VisionState), it stays as the device's file set it (esp32_camera ae_level, 0 when not).
  if (asked.ae_level != AE_LEVEL_UNKNOWN && asked.ae_level != now.ae_level) {
    s->set_ae_level(s, asked.ae_level);
    now.ae_level = asked.ae_level;
    settle = std::max(settle, SETTLE_PICTURES);
  }
  if (settle > 0) {
    s->set_gain_ctrl(s, 1);
    ESP_LOGI(TAG, "Camera set up: %s", settings_text(now).c_str());
  }
  return settle;
}

void VisionStateStream::settle_(uint8_t pictures) {
  App.wake_loop_threadsafe();
  for (uint8_t n = 0; n < this->kept_pictures_; n++)
    this->take_image_(IMAGE_TIMEOUT_MS);
  uint32_t first = 0;
  for (uint8_t n = 0; n < pictures; n++) {
    this->take_image_(IMAGE_TIMEOUT_MS);
    if (n == 0)
      first = millis();
  }
  if (pictures >= 2)
    this->frame_ms_ = (millis() - first) / (pictures - 1);
}

void VisionStateStream::set_orientation(const std::string &orientation) {
  // "Normal" is ESPHome's default for the camera: vertical_flip and horizontal_mirror on.
  bool mirror, flip;
  if (orientation == "Normal") {
    mirror = true, flip = true;
  } else if (orientation == "Mirrored") {
    mirror = false, flip = true;
  } else if (orientation == "Upside down") {  // turned 180 degrees
    mirror = false, flip = false;
  } else if (orientation == "Upside down, mirrored") {
    mirror = true, flip = false;
  } else {
    ESP_LOGW(TAG, "Unknown orientation: %s", orientation.c_str());
    return;
  }
  sensor_t *s = esp_camera_sensor_get();
  if (s == nullptr)
    return;  // the camera has not started yet: set again at boot
  s->set_hmirror(s, mirror);
  s->set_vflip(s, flip);
  ESP_LOGI(TAG, "Orientation: %s", orientation.c_str());
}

#if defined(USE_VISIONSTATE_CAMERA_POWER_DOWN) && defined(USE_OTA_STATE_LISTENER)
void VisionStateStream::on_ota_global_state(ota::OTAState state, float progress, uint8_t error,
                                            ota::OTAComponent *comp) {
  // Powered down while firmware is written, so it is not taking pictures meanwhile, and starts
  // afresh after the restart (the camera driver powers it up at start-up). Up again if it failed.
  if (state == ota::OTA_STARTED) {
    ESP_LOGI(TAG, "Firmware is installed: the camera is powered down until the restart");
    this->stopping_.store(true);
    gpio_set_level(static_cast<gpio_num_t>(this->camera_power_down_pin_), 1);
  } else if (state == ota::OTA_ERROR || state == ota::OTA_ABORT) {
    gpio_set_level(static_cast<gpio_num_t>(this->camera_power_down_pin_), 0);
    this->stopping_.store(false);
  }
}
#endif

std::shared_ptr<camera::CameraImage> VisionStateStream::take_image_(uint32_t timeout_ms) {
  xSemaphoreTake(this->image_ready_, 0);  // a give left over from an earlier picture
  this->image_ = nullptr;
  this->waiting_for_image_ = true;
  uint32_t asked = millis();
  // Asked for again every WAKE_EVERY_MS until it comes, and the main loop woken each time:
  // - the camera clears its requests after handing a picture out (single_requesters_ = 0), and this
  //   task runs on the other core: a request made right after the last picture (the fresh one of a
  //   snapshot) was wiped out before the camera saw it;
  // - the camera hands pictures out in the main loop, which sleeps while nothing happens.
  // Both only showed with nobody listening: a listener's sound keeps the cores and the loop busy.
  bool got = false;
  while (!got && millis() - asked < timeout_ms && !this->stopping_.load()) {
    this->camera_->request_image(camera::WEB_REQUESTER);
    App.wake_loop_threadsafe();
    got = xSemaphoreTake(this->image_ready_, pdMS_TO_TICKS(WAKE_EVERY_MS)) == pdTRUE;
  }
  if (!got) {
    this->waiting_for_image_ = false;
    if (this->stopping_.load())
      return nullptr;
    ESP_LOGW(TAG, "The camera gave no picture in %" PRIu32 " ms", timeout_ms);
    return nullptr;
  }
  ESP_LOGD(TAG, "Picture after %" PRIu32 " ms", millis() - asked);
  std::shared_ptr<camera::CameraImage> image;
  image.swap(this->image_);
  return image;
}
#endif

esp_err_t VisionStateStream::handle_snapshot_(httpd_req_t *req) {
  if (!this->authorized_(req))
    return answered(req);
#ifndef USE_VISIONSTATE_CAMERA
  return not_found(req, HTTPD_404_NOT_FOUND);
#else
  if (this->camera_ == nullptr)
    return not_found(req, HTTPD_404_NOT_FOUND);
  if (this->stopping_.load()) {
    httpd_resp_set_status(req, "503 Service Unavailable");
    httpd_resp_set_hdr(req, "Retry-After", "60");
    return httpd_resp_sendstr(req, "Firmware is being installed");
  }
  // The sensor's settings (?size=&quality=&clock=&exposure=&gain=): the camera set up so first (it
  // stays so until a snapshot asks otherwise), and the pictures taken before it settled thrown away.
  char query[160];
  char value[16];
  bool has_query = httpd_req_get_url_query_str(req, query, sizeof(query)) == ESP_OK;
  if (has_query) {
    uint8_t settle = this->apply_settings_(asked_settings(query));
    if (settle > 0)
      this->settle_(settle);
  }
  // Says it is our firmware and takes a sensor's settings, and how the camera is set up now.
  httpd_resp_set_hdr(req, "X-VisionState-Camera", "settings");
  std::string now = settings_text(this->settings_);
  httpd_resp_set_hdr(req, "X-VisionState-Camera-Settings", now.c_str());
  // Which camera chip it is (OV2640, OV3660 …): cameras of one board are sold with different ones.
  sensor_t *chip = esp_camera_sensor_get();
  camera_sensor_info_t *info = chip != nullptr ? esp_camera_sensor_get_info(&chip->id) : nullptr;
  if (info != nullptr && info->name != nullptr)
    httpd_resp_set_hdr(req, "X-VisionState-Camera-Chip", info->name);
  // The camera keeps a picture ready, taken right after the last one was fetched (up to its idle
  // interval ago, and before a light was switched on): with fresh_snapshot, that one is thrown away.
  bool kept_taken = false;
#ifdef USE_VISIONSTATE_FLASH
  // ?flash=<ms>: the picture taken with the flash, that long after it came on (the pictures taken
  // meanwhile thrown away); the flash goes off again after it. &flash_level=<1-100>: how strong, in
  // percent of full (FLASH_LEVEL_DEFAULT when not said). With &dark=<0-255>, only when the
  // picture the camera keeps ready is darker than that (its mean brightness): the camera looks all
  // the time, so it knows whether it is dark without a picture taken for it. Said to VisionState,
  // which asks so (X-VisionState-Flash), and whether the flash was used (X-VisionState-Flashed).
  bool flashed = false;
  if (this->flash_ != nullptr) {
    httpd_resp_set_hdr(req, "X-VisionState-Flash", "1");
    char dark[8];
    if (has_query && httpd_query_key_value(query, "flash", value, sizeof(value)) == ESP_OK) {
      uint32_t settle = std::min<uint32_t>(strtoul(value, nullptr, 10), FLASH_SETTLE_MAX_MS);
      bool wanted = true;
      if (httpd_query_key_value(query, "dark", dark, sizeof(dark)) == ESP_OK) {
        if (this->kept_lit_.exchange(false))
          this->take_image_(IMAGE_TIMEOUT_MS);  // taken in the last snapshot's flash: the next one says
        std::shared_ptr<camera::CameraImage> kept = this->take_image_(IMAGE_TIMEOUT_MS);
        kept_taken = true;
        int brightness = kept != nullptr ? picture_brightness(kept->get_data_buffer(), kept->get_data_length()) : -1;
        wanted = brightness < 0 || brightness < static_cast<int>(strtoul(dark, nullptr, 10));
        ESP_LOGD(TAG, "The picture kept ready: brightness %d, flash %s", brightness, wanted ? "on" : "not needed");
      }
      httpd_resp_set_hdr(req, "X-VisionState-Flashed", wanted ? "1" : "0");
      if (wanted) {
        int level = FLASH_LEVEL_DEFAULT;
        if (httpd_query_key_value(query, "flash_level", value, sizeof(value)) == ESP_OK)
          level = std::clamp<int>(atoi(value), 1, 100);
        this->flash_level_.store(level);
        this->flash_holders_++;
        flashed = true;
        uint32_t asked = millis();
        while (!this->flash_lit_.load() && millis() - asked < FLASH_ON_TIMEOUT_MS && !this->stopping_.load()) {
          App.wake_loop_threadsafe();
          delay(WAKE_EVERY_MS);
        }
        uint32_t lit = millis();
        uint32_t taken = 0;
        uint32_t first = 0;
        do {
          this->take_image_(IMAGE_TIMEOUT_MS);  // thrown away: taken before the camera caught up
          if (taken++ == 0)
            first = millis();
        } while (millis() - lit < settle && !this->stopping_.load());
        if (taken >= 2)
          this->frame_ms_ = (millis() - first) / (taken - 1);
        kept_taken = false;  // the one after these is thrown away as well (taken as the light came)
      }
    }
  }
#endif
  std::shared_ptr<camera::CameraImage> image = this->take_image_(IMAGE_TIMEOUT_MS);
  if (image != nullptr && this->fresh_snapshot_ && !kept_taken) {
    image = nullptr;
    image = this->take_image_(IMAGE_TIMEOUT_MS);
  }
  // A broken picture (it ends early, the rest an older one's) is taken again, so it is never sent.
  for (uint8_t again = 0; again < BROKEN_RETRIES && image != nullptr; again++) {
    if (!jpeg_broken(image->get_data_buffer(), image->get_data_length()))
      break;
    ESP_LOGW(TAG, "The camera gave a broken picture (it ends early): taken again");
    image = nullptr;
    image = this->take_image_(IMAGE_TIMEOUT_MS);
  }
#ifdef USE_VISIONSTATE_FLASH
  if (flashed) {
    this->flash_holders_--;
    this->kept_lit_.store(true);
    App.wake_loop_threadsafe();
  }
#endif
  if (image == nullptr) {
    ESP_LOGW(TAG, "No picture from the camera");
    httpd_resp_set_hdr(req, "Connection", "close");
    httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "No picture from the camera");
    return answered(req);
  }
  // The time between two pictures when it was last measured (settling): how fast the camera is.
  char frame[12];
  if (this->frame_ms_ > 0) {
    snprintf(frame, sizeof(frame), "%" PRIu32, this->frame_ms_);
    httpd_resp_set_hdr(req, "X-VisionState-Frame-Ms", frame);
  }
  httpd_resp_set_type(req, "image/jpeg");
  httpd_resp_set_hdr(req, "Cache-Control", "no-store");
  httpd_resp_set_hdr(req, "Connection", "close");
  httpd_resp_send(req, reinterpret_cast<const char *>(image->get_data_buffer()), image->get_data_length());
  return answered(req);
#endif
}

}  // namespace esphome::visionstate_stream

#endif  // USE_ESP32
