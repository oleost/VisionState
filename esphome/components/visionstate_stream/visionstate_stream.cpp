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
#include <esp_camera.h>
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
// Camera use (packages/camera-use.yaml): the names Home Assistant shows, and the keys a snapshot
// asks with (?use=...). VisionState asks for the one its sensor needs; it stays until changed.
static const uint8_t CAMERA_USES = 3;
static const char *const CAMERA_USE_NAMES[CAMERA_USES] = {"States", "Meters and displays", "People, cars and animals"};
static const char *const CAMERA_USE_KEYS[CAMERA_USES] = {"states", "meter", "movement"};
static const uint8_t USE_METER = 1;
static const uint8_t USE_MOVEMENT = 2;
// Pictures thrown away after a change, before the snapshot: the exposure is right in the next one,
// a new picture size needs a few (measured on an ESP32-CAM, 2026-11-05).
static const uint8_t SETTLE_PICTURES = 1;
static const uint8_t SETTLE_PICTURES_RESIZED = 3;
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
  std::string pair = username + ":" + password;
  this->expected_auth_ = "Basic " + base64_encode(reinterpret_cast<const uint8_t *>(pair.data()), pair.size());
}

void VisionStateStream::setup() {
#ifdef USE_VISIONSTATE_CAMERA
  this->image_ready_ = xSemaphoreCreateBinary();
  if (this->camera_ != nullptr)
    this->camera_->add_listener(this);
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
  if (this->camera_use_changed_ && this->camera_use_ < CAMERA_USES) {
    this->camera_use_changed_ = false;
    this->camera_use_trigger_.trigger(std::string(CAMERA_USE_NAMES[this->camera_use_]));
  }
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

#ifdef USE_VISIONSTATE_CAMERA
void VisionStateStream::on_camera_image(const std::shared_ptr<camera::CameraImage> &image) {
  if (image->was_requested_by(camera::WEB_REQUESTER))
    ESP_LOGD(TAG, "A picture for the snapshot: %u bytes%s", (unsigned) image->get_data_length(),
             this->waiting_for_image_ ? "" : " (no longer waited for)");
  if (this->waiting_for_image_ && image->was_requested_by(camera::WEB_REQUESTER)) {
    this->image_ = image;
    this->waiting_for_image_ = false;
    xSemaphoreGive(this->image_ready_);
  }
}

bool VisionStateStream::apply_camera_use_(uint8_t use) {
  sensor_t *s = esp_camera_sensor_get();
  if (s == nullptr || use >= CAMERA_USES)
    return false;  // the camera has not started yet: set again at boot
  bool resized = this->camera_use_ >= CAMERA_USES || (use == USE_METER) != (this->camera_use_ == USE_METER);
  s->set_framesize(s, use == USE_METER ? FRAMESIZE_UXGA : FRAMESIZE_SVGA);
  s->set_quality(s, use == USE_METER ? 8 : 10);
  if (use == USE_MOVEMENT) {
    // Short and fixed, so something moving stays sharp; the gain keeps it bright.
    s->set_exposure_ctrl(s, 0);
    s->set_aec_value(s, this->motion_exposure_);
    s->set_gainceiling(s, GAINCEILING_16X);
  } else {
    s->set_exposure_ctrl(s, 1);
    s->set_ae_level(s, 0);
    s->set_gainceiling(s, GAINCEILING_4X);
  }
  s->set_gain_ctrl(s, 1);
  this->camera_use_ = use;
  this->camera_use_changed_ = true;
  ESP_LOGI(TAG, "Camera use: %s", CAMERA_USE_NAMES[use]);
  return resized;
}

void VisionStateStream::set_camera_use(const std::string &use) {
  for (uint8_t i = 0; i < CAMERA_USES; i++) {
    if (use == CAMERA_USE_NAMES[i]) {
      if (i != this->camera_use_)
        this->apply_camera_use_(i);
      return;
    }
  }
  ESP_LOGW(TAG, "Unknown camera use: %s", use.c_str());
}

void VisionStateStream::set_motion_exposure(int lines) {
  this->motion_exposure_ = lines;
  sensor_t *s = esp_camera_sensor_get();
  if (s != nullptr && this->camera_use_ == USE_MOVEMENT)
    s->set_aec_value(s, lines);
}

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
  while (!got && millis() - asked < timeout_ms) {
    this->camera_->request_image(camera::WEB_REQUESTER);
    App.wake_loop_threadsafe();
    got = xSemaphoreTake(this->image_ready_, pdMS_TO_TICKS(WAKE_EVERY_MS)) == pdTRUE;
  }
  if (!got) {
    this->waiting_for_image_ = false;
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
  // ?use=states|meter|movement: the camera set up for that first (it stays so), and the pictures
  // taken before it settled thrown away.
  char query[48];
  char value[16];
  if (httpd_req_get_url_query_str(req, query, sizeof(query)) == ESP_OK &&
      httpd_query_key_value(query, "use", value, sizeof(value)) == ESP_OK) {
    for (uint8_t i = 0; i < CAMERA_USES; i++) {
      if (strcmp(value, CAMERA_USE_KEYS[i]) == 0 && i != this->camera_use_) {
        uint8_t settle = this->apply_camera_use_(i) ? SETTLE_PICTURES_RESIZED : SETTLE_PICTURES;
        App.wake_loop_threadsafe();
        for (uint8_t n = 0; n < settle; n++)
          this->take_image_(IMAGE_TIMEOUT_MS);
      }
    }
  }
  // Says it is our firmware, and how the camera is set up (VisionState shows Camera use for it).
  httpd_resp_set_hdr(req, "X-VisionState-Camera",
                     this->camera_use_ < CAMERA_USES ? CAMERA_USE_KEYS[this->camera_use_] : "none");
  // The camera keeps a picture ready, taken right after the last one was fetched (up to its idle
  // interval ago, and before a light was switched on): with fresh_snapshot, that one is thrown away.
  std::shared_ptr<camera::CameraImage> image = this->take_image_(IMAGE_TIMEOUT_MS);
  if (image != nullptr && this->fresh_snapshot_) {
    image = nullptr;
    image = this->take_image_(IMAGE_TIMEOUT_MS);
  }
  if (image == nullptr) {
    ESP_LOGW(TAG, "No picture from the camera");
    httpd_resp_set_hdr(req, "Connection", "close");
    httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "No picture from the camera");
    return answered(req);
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
