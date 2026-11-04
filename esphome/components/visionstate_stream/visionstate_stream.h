#pragma once

#ifdef USE_ESP32

#include <freertos/FreeRTOS.h>
#include <freertos/semphr.h>

#include <memory>
#include <string>
#include <vector>

#include "esphome/components/ring_buffer/ring_buffer.h"
#include "esphome/components/sensor/sensor.h"
#include "esphome/core/component.h"
#include "esphome/core/helpers.h"

#ifdef USE_VISIONSTATE_MICROPHONE
#include "esphome/components/microphone/microphone_source.h"
#endif
#ifdef USE_VISIONSTATE_CAMERA
#include "esphome/components/camera/camera.h"
#endif

struct httpd_req;  // NOLINT(readability-identifier-naming)

namespace esphome::visionstate_stream {

/// One client of /audio.wav: the sound waits in its own ring buffer, and a task of its own sends it.
struct Listener {
  std::shared_ptr<ring_buffer::RingBuffer> buffer;
  struct httpd_req *req{nullptr};
  class VisionStateStream *parent{nullptr};
};

class VisionStateStream final : public Component
#ifdef USE_VISIONSTATE_CAMERA
    ,
                                public camera::CameraListener
#endif
{
 public:
  void setup() override;
  void loop() override;
  void dump_config() override;
  void on_shutdown() override;
  float get_setup_priority() const override { return setup_priority::AFTER_WIFI; }

  void set_port(uint16_t port) { this->port_ = port; }
  void set_max_listeners(uint8_t max_listeners) { this->max_listeners_ = max_listeners; }
  void set_buffer_duration(uint32_t ms) { this->buffer_ms_ = ms; }
  void set_fresh_snapshot(bool fresh) { this->fresh_snapshot_ = fresh; }
  void set_credentials(const std::string &username, const std::string &password);
  void set_listeners_sensor(sensor::Sensor *sensor) { this->listeners_sensor_ = sensor; }
#ifdef USE_VISIONSTATE_MICROPHONE
  void set_microphone_source(microphone::MicrophoneSource *source) { this->microphone_source_ = source; }
  /// The gain the microphone's sound is multiplied by (1–64), e.g. from a number entity.
  void set_gain_factor(int32_t gain) { this->microphone_source_->set_gain_factor(gain); }
#endif
#ifdef USE_VISIONSTATE_CAMERA
  void set_camera(camera::Camera *camera) { this->camera_ = camera; }
  void on_camera_image(const std::shared_ptr<camera::CameraImage> &image) override;
#endif

  uint8_t listener_count() const { return this->listener_count_; }

 protected:
  bool authorized_(struct httpd_req *req);
  esp_err_t handle_audio_(struct httpd_req *req);
  esp_err_t handle_snapshot_(struct httpd_req *req);
  static void listener_task_(void *arg);
  void remove_listener_(Listener *listener);

  uint16_t port_{8081};
  uint8_t max_listeners_{2};
  uint32_t buffer_ms_{1000};
  bool fresh_snapshot_{true};
  std::string expected_auth_;  // "Basic <base64>", empty: no login
  sensor::Sensor *listeners_sensor_{nullptr};
  void *httpd_{nullptr};

  Mutex lock_;  // guards listeners_ (the microphone's task writes, the listeners' tasks leave)
  std::vector<Listener *> listeners_;
  volatile uint8_t listener_count_{0};
  int published_count_{-1};
  bool microphone_wanted_{false};

#ifdef USE_VISIONSTATE_MICROPHONE
  microphone::MicrophoneSource *microphone_source_{nullptr};
#endif
#ifdef USE_VISIONSTATE_CAMERA
  camera::Camera *camera_{nullptr};
  SemaphoreHandle_t image_ready_{nullptr};
  std::shared_ptr<camera::CameraImage> image_;
  volatile bool waiting_for_image_{false};
  std::shared_ptr<camera::CameraImage> take_image_(uint32_t timeout_ms);
#endif
};

}  // namespace esphome::visionstate_stream

#endif  // USE_ESP32
