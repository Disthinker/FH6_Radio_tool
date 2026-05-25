# FH6 Radio Audio Replacement Mechanism and Operation Guide

This document explains how the FH6 radio audio system appears to work from the perspective of modding and tool development.  
It focuses on how to replace normal radio songs, main menu music, stingers/jingles, VO_DJ lines, and some sound effects more safely.

This guide is based on FH6 Radio Tool developer scan results and practical replacement tests.

---

## 0. Core Rule

**Bank files contain the actual audio. RadioInfo XML controls normal song lists, display metadata, and song timing markers. Game-side / FMOD event logic controls when DJ lines, stingers, and transition sounds are triggered.**

So different replacement targets require different files:

```text
Replacing normal songs:
Modify R*_Tracks_*.assets.bank + RadioInfo_*.xml

Replacing main menu music:
Modify GLB_RadioPressStart.assets.bank

Replacing stingers / jingles:
Modify R*_Stingers_LANGUAGE.assets.bank, usually without editing RadioInfo XML

Replacing DJ voice lines:
Modify VO_DJ_*_LANGUAGE.assets.bank, usually without editing RadioInfo XML

Changing song loops, drops, or end points:
Modify marker / SampleLength / End fields in RadioInfo_*.xml
```

---

## 1. Three-Layer Radio System Model

### 1.1 Audio Bank Layer

Main path:

```text
media/Audio/FMODBanks/*.bank
media/Audio/FMODBanks/*.assets.bank
```

Developer scan results show that most banks appear in pairs:

```text
XXX.bank
XXX.assets.bank
```

For audio replacement, the most important file is usually:

```text
XXX.assets.bank
```

A practical interpretation is:

```text
XXX.bank          Metadata / event / index side
XXX.assets.bank   Actual sample payload side
```

Most extractable audio appears in `.assets.bank`. When replacing audio, the usual target is a specific `sound_*.wav` inside an `.assets.bank`.

---

### 1.2 RadioInfo XML Layer

Main files:

```text
media/Audio/RadioInfo_EN.xml
media/Audio/RadioInfo_CN.xml
media/Audio/RadioInfo_JP.xml
...
```

RadioInfo XML mainly controls normal radio songs:

```text
1. Which songs belong to each station
2. Song title / artist display
3. SampleLength / End
4. TrackDrop / PostDrop
5. TrackLoopStart / TrackLoopEnd
6. PostLoopStart / PostLoopEnd or PostRaceLoopStart / PostRaceLoopEnd
7. FreeRoam / Event / Streamer Mode playlists
```

If you replace a normal song bank but do not update XML, the game may show issues such as:

```text
Wrong song title
Song ending too early
Wrong loop points
Radio stops after one song
Incorrect transition timing
Wrong race-start or finish-line entry point
```

---

### 1.3 Game / FMOD Event Logic Layer

This layer is not fully editable by the current tool. It likely controls:

```text
When DJ lines are inserted
When stingers are played
When race-start transitions happen
When post-race loops are used
Which logic is used for menu, free roam, race, and Streamer Mode
```

This explains why:

```text
Song replacement can be relatively stable.
DJ / stinger audio can be replaced as raw audio assets.
But the exact trigger timing of DJ / stinger audio is not fully controllable through RadioInfo XML.
```

---

## 2. Main Bank Categories and Their Uses

The game contains many banks, but only some categories are important for radio replacement.

---

### 2.1 Normal Song Banks: `R*_Tracks_*.assets.bank`

Typical files:

```text
R1_Tracks_CU1.assets.bank
R1_Tracks_Disk.assets.bank
R2_Tracks_CU1.assets.bank
R2_Tracks_Disk.assets.bank
R3_Tracks_CU1.assets.bank
R4_Tracks_CU1.assets.bank
R5_Tracks_Disk.assets.bank
R6_Tracks_CU1.assets.bank
R7_Tracks_CU1.assets.bank
R8_Tracks_CU1.assets.bank
R9_Tracks_CU1.assets.bank
R10_Tracks_Disk.assets.bank
```

Purpose:

