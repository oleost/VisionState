import numpy as np
from PIL import Image

from visionstate import classifier, imaging
from visionstate.api.sensors import quality_tips
from visionstate.engine import Debouncer, review_reason
from visionstate.mqtt import SensorDescriptor, discovery_messages, topics
from visionstate.settings import REVIEW_DEFAULTS, UNKNOWN_STATE, merge_review


def test_debouncer_publishes_first_value_immediately_then_waits():
    d = Debouncer()
    assert d.update("closed", 2) is True
    assert d.published == "closed"
    assert d.update("open", 2) is False  # one result is not enough
    assert d.published == "closed"
    assert d.update("open", 2) is True
    assert d.published == "open"


def test_debouncer_resets_on_flicker():
    d = Debouncer()
    d.update("closed", 3)
    d.update("open", 3)
    d.update("closed", 3)
    d.update("open", 3)
    assert d.published == "closed"


def test_review_reason_priorities():
    rules = merge_review(None, {"spot_rate": 0.05})
    assert review_reason(rules, 0.5, 0.7, 0, 10_000, 0.5) == "low_confidence"
    assert review_reason(rules, 0.99, 0.7, REVIEW_DEFAULTS["flip_limit"], 10_000, 0.5) == "flip"
    assert review_reason(rules, 0.99, 0.7, 0, 10_000, 0.0) == "spot_check"
    assert review_reason(rules, 0.99, 0.7, 0, 10_000, 0.9) is None
    assert review_reason(rules, 0.1, 0.7, 0, 1, 0.0) is None  # cooldown


def test_review_rules_layering():
    assert merge_review(None, None)["spot_rate"] == 0.0  # no random spot checks by default
    rules = merge_review({"below": 0.8, "cooldown_s": 60}, {"below": 0.6, "cooldown_s": None})
    assert rules["below"] == 0.6  # sensor override wins
    assert rules["cooldown_s"] == 60  # empty override falls back to global
    # 83 % is not flagged with a 80 % review limit ...
    assert review_reason(rules | {"below": 0.8}, 0.83, 0.7, 0, 10_000, 0.5) is None
    # ... and the review limit never drops below the reporting threshold.
    assert review_reason(rules | {"below": 0.3}, 0.65, 0.7, 0, 10_000, 0.5) == "low_confidence"
    assert review_reason(rules | {"enabled": False}, 0.1, 0.7, 0, 10_000, 0.0) is None


def test_roi_normalisation_and_crop():
    assert imaging.normalise_roi(None) is None
    assert imaging.normalise_roi({"x": 0, "y": 0, "w": 1, "h": 1}) is None
    roi = imaging.normalise_roi({"x": 0.5, "y": 0.5, "w": 0.9, "h": 0.2})
    assert roi["w"] == 0.5  # clamped to the frame
    assert imaging.roi_key(None) == imaging.FULL_FRAME_KEY
    image = Image.new("RGB", (200, 100))
    assert imaging.crop(image, {"x": 0.25, "y": 0.5, "w": 0.5, "h": 0.5}).size == (100, 50)


def test_night_detection():
    grey = Image.new("RGB", (32, 32), (90, 90, 90))
    colour = Image.new("RGB", (32, 32), (200, 80, 30))
    assert imaging.is_night(grey)
    assert not imaging.is_night(colour)


def test_discovery_payloads():
    messages = dict(
        discovery_messages("homeassistant", SensorDescriptor("garage_door", "Garage door", ["open", "closed"]))
    )
    state = messages["homeassistant/sensor/visionstate_garage_door/state/config"]
    assert state["options"] == ["open", "closed", UNKNOWN_STATE]
    assert state["device_class"] == "enum"
    assert state["state_topic"] == topics("garage_door")["state"]
    assert all(p["device"]["identifiers"] == ["visionstate_garage_door"] for p in messages.values())
    assert len({p["unique_id"] for p in messages.values()}) == len(messages)


