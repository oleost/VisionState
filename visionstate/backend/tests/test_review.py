"""Review answers for state sensors: an answer adds the frame to the dataset, a second one changes it."""

from fastapi.testclient import TestClient

from visionstate.main import create_app

from .conftest import requires_model
from .test_storage import STATES, add_frames, settings  # noqa: F401 (fixture)


def samples(client, sid: int) -> list[dict]:
    return [
        {"id": x["id"], "labels": x["labels"], "origin": x["origin"]}
        for x in client.get(f"/api/v1/sensors/{sid}/samples").json()["items"]
    ]


@requires_model
def test_answering_again_changes_the_sample_instead_of_adding_one(settings):  # noqa: F811
    with TestClient(create_app(settings)) as client:
        rt = client.app.state.runtime
        sid = client.post(
            "/api/v1/sensors", json={"name": "Door", "source_type": "http", "source": "x", "states": STATES}
        ).json()["id"]
        first, second = add_frames(rt, sid, [(0, False), (0, False)])

        # The predicted state ("open") is confirmed by mistake: one sample, labelled open.
        assert client.post(f"/api/v1/review/{first}", json={"action": "confirm"}).status_code == 200
        [sample] = samples(client, sid)
        assert sample["labels"] == ["open"] and sample["origin"] == "review"
        assert client.get("/api/v1/review").json()["total"] == 1

        # Corrected from the list: the same sample gets the other label, nothing is added.
        client.post(f"/api/v1/review/{first}", json={"action": "label", "state_key": "closed"})
        assert samples(client, sid) == [{**sample, "labels": ["closed"]}]

        # Skipped after all: the sample goes.
        client.post(f"/api/v1/review/{first}", json={"action": "skip"})
        assert samples(client, sid) == []
        # And answered once more: added again.
        client.post(f"/api/v1/review/{first}", json={"action": "confirm"})
        assert [x["labels"] for x in samples(client, sid)] == [["open"]]

        # A sample deleted in the dataset in the meantime: the answer adds a new one.
        client.post(f"/api/v1/review/{second}", json={"action": "label", "state_key": "closed"})
        mine = next(x["id"] for x in samples(client, sid) if x["labels"] == ["closed"])
        client.post(f"/api/v1/sensors/{sid}/samples/delete", json={"sample_ids": [mine]})
        client.post(f"/api/v1/review/{second}", json={"action": "confirm"})
        assert sorted(x["labels"][0] for x in samples(client, sid)) == ["open", "open"]
        assert client.get("/api/v1/review").json()["total"] == 0
