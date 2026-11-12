"""VisionState answer: a media player on the device for a voice assistant with no speaker of its own.

The answer plays on a media player in Home Assistant (whose entity id a text entity holds), and this
player stays *announcing* until that one has played it. The voice assistant waits for it before it
listens for a follow-up, so it does not hear its own answer (see media_player.py).
"""

CODEOWNERS = ["@oleost"]
