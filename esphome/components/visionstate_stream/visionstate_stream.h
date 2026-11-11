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
#include "esphome/core/automation.h"
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
  /// Paused, the stream carries silence without the microphone being muted (which would mute it for
  /// everything else on the device too, such as a voice assistant).
  void set_paused(bool paused) { this->paused_ = paused; }
  bool is_paused() const { return this->paused_; }
  /// Off, the stream carries silence (a switch of its own, apart from the microphone's mute and from
  /// the pause for a voice assistant).
  void set_stream_enabled(bool enabled) { this->stream_enabled_ = enabled; }
  /// The microphone's own rate: 16000, or 48000 (filtered and taken down to 16 kHz here).
  void set_input_rate(uint32_t rate) { this->decimate_ = rate / SAMPLE_RATE_OUT; }
  void set_level_sensor(sensor::Sensor *sensor) { this->level_sensor_ = sensor; }
  void set_peak_sensor(sensor::Sensor *sensor) { this->peak_sensor_ = sensor; }
  void set_clipped_sensor(sensor::Sensor *sensor) { this->clipped_sensor_ = sensor; }
  void set_dropped_sensor(sensor::Sensor *sensor) { this->dropped_sensor_ = sensor; }
  void set_diagnostics_interval(uint32_t ms) { this->diagnostics_ms_ = ms; }
  static const uint32_t SAMPLE_RATE_OUT = 16000;
#endif
#ifdef USE_VISIONSTATE_CAMERA
  void set_camera(camera::Camera *camera) { this->camera_ = camera; }
  void on_camera_image(const std::shared_ptr<camera::CameraImage> &image) override;
  /// What the camera looks at (packages/camera-use.yaml): "States", "Meters and displays" or
  /// "People, cars and animals". A snapshot can ask for one too (?use=states|meter|movement).
  void set_camera_use(const std::string &use);
  /// The fixed exposure of "People, cars and animals" (the sensor's lines, 0-1200).
  void set_motion_exposure(int lines);
  /// The picture turned or mirrored on the camera chip (packages/camera-use.yaml): "Normal",
  /// "Mirrored", "Upside down" or "Upside down, mirrored", from ESPHome's default for the camera.
  void set_orientation(const std::string &orientation);
  /// Fired (in the main loop) when the camera use changed, with its name.
  Trigger<std::string> *get_camera_use_trigger() { return &this->camera_use_trigger_; }
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
  volatile bool paused_{false};
  volatile bool stream_enabled_{true};
  // Taking a 48 kHz microphone down to 16 kHz: a low-pass filter, then every third sample.
  uint32_t decimate_{1};
  std::vector<float> taps_;
  std::vector<float> history_;  // the last taps_.size() input samples, a ring
  size_t history_pos_{0};
  uint32_t phase_{0};
  std::vector<uint8_t> out_;
  void deliver_(const uint8_t *data, size_t len);
  // Diagnostics, counted by the microphone's task and published by the main loop.
  sensor::Sensor *level_sensor_{nullptr};
  sensor::Sensor *peak_sensor_{nullptr};
  sensor::Sensor *clipped_sensor_{nullptr};
  sensor::Sensor *dropped_sensor_{nullptr};
  uint32_t diagnostics_ms_{10000};
  uint32_t last_diagnostics_{0};
  double sum_squares_{0};
  uint32_t samples_{0};
  int32_t peak_{0};
  uint32_t clipped_{0};
  uint32_t dropped_bytes_{0};
  void publish_diagnostics_();
#endif
#ifdef USE_VISIONSTATE_CAMERA
  camera::Camera *camera_{nullptr};
  SemaphoreHandle_t image_ready_{nullptr};
  std::shared_ptr<camera::CameraImage> image_;
  volatile bool waiting_for_image_{false};
  std::shared_ptr<camera::CameraImage> take_image_(uint32_t timeout_ms);
  bool apply_camera_use_(uint8_t use);
  uint8_t camera_use_{255};  // none set yet
  int motion_exposure_{250};
  volatile bool camera_use_changed_{false};
  Trigger<std::string> camera_use_trigger_;
#endif
};

}  // namespace esphome::visionstate_stream

#endif  // USE_ESP32
