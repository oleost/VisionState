#include "visionstate_stream.h"

#ifdef USE_ESP32

#include <esp_http_server.h>

#include <algorithm>
#include <cstring>

#include "esphome/core/alloc_helpers.h"
#include "esphome/core/application.h"
#include "esphome/core/hal.h"
#include "esphome/core/log.h"

namespace esphome::visionstate_stream {

static const char *const TAG = "visionstate_stream";

static const uint32_t SAMPLE_RATE = 16000;
static const size_t BYTES_PER_SECOND = SAMPLE_RATE * 2;     // mono, 16 bits
static const size_t CHUNK_BYTES = 1024;                     // 32 ms
static const uint32_t READ_WAIT_MS = 250;                   // a read that waits longer gives up for now
static const uint32_t SILENT_GIVE_UP_MS = 10000;            // no sound from the microphone this long: close
static const uint32_t IMAGE_TIMEOUT_MS = 5000;
static const uint32_t LISTENER_TASK_STACK = 4096;

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
  this->microphone_source_->add_data_callback([this](const std::vector<uint8_t> &data) {
    LockGuard guard(this->lock_);
    for (auto *listener : this->listeners_)
      listener->buffer->write(data.data(), data.size());  // a full buffer drops its oldest sound
  });
#endif

  httpd_config_t config = HTTPD_DEFAULT_CONFIG();
  config.server_port = this->port_;
  config.ctrl_port = this->port_;
  config.max_open_sockets = this->max_listeners_ + 1;
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
}

void VisionStateStream::loop() {
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
  ESP_LOGCONFIG(TAG, "  Sound: /audio.wav (16 kHz, mono, 16 bits)");
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
  httpd_resp_set_hdr(req, "WWW-Authenticate", "Basic realm=\"VisionState\"");
  httpd_resp_sendstr(req, "Unauthorized\n");
  return false;
}

esp_err_t VisionStateStream::handle_audio_(httpd_req_t *req) {
  if (!this->authorized_(req))
    return ESP_OK;
#ifndef USE_VISIONSTATE_MICROPHONE
  httpd_resp_send_404(req);
  return ESP_OK;
#else
  if (this->listener_count_ >= this->max_listeners_) {
    httpd_resp_set_status(req, "409 Conflict");
    httpd_resp_sendstr(req, "Too many listeners\n");
    return ESP_OK;
  }
  size_t bytes = std::max<size_t>(CHUNK_BYTES * 2, BYTES_PER_SECOND * this->buffer_ms_ / 1000);
  bytes -= bytes % 2;
  std::shared_ptr<ring_buffer::RingBuffer> buffer = ring_buffer::RingBuffer::create(bytes);
  if (buffer == nullptr) {
    httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Out of memory");
    return ESP_OK;
  }
  auto *listener = new Listener();
  listener->buffer = buffer;
  listener->parent = this;
  if (httpd_req_async_handler_begin(req, &listener->req) != ESP_OK) {
    delete listener;
    httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Could not start the stream");
    return ESP_OK;
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

std::shared_ptr<camera::CameraImage> VisionStateStream::take_image_(uint32_t timeout_ms) {
  xSemaphoreTake(this->image_ready_, 0);  // a give left over from an earlier picture
  this->image_ = nullptr;
  this->waiting_for_image_ = true;
  uint32_t asked = millis();
  this->camera_->request_image(camera::WEB_REQUESTER);
  // The camera hands pictures out in the main loop, which sleeps while nothing happens (it is woken
  // only for a picture taken while one is asked for: with two buffers one is ready already, and
  // nobody wakes it). Without this, the second picture of a fresh snapshot waited until something
  // else woke the loop — always, while a listener's sound kept it busy; never, without one.
  App.wake_loop_threadsafe();
  if (xSemaphoreTake(this->image_ready_, pdMS_TO_TICKS(timeout_ms)) != pdTRUE) {
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
    return ESP_OK;
#ifndef USE_VISIONSTATE_CAMERA
  httpd_resp_send_404(req);
  return ESP_OK;
#else
  if (this->camera_ == nullptr) {
    httpd_resp_send_404(req);
    return ESP_OK;
  }
  // The camera keeps a picture ready, taken right after the last one was fetched (up to its idle
  // interval ago, and before a light was switched on): with fresh_snapshot, that one is thrown away.
  std::shared_ptr<camera::CameraImage> image = this->take_image_(IMAGE_TIMEOUT_MS);
  if (image != nullptr && this->fresh_snapshot_) {
    image = nullptr;
    image = this->take_image_(IMAGE_TIMEOUT_MS);
  }
  if (image == nullptr) {
    ESP_LOGW(TAG, "No picture from the camera");
    httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "No picture from the camera");
    return ESP_OK;
  }
  httpd_resp_set_type(req, "image/jpeg");
  httpd_resp_set_hdr(req, "Cache-Control", "no-store");
  return httpd_resp_send(req, reinterpret_cast<const char *>(image->get_data_buffer()), image->get_data_length());
#endif
}

}  // namespace esphome::visionstate_stream

#endif  // USE_ESP32
