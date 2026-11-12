#pragma once

#include <string>

#include "esphome/components/media_player/media_player.h"
#include "esphome/components/text/text.h"
#include "esphome/core/automation.h"
#include "esphome/core/component.h"

namespace esphome::visionstate_answer {

// A media player that plays nothing itself: the answer goes to a player in Home Assistant (on_answer),
// and this one stays announcing until that one is done, so a voice assistant waits for it.
//
// Done is seen in the player's state (playing or buffering, then anything else). A player that plays
// an announcement over its music, or without showing it in its state, gives no such sign: then the
// answer's length, guessed from its text, says when it is done.
class AnswerPlayer : public media_player::MediaPlayer, public Component {
 public:
  void setup() override;
  void dump_config() override;
  float get_setup_priority() const override { return setup_priority::LATE; }  // after the text restored its value

  media_player::MediaPlayerTraits get_traits() override;

  void set_player(text::Text *player) { this->player_ = player; }
  void set_start_timeout(uint32_t ms) { this->start_timeout_ = ms; }
  void set_start_allowance(uint32_t ms) { this->start_allowance_ = ms; }
  void set_max_duration(uint32_t ms) { this->max_duration_ = ms; }
  void set_end_delay(uint32_t ms) { this->end_delay_ = ms; }
  void set_bytes_per_second(uint32_t rate) { this->bytes_per_second_ = rate; }
  Trigger<std::string> *get_answer_trigger() { return &this->answer_trigger_; }

  // The answer's text (the voice assistant's on_tts_start), before or after its URL came.
  void set_answer_text(const std::string &text);

 protected:
  void control(const media_player::MediaPlayerCall &call) override;

  void follow_(const std::string &entity_id);
  void on_player_state_(StringRef state);
  void schedule_deadline_();
  void finish_(const char *why);

  enum class Phase : uint8_t { IDLE, WAITING_FOR_START, PLAYING };
  Phase phase_{Phase::IDLE};
  uint32_t request_ms_{0};
  uint32_t start_ms_{0};
  size_t text_bytes_{0};  // 0: no text known

  text::Text *player_{nullptr};
  uint32_t start_timeout_{15000};
  uint32_t start_allowance_{5000};
  uint32_t max_duration_{120000};
  uint32_t end_delay_{500};
  uint32_t bytes_per_second_{12};
  Trigger<std::string> answer_trigger_;

  // The entity followed: Home Assistant's API keeps a pointer to it, so it is changed in place.
  static constexpr size_t ENTITY_ID_SIZE = 128;
  char entity_id_[ENTITY_ID_SIZE]{};
  bool subscribed_{false};
};

}  // namespace esphome::visionstate_answer
