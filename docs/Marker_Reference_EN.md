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

- **DJDrop**: The point where music resumes after a DJ/radio host line. It usually stays close to `TrackDrop` or the original XML DJ entry point. Normal users should leave it automatic instead of copying an old-song coordinate.

- **PostDrop**: A transition point used for finish/post-race style playback. A good placement is a chorus, final drop, or another energetic section that sounds natural after the race finish. If unsure, place it near `PostRaceLoopStart` or reuse a strong chorus/drop point.

- **DJSegment**: A marker related to DJ/radio segment insertion. For normal custom song replacement, leave it automatic so the tool can derive it from the original slot template and final WAV length.

- **DJStart**: The point near the end of music where a DJ/radio voice can enter. The tool automatically places it close to the end of the final WAV.

- **StingerStart**: A marker for a short stinger/transition sound. Original XML data shows it is usually `1000` samples before `DJStart`, and the tool keeps that relationship automatically.

### Practical recommendations

For most custom songs, start with this simple strategy:

1. Set `TrackStart` to `0`.
2. `SampleLength` is the total sample count; set `End` to the final sample index, which is `SampleLength - 1`.
3. Choose a stable chorus or main beat loop for `TrackLoopStart` and `TrackLoopEnd`.
4. Set `TrackDrop` to the first strong drop/chorus after the intro.
5. Set `PostDrop` to a strong chorus/final drop, or near `PostRaceLoopStart`.
6. Set `PostRaceLoopStart` and `PostRaceLoopEnd` to a section that can loop after a race.
7. Leave `DJDrop`, `DJSegment`, `DJStart`, and `StingerStart` automatic. Advanced users can write `DISABLE` in CSV only when they explicitly want XML `-1`.

Automatic loop candidates are only a helper. Manual preview and fine tuning are still strongly recommended.
