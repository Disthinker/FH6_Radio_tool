# Marker Reference
The names below are based on the current FH radio XML workflow and community testing. Some markers may behave slightly differently depending on the radio station and bank, so always preview in the tool and test in game.

### Common playback markers

- **TrackStart**: The normal start position of the song. Usually `0`.
- **TrackLoopStart**: The start of the main loop used during normal/race playback.
- **TrackLoopEnd**: The end of the main loop. When the loop is active, playback should jump back to `TrackLoopStart`.
- **PostRaceLoopStart**: The start of the post-race loop section.
- **PostRaceLoopEnd**: The end of the post-race loop section. When the post-race loop is active, playback should jump back to `PostRaceLoopStart`.
- **End**: The end sample of the audio file. Usually this should be the last valid sample.

### Less obvious markers

- **TrackDrop**: A high-energy entry point for the track, usually used around race start or when the game wants to jump into a stronger part of the song. A good placement is the first chorus/drop/main beat after the intro. If the song has no clear drop, use `TrackLoopStart` or a musically strong point.

- **PostDrop**: A transition point used for finish/post-race style playback. A good placement is a chorus, final drop, or another energetic section that sounds natural after the race finish. If unsure, place it near `PostRaceLoopStart` or reuse a strong chorus/drop point.

- **DJSegment**: A marker related to DJ/radio segment insertion. For normal custom song replacement, this is usually not needed. If you do not have a specific DJ segment, leave it as `-1`.

- **DJStart**: The start point for a DJ/radio voice segment. For normal music replacement, leave it as `-1` unless you intentionally prepare a DJ/voice section.

- **StingerStart**: A marker for a short stinger/transition sound. Most custom songs do not need this. Leave it as `-1` unless you know the track has a dedicated stinger section.

### Practical recommendations

For most custom songs, start with this simple strategy:

1. Set `TrackStart` to `0`.
2. Set `End` to the final sample of the file.
3. Choose a stable chorus or main beat loop for `TrackLoopStart` and `TrackLoopEnd`.
4. Set `TrackDrop` to the first strong drop/chorus after the intro.
5. Set `PostDrop` to a strong chorus/final drop, or near `PostRaceLoopStart`.
6. Set `PostRaceLoopStart` and `PostRaceLoopEnd` to a section that can loop after a race.
7. Leave `DJSegment`, `DJStart`, and `StingerStart` as `-1` unless you specifically need them.

Automatic loop candidates are only a helper. Manual preview and fine tuning are still strongly recommended.