```text
Store normal radio station songs
Each sound_x.wav is usually one song
Duration is usually from tens of seconds to several minutes
```

Normal song replacement mainly means:

```text
Find the song slot in XML
Find the corresponding R*_Tracks_*.assets.bank
Replace the corresponding sound_x.wav
Rebuild the bank
Patch RadioInfo XML
```

---

### 2.2 Main Menu Music Bank: `GLB_RadioPressStart.assets.bank`

The main menu / Press Start music is located in:

```text
GLB_RadioPressStart.assets.bank
```

Characteristics:

```text
Contains one long music track
Not part of normal station song slots
```

Replacement workflow:

```text
Locate GLB_RadioPressStart.assets.bank
Extract the only audio entry
Replace it with the user's selected music
Rebuild the bank
```

This usually does not require the normal RadioInfo XML workflow.

---

### 2.3 Global / Special Radio Bank: `GLB_Radio_3D.assets.bank`

Developer scan results show:

```text
GLB_Radio_3D.assets.bank
Contains multiple long audio entries
May be related to global radio, 3D radio, Streamer Mode, or special states
```

Important note:

```text
It is not a normal R1-R9 station song bank.
But it appears frequently in Streamer Mode-related candidate mappings.
```

So it should not be ignored when investigating Streamer Mode or the issue where free roam / Streamer Mode stops after one song.

---

### 2.4 Stinger / Jingle Banks: `R*_Stingers_LANGUAGE.assets.bank`

Typical files:

```text
R1_Stingers_EN.assets.bank
R1_Stingers_CN.assets.bank
R2_Stingers_EN.assets.bank
...
```

Purpose:

```text
Station intros
Station IDs
Short transitions
Jingles
Radio identity sounds
```

Typical structure:

```text
Each extractable stinger bank contains several short audio clips
Most clips are a few seconds to around ten-plus seconds
```

Stinger replacement usually means:

```text
Replace sound_x.wav inside R*_Stingers_LANGUAGE.assets.bank
Keep the same number of sounds
Keep the same order
Keep duration close to the original if possible
```

RadioInfo XML is usually not required for this workflow.

Key point:

```text
Stinger trigger timing is likely controlled by FMOD events / game logic,
not by normal song XML.
```

So you can replace “what sound plays”, but you may not be able to directly control “when it plays”.

---

### 2.5 DJ Voice Banks: `VO_DJ_*_LANGUAGE.assets.bank`

Typical files:

```text
VO_DJ_01_EN.assets.bank
VO_DJ_01_CN.assets.bank
VO_DJ_02_EN.assets.bank
...
```

Purpose:

```text
DJ speech
Radio host introductions
Comments between songs
Race / event-related radio dialogue
```

Structure:

```text
Each VO_DJ bank usually contains many short voice clips
Each clip may correspond to one DJ line or one radio event response
Different languages have different banks
```

Core DJ replacement rules:

```text
1. Replace VO_DJ_*_LANGUAGE.assets.bank
2. Keep the same sound count
3. Keep the same sound order
4. Keep replacement line duration close to the original
5. Do not expect RadioInfo XML to control DJ trigger timing
```

Risks:

```text
New DJ line too long: may overlap with music or stingers
New DJ line too short: may create awkward silence
Wrong sound index: DJ may speak the wrong line in the wrong scene
```

---

### 2.6 Horn / SFX Banks: Not Normal Radio Songs

Examples:

```text
Horn_SFX_*.assets.bank
Horn_Musical_*.assets.bank
```

These may appear as false-positive matches in mapping tables due to similar names, for example:

```text
Speed
Rewind
Falling
777
```

But they are not normal radio songs.

The tool should avoid:

```text
Mapping a song to a Horn / SFX bank only because of a text match
```

A safer normal song candidate should satisfy:

```text
candidate_bank_role = radio_tracks
duration is close
bank name matches R*_Tracks_*.assets
```

---

## 3. Approximate Station-to-Bank Mapping

Based on developer scan results, normal stations roughly map as follows:

