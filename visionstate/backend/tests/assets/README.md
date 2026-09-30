# Test photos

Real photos for the object detector tests (the detector must find real objects, so synthetic
drawings do not work). All are CC0 (public domain dedication) from Wikimedia Commons, scaled
down to 640 px.

| File | What the tests expect | Source |
|---|---|---|
| `driveway.jpg` | cars and one person | [TGSIs in car park driveway Townsville 104 9310.jpg](https://commons.wikimedia.org/wiki/File:TGSIs_in_car_park_driveway_Townsville_104_9310.jpg) |
| `beach.jpg` | one person and several dogs | [Dog owner walking his dogs on the beach..JPG](https://commons.wikimedia.org/wiki/File:Dog_owner_walking_his_dogs_on_the_beach..JPG) |
| `cat.jpg` | one cat | [House cat sitting next to apartment entrance door looking off to side.jpg](https://commons.wikimedia.org/wiki/File:House_cat_sitting_next_to_apartment_entrance_door_looking_off_to_side.jpg) |

`lcd.png` is drawn by `tests/displays.py` (our own, no licence questions) and used by the CI smoke
test for the number reader; the reading tests draw their displays on the fly.
