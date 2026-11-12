#include "answer_player.h"

#include <cstring>

#include "esphome/components/api/api_connection.h"
#include "esphome/components/api/api_pb2.h"
#include "esphome/components/api/api_server.h"
#include "esphome/core/hal.h"
#include "esphome/core/log.h"

namespace esphome::visionstate_answer {

static const char *const TAG = "visionstate_answer";

void AnswerPlayer::setup() {
  this->state = media_player::MEDIA_PLAYER_STATE_IDLE;
  this->player_->add_on_state_callback([this](const std::string &entity_id) { this->follow_(entity_id); });
  this->follow_(this->player_->state);
}

void AnswerPlayer::dump_config() {
  ESP_LOGCONFIG(TAG,
                "VisionState answer player:\n"
                "  Plays on: %s\n"
                "  Start allowance: %" PRIu32 " ms\n"
                "  Start timeout: %" PRIu32 " ms\n"
                "  Max duration: %" PRIu32 " ms\n"
                "  End delay: %" PRIu32 " ms\n"
                "  Speech rate: %" PRIu32 " bytes/s",
                this->entity_id_[0] != '\0' ? this->entity_id_ : "(none)", this->start_allowance_,
                this->start_timeout_, this->max_duration_, this->end_delay_, this->bytes_per_second_);
}

media_player::MediaPlayerTraits AnswerPlayer::get_traits() { return media_player::MediaPlayerTraits(); }

void AnswerPlayer::follow_(const std::string &entity_id) {
  if (entity_id.size() >= ENTITY_ID_SIZE) {
    ESP_LOGW(TAG, "The player's entity id is too long: %s", entity_id.c_str());
    return;
  }
  if (strcmp(entity_id.c_str(), this->entity_id_) == 0)
    return;
  this->finish_("the player changed");
  memcpy(this->entity_id_, entity_id.c_str(), entity_id.size() + 1);
  if (this->entity_id_[0] == '\0')
    return;  // states of the one before no longer match: nothing is followed
  ESP_LOGD(TAG, "Following %s", this->entity_id_);
  if (!this->subscribed_) {
    // Sent to Home Assistant when it connects; the buffer is read again at every connection.
    api::global_api_server->subscribe_home_assistant_state(
        this->entity_id_, nullptr, [this](StringRef state) { this->on_player_state_(state); });
    this->subscribed_ = true;
  }
  // A client connected already asked for the subscriptions once: tell it of this one now. (One that is
  // still connecting gets the list, with this buffer in it, when it asks.)
  for (auto &client : api::global_api_server->active_clients()) {
    if (!client->is_authenticated())
      continue;
    api::SubscribeHomeAssistantStateResponse resp;
    resp.entity_id = StringRef(this->entity_id_);
    resp.attribute = StringRef("");
    resp.once = false;
    if (!client->send_message(resp))
      ESP_LOGW(TAG, "Could not ask Home Assistant for the state of %s", this->entity_id_);
  }
}

void AnswerPlayer::on_player_state_(StringRef state) {
  const bool playing = state == "playing" || state == "buffering";
  ESP_LOGD(TAG, "%s is %s", this->entity_id_, state.c_str());
  if (playing) {
    this->cancel_timeout("end");
    if (this->phase_ == Phase::WAITING_FOR_START) {
      ESP_LOGD(TAG, "The answer started on %s", this->entity_id_);
      this->phase_ = Phase::PLAYING;
      this->start_ms_ = millis();
      this->schedule_deadline_();
    }
  } else if (this->phase_ == Phase::PLAYING) {
    this->set_timeout("end", this->end_delay_, [this]() { this->finish_("played"); });
  }
}

void AnswerPlayer::set_answer_text(const std::string &text) {
  this->text_bytes_ = text.size();
  this->schedule_deadline_();
}

void AnswerPlayer::control(const media_player::MediaPlayerCall &call) {
  const auto &url = call.get_media_url();
  if (!url.has_value()) {
    const auto &command = call.get_command();
    if (command.has_value() && command.value() == media_player::MEDIA_PLAYER_COMMAND_STOP)
      this->finish_("stopped");
    return;
  }
  if (this->entity_id_[0] == '\0') {
    ESP_LOGW(TAG, "No player to play the answer on (Answer on is empty)");
    return;
  }
  ESP_LOGD(TAG, "Playing the answer on %s", this->entity_id_);
  this->answer_trigger_.trigger(url.value());
  // A second URL (an announcement after its chime) replaces the first on the player: wait for it anew.
  this->phase_ = Phase::WAITING_FOR_START;
  this->request_ms_ = millis();
  this->cancel_timeout("end");
  this->schedule_deadline_();
  if (this->state != media_player::MEDIA_PLAYER_STATE_ANNOUNCING) {
    this->state = media_player::MEDIA_PLAYER_STATE_ANNOUNCING;
    this->publish_state();
  }
}

// When to stop waiting without a sign from the player. With the answer's text: its length spoken, after
// the time a player takes to start (from the request, or from the start the player showed, with room
// for slower speech: a player that plays the answer over its music shows no end). Without it: the
// timeouts. Never longer than max_duration.
void AnswerPlayer::schedule_deadline_() {
  if (this->phase_ == Phase::IDLE)
    return;
  const uint32_t speech = this->text_bytes_ != 0 ? this->text_bytes_ * 1000 / this->bytes_per_second_ : 0;
  uint32_t from_request;
  const char *why;
  if (this->phase_ == Phase::WAITING_FOR_START) {
    from_request = speech != 0 ? this->start_allowance_ + speech : this->start_timeout_;
    why = "the player showed no start";
  } else {
    const uint32_t started = this->start_ms_ - this->request_ms_;
    from_request = speech != 0 ? started + speech * 3 / 2 + this->start_allowance_ : this->max_duration_;
    why = "the player showed no end";
  }
  from_request = std::min(from_request, this->max_duration_);
  const uint32_t elapsed = millis() - this->request_ms_;
  const uint32_t delay = from_request > elapsed ? from_request - elapsed : 0;
  this->set_timeout("deadline", delay, [this, why]() { this->finish_(why); });
}

void AnswerPlayer::finish_(const char *why) {
  this->cancel_timeout("deadline");
  this->cancel_timeout("end");
  this->text_bytes_ = 0;
  if (this->phase_ == Phase::IDLE && this->state == media_player::MEDIA_PLAYER_STATE_IDLE)
    return;
  ESP_LOGD(TAG, "Answer done: %s", why);
  this->phase_ = Phase::IDLE;
  this->state = media_player::MEDIA_PLAYER_STATE_IDLE;
  this->publish_state();
}

}  // namespace esphome::visionstate_answer