```text
Horizon Pulse
→ R1_Tracks_CU1.assets.bank
→ R1_Tracks_Disk.assets.bank

Horizon Bass Arena
→ R2_Tracks_CU1.assets.bank
→ R2_Tracks_Disk.assets.bank

Horizon Block Party
→ R3_Tracks_CU1.assets.bank

Horizon XS
→ R4_Tracks_CU1.assets.bank

Hospital Records
→ R5_Tracks_Disk.assets.bank

Gacha City Radio
→ R6_Tracks_CU1.assets.bank
→ small number of candidates may involve R5_Tracks_Disk.assets.bank

Sub Pop Records
→ R7_Tracks_CU1.assets.bank

Horizon Wave
→ R8_Tracks_CU1.assets.bank
→ small number of candidates may involve R3_Tracks_CU1.assets.bank

Horizon Opus
→ R9_Tracks_CU1.assets.bank

Streamer Mode
→ Not a single simple bank
→ Reuses songs from multiple stations
→ May also involve GLB_Radio_3D.assets.bank
```

Important:

**Do not rely only on the R number.**

For example:

```text
Gacha City Radio is mainly R6, but a few candidates map to R5.
Horizon Wave is mainly R8, but a few candidates map to R3.
Streamer Mode is more complex and appears to combine multiple station/global contents.
```

The tool should not hardcode:

```text
R6 station = only modify R6_Tracks_CU1
```

Instead, use:

```text
RadioInfo XML
xml_to_bank_mapping.csv
fmod_all_bank_audio_inventory.csv
actual sample_length
bank_role
```

---

## 4. Song Marker Field Meanings

These are the most important fields in normal song XML.

---

### 4.1 `SampleLength` / `End`

Meaning:

```text
Total sample count of the final prepared WAV
```

Critical rule:

```text
Use the final WAV that is actually written into the bank.
Do not use the original MP3 / FLAC length.
Do not use an intermediate WAV length.
```

If `SampleLength` / `End` is wrong, possible issues include:

```text
Song ends too early
Radio stops after one song
Delayed transition
Wrong loop points
Game thinks the song is already finished
```

---

### 4.2 `TrackDrop`

Inferred meaning:

```text
Preferred entry point for normal playback, race start, or track punch-in
```

Think of it as:

```text
“Start here for stronger energy.”
```

Usually this should be placed at the chorus, drop, or main melody section.

---

### 4.3 `PostDrop`

Inferred meaning:

```text
Preferred entry point for post-race, finish-line, or special transition states
```

Think of it as:

```text
“The section suitable after a race or event ends.”
```

---

### 4.4 `TrackLoopStart` / `TrackLoopEnd`

Meaning:

```text
Main loop section for normal playback or free roam
```

Validation:

```text
TrackLoopStart < TrackLoopEnd
TrackLoopEnd <= SampleLength
```

If the game needs to loop the current song, it may loop within this region.

---

### 4.5 `PostLoopStart` / `PostLoopEnd`

Meaning:

```text
Loop section for post-race / transition playback
```

Some XML or tool code may call these:

```text
PostRaceLoopStart
PostRaceLoopEnd
```

The tool should support both naming styles if needed.

---

## 5. Most Important Marker Rule: Use Final Prepared WAV Coordinates

This is the most common source of bugs.

The user may import:

```text
MP3
FLAC
96kHz WAV
44.1kHz WAV
Mono WAV
High-sample-rate WAV
```

The tool may finally prepare it as:

```text
48kHz
Stereo
PCM WAV
Loudness-normalized WAV
Trimmed or padded WAV
```

So:

```text
source audio sample count ≠ final prepared WAV sample count
```

Therefore, marker values from the source file must not be blindly written into XML.

Correct logic:

```text
User sets marker in UI
↓
Audio is converted / normalized
↓
Final prepared WAV is created
↓
Tool reads prepared WAV sample_rate and total_samples
↓
Tool converts marker into prepared WAV sample coordinates
↓
Tool writes XML
```

If the UI marker is stored in seconds:

```text
final_marker = round(marker_seconds * final_sample_rate)
```

If the UI marker is stored in source samples:

