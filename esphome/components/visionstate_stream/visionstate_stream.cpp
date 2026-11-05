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
// The main loop is woken, and the picture asked for again, this often while it is waited for: the
// first request after a picture was handed out is lost, so this is what a fresh snapshot waits
// beyond the camera itself (50 ms made it 100: measured on an ESP32-CAM, 2026-11-05).
static const uint32_t WAKE_EVERY_MS = 10;
static const uint32_t LISTENER_TASK_STACK = 4096;
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
  this->microphone_source_->add_data_callback([this](const std::vector<uint8_t> &data) {
    static const uint8_t SILENCE[256] = {};
    LockGuard guard(this->lock_);
    for (auto *listener : this->listeners_) {
      if (!this->paused_) {
        listener->buffer->write(data.data(), data.size());  // a full buffer drops its oldest sound
        continue;
      }
      // Paused: as much silence instead, so the stream keeps its pace and the listener stays.
      for (size_t left = data.size(); left > 0;) {
        size_t n = std::min(left, sizeof(SILENCE));
        listener->buffer->write(SILENCE, n);
        left -= n;
      }
    }
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
