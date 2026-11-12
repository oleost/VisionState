"""The answer player: give it to the voice assistant as its media player.

    media_player:
      - platform: visionstate_answer
        id: vs_answer
        player: vs_answer_on          # a text entity holding the Home Assistant player's entity id
        on_answer:                    # x: the answer's URL; play it on that player
          - homeassistant.action: …

It is *announcing* from the answer's URL until the player in Home Assistant has gone from playing (or
buffering) to anything else. It follows the player's state in Home Assistant, also after the text names
another one. A player that shows no start or no end (one that plays an announcement over its music, or
without changing its state) is waited for as long as the answer's text takes to say, given with
set_answer_text from the voice assistant's on_tts_start:

    voice_assistant:
      media_player: vs_answer
      on_tts_start:
        - lambda: id(vs_answer).set_answer_text(x);
"""

from esphome import automation
import esphome.codegen as cg
from esphome.components import media_player, text
import esphome.config_validation as cv

DEPENDENCIES = ["api"]

CONF_PLAYER = "player"
CONF_ON_ANSWER = "on_answer"
CONF_START_TIMEOUT = "start_timeout"
CONF_START_ALLOWANCE = "start_allowance"
CONF_SPEECH_RATE = "speech_rate"
CONF_MAX_DURATION = "max_duration"
CONF_END_DELAY = "end_delay"

visionstate_answer_ns = cg.esphome_ns.namespace("visionstate_answer")
AnswerPlayer = visionstate_answer_ns.class_("AnswerPlayer", media_player.MediaPlayer, cg.Component)

CONFIG_SCHEMA = media_player.media_player_schema(AnswerPlayer).extend(
    {
        cv.Required(CONF_PLAYER): cv.use_id(text.Text),
        cv.Required(CONF_ON_ANSWER): automation.validate_automation(single=True),
        # How long a player may take to start (a Google Cast speaker: 1 to 3 s), added to the text's length.
        cv.Optional(CONF_START_ALLOWANCE, default="5s"): cv.positive_time_period_milliseconds,
        # How fast the answer is spoken, in bytes of its UTF-8 text a second: slower than speech, so the
        # guess is too long rather than too short (Norwegian Piper: about 15; a CJK character is 3 bytes).
        cv.Optional(CONF_SPEECH_RATE, default=12): cv.int_range(min=1, max=100),
        # Without a text: how long to wait for a start the player does not show.
        cv.Optional(CONF_START_TIMEOUT, default="15s"): cv.positive_time_period_milliseconds,
        cv.Optional(CONF_MAX_DURATION, default="2min"): cv.positive_time_period_milliseconds,
        # After the player says it is done: what is still on its way to the speaker.
        cv.Optional(CONF_END_DELAY, default="500ms"): cv.positive_time_period_milliseconds,
    }
).extend(cv.COMPONENT_SCHEMA)


async def to_code(config):
    var = await media_player.new_media_player(config)
    await cg.register_component(var, config)
    cg.add_define("USE_API_HOMEASSISTANT_STATES")
    cg.add(var.set_player(await cg.get_variable(config[CONF_PLAYER])))
    cg.add(var.set_start_timeout(config[CONF_START_TIMEOUT]))
    cg.add(var.set_start_allowance(config[CONF_START_ALLOWANCE]))
    cg.add(var.set_bytes_per_second(config[CONF_SPEECH_RATE]))
    cg.add(var.set_max_duration(config[CONF_MAX_DURATION]))
    cg.add(var.set_end_delay(config[CONF_END_DELAY]))
    await automation.build_automation(var.get_answer_trigger(), [(cg.std_string, "x")], config[CONF_ON_ANSWER])
