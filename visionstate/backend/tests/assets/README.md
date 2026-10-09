# Test photos

Real photos for the object detector tests (the detector must find real objects, so synthetic
drawings do not work). All are CC0 (public domain dedication) from Wikimedia Commons, scaled
down to 640 px.

| File | What the tests expect | Source |
|---|---|---|
| `driveway.jpg` | cars and one person | [TGSIs in car park driveway Townsville 104 9310.jpg](https://commons.wikimedia.org/wiki/File:TGSIs_in_car_park_driveway_Townsville_104_9310.jpg) |
| `beach.jpg` | one person and several dogs | [Dog owner walking his dogs on the beach..JPG](https://commons.wikimedia.org/wiki/File:Dog_owner_walking_his_dogs_on_the_beach..JPG) |
| `cat.jpg` | one cat | [House cat sitting next to apartment entrance door looking off to side.jpg](https://commons.wikimedia.org/wiki/File:House_cat_sitting_next_to_apartment_entrance_door_looking_off_to_side.jpg) |

`counter_red.jpg` is the counter of a real water meter (seven wheels, the last three red, read
as `0632289`): one image from the checked readings a user exported from VisionState and shared
in [issue #40](https://github.com/oleost/VisionState/issues/40) under CC0 (the export's
LICENSE.txt), cut to the sensor's region. `counter_turning.jpg` is another image of the same
export (`0632590`, the second-to-last wheel half way from 8 to 9 while the last shows 0), cut to
the region the same way.

`lcd.png` is drawn by `tests/displays.py` (our own, no licence questions) and used by the CI smoke
test for the number reader; the reading tests draw their displays on the fly.