```text
scale = prepared_total_samples / source_total_samples
final_marker = round(source_marker * scale)
```

In short:

**Every XML field such as SampleLength, End, TrackDrop, LoopStart, and LoopEnd must be based on the final prepared WAV.**

---

## 6. Correct Workflow for Replacing Normal Songs

### 6.1 Required files

```text
1. Target R*_Tracks_*.assets.bank
2. Related RadioInfo_*.xml for the selected language
```

For multi-language display, multiple XML files may need to be updated:

```text
RadioInfo_EN.xml
RadioInfo_CN.xml
RadioInfo_JP.xml
...
```

### 6.2 Recommended workflow

```text
1. Select target language XML
2. Select target station
3. Select target slot
4. Use xml_to_bank_mapping to locate candidate bank and sound_x.wav
5. Prepare replacement music
6. Convert it into the final prepared WAV
   - 48kHz
   - Stereo
   - PCM WAV
   - Loudness-matched
7. Read the true sample count of the prepared WAV
8. Recalculate SampleLength / End / markers
9. Replace the corresponding sound_x.wav in the bank
10. Rebuild the bank
11. Patch RadioInfo XML
12. Validate that XML is still well-formed
13. Test in game
```

### 6.3 Important warnings

```text
Do not globally replace only by sound_name
Do not match only by title
Do not guess only by R number
Do not write SampleLength from the original source audio
Do not update only one XML node while ignoring duplicate aliases
```

The safest matching key is:

```text
xml_file + station + slot_index + sound_name
```

Do not rely only on:

```text
sound_name
```

Because scan results show:

```text
Some sound_names appear in multiple station/context rows
Some repeated sound_names even have different sample_length values
Streamer Mode is a typical example
```

---

## 7. Correct Workflow for Replacing Stingers / Jingles

### 7.1 Target files

```text
R1_Stingers_EN.assets.bank
R2_Stingers_EN.assets.bank
...
R1_Stingers_CN.assets.bank
R2_Stingers_CN.assets.bank
...
```

### 7.2 Usually not required

```text
RadioInfo_*.xml
```

### 7.3 Replacement workflow

```text
1. Find the target station's R*_Stingers_LANGUAGE.assets.bank
2. Extract
3. Listen to sound_0.wav ~ sound_n.wav
4. Identify what each sound is used for
5. Replace the corresponding sound with a new stinger
6. Keep duration close to the original
7. Rebuild the bank
8. Test station switching, gaps between songs, and race-related triggers in game
```

### 7.4 Important warning

```text
Stingers are likely triggered by index.
Do not reorder sound entries.
```

Bad example:

```text
sound_3 was originally a station ID.
You replace it with an outro jingle.
The game will still play sound_3 where it expects a station ID.
The result will feel wrong.
```

---

## 8. Correct Workflow for Replacing VO_DJ

### 8.1 Target files

```text
VO_DJ_01_EN.assets.bank
VO_DJ_02_EN.assets.bank
...
VO_DJ_01_CN.assets.bank
VO_DJ_02_CN.assets.bank
...
```

### 8.2 Usually not required

```text
RadioInfo_*.xml
```

### 8.3 Replacement workflow

```text
1. Find the target language and target DJ bank
2. Extract
3. Listen to and manually label each sound_x.wav
4. Build a mapping table:
   sound index → original line → guessed usage → new line
5. Replace the corresponding sound
6. Keep the same sound count
7. Keep the same order
8. Keep new voice line duration close to the original
9. Rebuild the bank
10. Test in game under different radio states
```

### 8.4 Most important warning

DJ replacement is not “write any line anywhere”.

Since the exact trigger condition of each DJ line is not fully known, the safest strategy is:

```text
Old sound is a short intro → new sound should also be a short intro
Old sound is a between-song transition → new sound should also be a between-song transition
Old sound is 10 seconds → new sound should ideally be 8–12 seconds
Old sound is 30 seconds → new sound can be around 25–35 seconds
```

Avoid:

```text
Replacing a short line with a very long DJ monologue
Replacing with semantically unrelated lines
Reordering sound indexes
Deleting sounds
Adding extra sounds
```