def test_classifier_trains_and_predicts_with_missing_states():
    rng = np.random.default_rng(0)
    a = rng.normal(0, 0.1, (10, 8)) + 1
    b = rng.normal(0, 0.1, (10, 8)) - 1
    result = classifier.train(
        np.vstack([a, b]), ["open"] * 10 + ["closed"] * 10, "test", 1, ["open", "closed", "partial"]
    )
    assert result.head is not None
    assert result.accuracy == 1.0
    probs = result.head.predict(a[:1], ["open", "closed", "partial"])[0]
    assert probs["partial"] == 0.0
    assert max(probs, key=probs.get) == "open"


def test_classifier_needs_two_states():
    result = classifier.train(np.ones((3, 4)), ["open"] * 3, "test", 1, ["open", "closed"])
    assert result.head is None


def test_quality_tips_flag_small_states():
    states = [{"key": "open", "name": "Open"}, {"key": "closed", "name": "Closed"}]
    counts = {"per_state": {"open": {"day": 30, "night": 6}, "closed": {"day": 3, "night": 0}}}
    titles = [t["title"] for t in quality_tips(states, counts, None)]
    assert any("Closed has 3 samples" in t for t in titles)
    assert any("Closed has only 0 night images" in t for t in titles)


def test_polygon_roi_normalises_masks_and_keys():
    from visionstate.settings import NEUTRAL_FILL

    triangle = {"x": 0, "y": 0, "w": 1, "h": 1, "points": [[0.1, 0.1], [0.9, 0.1], [0.1, 0.9]]}
    roi = imaging.normalise_roi(triangle)
    assert roi == {"x": 0.1, "y": 0.1, "w": 0.8, "h": 0.8, "points": [[0.1, 0.1], [0.9, 0.1], [0.1, 0.9]]}
    # A polygon that is just the rectangle is stored as a rectangle.
    square = {"x": 0, "y": 0, "w": 1, "h": 1, "points": [[0.2, 0.2], [0.6, 0.2], [0.6, 0.7], [0.2, 0.7]]}
    assert imaging.normalise_roi(square) == {"x": 0.2, "y": 0.2, "w": 0.4, "h": 0.5}
    # Different shapes with the same bounding box get different cache keys.
    other = {**triangle, "points": [[0.9, 0.9], [0.9, 0.1], [0.1, 0.9]]}
    assert imaging.roi_key(triangle) != imaging.roi_key(other) != imaging.roi_key(square)

    white = Image.new("RGB", (100, 100), (255, 255, 255))
    out = imaging.crop(white, roi)
    assert out.size == (80, 80)
    assert out.getpixel((5, 5)) == (255, 255, 255)  # inside the triangle
    assert out.getpixel((75, 75)) == NEUTRAL_FILL  # outside -> neutral


def test_reading_verdict():
    from visionstate.engine.logic import reading_verdict
    from visionstate.readers import Text
    from visionstate.settings import merge_reading

    counter = merge_reading({"mode": "counter", "decimals": 1, "max_step": 0})
    wheels = merge_reading({"mode": "counter", "decimals": 1, "display": "counter", "digits": 5, "max_step": 0})
    value = merge_reading({"mode": "value", "decimals": 0, "max_step": 10})
    sure = 0.9

    assert reading_verdict(Text("", 0.0), None, 5.0, 0.5, counter) == ("nothing read", False)
    assert reading_verdict(Text("1234", sure), 123.4, None, 0.5, wheels) == ("wrong digit count", False)
    assert reading_verdict(Text("12345", 0.3), 1234.5, None, 0.5, counter) == ("unsure", False)
    assert reading_verdict(Text("12345", sure), 1234.5, 1234.4, 0.5, counter) == (None, False)  # accepted
    assert reading_verdict(Text("12340", sure), 1234.0, 1234.5, 0.5, counter) == ("went down", False)
    # One step below: the last wheel turning — kept, not rejected.
    assert reading_verdict(Text("12344", sure), 1234.4, 1234.5, 0.5, counter) == (None, True)
    assert reading_verdict(Text("40", sure), 40.0, 20.0, 0.5, value) == ("changed too much", False)
