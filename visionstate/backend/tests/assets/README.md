# Test photos

Real photos for the object detector tests (the detector must find real objects, so synthetic
drawings do not work). All are CC0 (public domain dedication) from Wikimedia Commons, scaled
down to 640 px.

| File | What the tests expect | Source |
|---|---|---|
| `driveway.jpg` | cars and one person | [TGSIs in car park driveway Townsville 104 9310.jpg](https://commons.wikimedia.org/wiki/File:TGSIs_in_car_park_driveway_Townsville_104_9310.jpg) |
| `beach.jpg` | one person and several dogs | [Dog owner walking his dogs on the beach..JPG](https://commons.wikimedia.org/wiki/File:Dog_owner_walking_his_dogs_on_the_beach..JPG) |
| `cat.jpg` | one cat | [House cat sitting next to apartment entrance door looking off to side.jpg](https://commons.wikimedia.org/wiki/File:House_cat_sitting_next_to_apartment_entrance_door_looking_off_to_side.jpg) |

The face tests use NASA portraits, works of the US federal government and so in the public domain,
from Wikimedia Commons, scaled down:

| File | Who | Source |
|---|---|---|
| `face_williams_2006.jpg` | Suni Williams | [Sunita Williams.jpg](https://commons.wikimedia.org/wiki/File:Sunita_Williams.jpg) |
| `face_williams_2018.jpg` | Suni Williams, twelve years later | [Sunita Williams in 2018.jpg](https://commons.wikimedia.org/wiki/File:Sunita_Williams_in_2018.jpg) |
| `face_wilmore.jpg` | Butch Wilmore | [Barry Wilmore.jpg](https://commons.wikimedia.org/wiki/File:Barry_Wilmore.jpg) |
| `face_crew.jpg` | Williams (left) and Wilmore, 2024 | [Boeing CFT Crew Portrait-Sunita Williams and Barry Wilmore (KSC-20240404-PH-NAS01 0001).jpg](https://commons.wikimedia.org/wiki/File:Boeing_CFT_Crew_Portrait-Sunita_Williams_and_Barry_Wilmore_(KSC-20240404-PH-NAS01_0001).jpg) |
| `face_group.jpg` | A crew of nine with both of them | [The official Expedition 71 crew portrait with CFT crew.jpg](https://commons.wikimedia.org/wiki/File:The_official_Expedition_71_crew_portrait_with_CFT_crew.jpg) |

`counter_red.jpg` is the counter of a real water meter (seven wheels, the last three red, read
as `0632289`): one image from the checked readings a user exported from VisionState and shared
in [issue #40](https://github.com/oleost/VisionState/issues/40) under CC0 (the export's
LICENSE.txt), cut to the sensor's region. `counter_turning.jpg` is another image of the same
export (`0632590`, the second-to-last wheel half way from 8 to 9 while the last shows 0), cut to
the region the same way.

`lcd.png` is drawn by `tests/displays.py` (our own, no licence questions) and used by the CI smoke
test for the number reader; the reading tests draw their displays on the fly.

The demo sensors (`scripts/seed_demo.py`, the README screenshots) use:

| File | What | Source |
|---|---|---|
| `demo_driveway.jpg` | A driveway seen from above: Tom and a girl with a parcel, cars, a bicycle and a cat | Made by the maintainer with Google Gemini (AI, no real people), CC0 |
| `demo_tom.jpg` | Tom alone on the same driveway: a face to teach on the camera | Made by the maintainer with Google Gemini (AI, no real people), CC0 |
| `demo_tom_photo.jpg` | Tom close up, as a photo from a phone: a face to teach with Add photos | Made by the maintainer with Google Gemini (AI, no real people), CC0 |
| `demo_garage.jpg` | A garage door from inside, cut; the script draws it open and half open | [Utility building interior view of wood panel overhead garage doors](https://commons.wikimedia.org/wiki/File:UTILITY_BUILDING_INTERIOR_VIEW_OF_WOOD_PANEL_OVERHEAD_GARAGE_DOORS,_LOOKING_NORTHWEST._-_Tucson_Plant_Materials_Center,_General_Utility_Building,_3241_North_Romero_Road,_HABS_ARIZ,10-TUCSO,32C-7.tif), HABS (public domain) |

"Tom" is a made-up name. The water meter is `counter_turning.jpg` above.

`backups/` holds a backup made by each database version and backup format (`backup-format<F>-schema<S>.zip`),
drawn by our own code (`tests/backup_fixture.py`): every later version's tests restore each of
them. Never change or remove one; add the next with `python -m tests.backup_fixture`.