---

## 9. Correct Workflow for Replacing Normal SFX

There are many SFX banks, such as horns, UI, or environmental sounds. They are usually unrelated to normal radio songs.

If you want to replace one:

```text
1. Use fmod_all_bank_audio_inventory.csv to find the target bank
2. Listen and confirm the target sound_x.wav
3. Replace the same index
4. Keep duration, channels, and loudness close
5. Usually do not edit RadioInfo XML
```

Important:

```text
Do not let the tool treat Horn_SFX as a normal song bank.
```

---

## 10. Correct Workflow for Replacing Main Menu Music

Target:

```text
GLB_RadioPressStart.assets.bank
```

Characteristics:

```text
Contains only one music track
Not part of normal station slots
```

Workflow:

```text
1. User selects the replacement main menu music
2. Tool locates GLB_RadioPressStart.assets.bank
3. Extracts the only sound
4. Replaces it with the prepared WAV
5. Rebuilds the bank
6. Installs it or generates a mod package
```

Important:

```text
The user should not need to manually select the bank.
This should not be mixed into the normal RadioInfo XML workflow.
```

---

## 11. Why Streamer Mode Is Special

Scan results indicate:

```text
Streamer Mode is not a simple normal R10 station.
It reuses songs from multiple stations.
It may also involve GLB_Radio_3D.assets.bank.
```

So Streamer Mode should not be understood as:

```text
R10 = R10_Tracks_Disk + R10_Stingers
```

A better understanding is:

```text
Streamer Mode is a special playlist / filtering mode.
It references other stations or global radio content.
```

This helps explain issues such as:

```text
Streamer Mode stops after one song
Streamer Mode behaves differently from normal stations after replacement
```

Future Streamer Mode fixes should focus on:

```text
Streamer Mode XML nodes
Repeated sound_name values
GLB_Radio_3D.assets
SampleLength / End
Playback queue
```

---

## 12. How to Use Developer Mode Output Tables

### 12.1 `all_bank_extract_statistics.csv`

Purpose:

```text
Identify what each bank is
Check whether extraction succeeded
See sound count
See approximate duration range
```

Good first-pass filter.

Common `bank_role` values:

```text
radio_tracks       Normal songs
press_start        Main menu music
glb_radio_3d       Special global radio
dj_or_stinger_hint DJ or stinger candidates
```

---

### 12.2 `fmod_all_bank_audio_inventory.csv`

Purpose:

```text
List every extracted sound_x.wav:
Which bank it is in
Its index
Its duration
Its sample rate
```

This is the most important table for DJ / Stinger / SFX replacement.

---

### 12.3 `target_all_station_soundnames.csv`

Purpose:

```text
Show which song slots exist in RadioInfo XML
```

It represents the XML layer.

Useful for analyzing:

```text
Which stations exist in a language
How many slots each station has
Each slot's sound_name
Each slot's sample_length
```

---

### 12.4 `xml_to_bank_mapping.csv`

Purpose:

```text
Map XML song slots to specific bank/sound candidates
```

This is the most important table for normal song replacement.

Important columns:

```text
candidate_rank
candidate_bank
candidate_sound_file
candidate_frames
length_diff
confidence
candidate_bank_role
```

Safer match conditions:

```text
candidate_bank_role = radio_tracks
length_diff is close to 0
candidate_duration_sec is song-like
candidate_bank is R*_Tracks_*.assets
```

Warning:

```text
text_hint can sometimes match SFX incorrectly.
length_candidate is useful, but bank_role must still be checked.
```

---

### 12.5 `music_bank_inventory.csv`

Purpose:

```text
Quickly list long-audio banks
```

Useful for finding:

```text
Normal song banks
Main menu music bank
Special long-audio banks
```

---

### 12.6 `missing_track_candidate_shortlist.csv`

Purpose:

```text
Analyze hidden slots, aliases, duplicate IDs, and manual-review candidates
```

Useful for explaining:

```text
Why the station dropdown count may not equal the number of actually replaceable slots
```

Some XML rows may be:

```text
Internal aliases
_ID rows
Streamer Mode reused entries
Not normal replaceable songs
```

---

## 13. Rules for “Perfect Replacement”

### Rule 1: Normal songs require both bank and XML changes

Normal song replacement must handle:

```text
Bank audio replacement
XML metadata update
XML SampleLength update
XML marker update
```

---

### Rule 2: All markers must use the final prepared WAV

The reference is:

```text
Not the original MP3
Not the original FLAC
Not an intermediate WAV
But the final prepared WAV written into the bank
```

---

### Rule 3: Keep sound count and order unchanged

Bank index matters:

```text
sound_0
sound_1
sound_2
...
```

Do not delete, add, or reorder sounds.

This is especially important for:

```text
DJ
Stinger
SFX
```

---

### Rule 4: DJ / Stinger replacements should keep similar duration

Because trigger logic is not controlled by normal XML:

```text
You can safely replace the audio itself.
You cannot fully control when it plays yet.
```

So prefer:

```text
Same type
Similar duration
Similar semantic purpose
```

---

### Rule 5: Handle each language separately

RadioInfo is language-specific:

```text
RadioInfo_EN.xml
RadioInfo_CN.xml
RadioInfo_JP.xml
...
```

DJ / Stinger banks are also language-specific:

```text
VO_DJ_01_EN.assets.bank
VO_DJ_01_CN.assets.bank

R1_Stingers_EN.assets.bank
R1_Stingers_CN.assets.bank
```

A multi-language mod cannot only replace EN.

---

### Rule 6: Do not stack multiple radio mods blindly

If another mod already changed RadioInfo XML, it may cause:

```text
Malformed XML
Broken slot mapping
Inconsistent SampleLength
Inconsistent markers
Tool cannot load XML
```

Safest workflow:

```text
Start from original files
Merge all intended changes once
Generate one final package
```

---

## 14. Recommended Tool Development Priorities

### P0: Perfect normal song replacement

Must fix first:

```text
Export marker
Import marker
Prepared WAV coordinate system
SampleLength / End
Loop marker
Waveform player and marker visualization
```

---

### P1: Normal song replacement validator

Before package generation, automatically check:

```text
XML is well-formed
SampleLength equals prepared WAV samples
LoopStart < LoopEnd
LoopEnd <= SampleLength
candidate_bank_role is radio_tracks
No accidental Horn/SFX match
Repeated sound_name nodes are synchronized
```

---

### P2: Stinger replacement mode

Developer feature:

```text
Select R*_Stingers_LANGUAGE.assets.bank
Show all sounds
Preview
Replace
Keep index
Generate package
```

Do not promise trigger timing control.

---

### P3: VO_DJ replacement mode

Developer feature:

```text
Select VO_DJ_*_LANGUAGE.assets.bank
Batch preview / label
Export DJ mapping table
Replace selected sound index
Generate package
```

Do not promise trigger condition control.

---

### P4: Streamer Mode-specific fix

Focus on:

```text
Streamer Mode XML nodes
Repeated sound_name values
GLB_Radio_3D.assets
SampleLength / End
Playback queue
```

Goal:

```text
Fix stopping after one song
Improve replacement queue continuity
```

---

## 15. Final Summary

“Perfect replacement” is not just putting a WAV file into a bank.

The correct model is:

```text
Normal song = Tracks bank audio + RadioInfo XML timing data
DJ = VO_DJ bank audio + game event trigger
Stinger = R*_Stingers bank audio + game event trigger
Main menu music = the single audio inside GLB_RadioPressStart
Streamer Mode = special composite playlist, not a normal R10 station
```

Most important engineering principles:

```text
1. Keep bank sound indexes unchanged
2. Sync XML when replacing normal songs
3. XML SampleLength / End / markers must be based on the final prepared WAV
4. DJ and Stinger audio can be replaced, but trigger timing should not be promised yet
5. Multi-language, multi-station, and Streamer Mode logic cannot rely on simple global sound_name replacement
```

If the tool follows these rules, it can move from “replacement works” toward stable, predictable, near-native radio replacement.
