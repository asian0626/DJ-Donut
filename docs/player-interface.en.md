# DJ Donut 2.1.0 Player Interface

[English](player-interface.en.md) · [繁體中文](player-interface.md) · [README](../README.md) · [v2.1.0 release notes](releases/v2.1.0.md)

## Card layout

Playing a song or running `/nowplaying` sends a Discord Components V2 player card:

- The top shows playback or pause status, the song link, artist, and artwork.
- The middle shows elapsed time, total duration, and a progress bar; live streams show LIVE.
- The five main buttons are shuffle queue, replay current song, pause/resume, skip, and favorite, in that order.
- The menu contains volume, repeat mode, rewind 15 seconds, forward 15 seconds, queue, autoplay, EQ, speed, Sing Along, lyrics, and stop, in that order.
- The bottom shows the requester, voice channel, and number of upcoming songs.

The progress display updates periodically during playback. Pausing, resuming, and seeking update the card. Replay restarts the current song.

## Control menu and playback speed

Volume, repeat, EQ, and speed show their current settings in the menu. Volume accepts whole numbers from 0 to 200, with 100 representing normal volume. Repeat cycles through off, current song, and the entire queue. Rewind and forward move the current song's playback position; live streams cannot seek.

Autoplay recommends similar songs when the queue ends. EQ offers bass boost, Nightcore, 8D, and other effects. Speed changes playback tempo while preserving the original pitch. Nightcore and Vaporwave have their own pitch changes as separate audio effects.

The seven speed choices are 0.5×, 0.75×, 1.0×, 1.25×, 1.5×, 1.75×, and 2.0×. Speed and Sing Along apply to the current room session. Stopping and leaving, resetting the room, or restarting the bot restores 1.0× speed and turns Sing Along off.

Sing Along shows synchronized lyric excerpts that follow playback on the card. The song must have synchronized lyrics available. The separate Lyrics option shows the full lyrics. Stop clears the current song and queue, then disconnects the bot from the voice channel. These controls follow the project's DJ permission settings and require no votes, subscriptions, or time-limited access.

## Voice channel song status

During playback, the voice channel status automatically shows `🎵 Song title`. While paused, it shows `⏸️ Song title`. Track changes update the status. Playback completion, stopping, leaving, and normal bot shutdown clear it. Moving rooms clears the old room and updates the new one. Long titles are truncated to Discord's 500-character status limit; the channel name is unchanged.

Grant the bot **Set Voice Channel Status** permission. Clearing the status after leaving may also require **Manage Channels**. Missing permissions or temporary Discord API failures are logged and audio continues. This feature applies to standard voice channels; Stage channels are skipped.

## Discord appearance limits

Discord controls the card's background, fonts, button styling, and mobile layout. The viewer's theme affects its appearance. The bot cannot set the screenshot's overall gradient or green pill badge, so playback status uses a green dot. The progress bar is for display: Discord bot messages do not support a draggable slider. Seek with the menu's rewind/forward options or `/seek`.

## Upgrade to 2.1.0

1. Stop the bot normally and retain your existing `.env`, `data/`, and custom settings.
2. Update the working directory to the 2.1.0 code. Python deployments use the existing `requirements.txt` and startup method. Docker deployments rebuild and start with `docker compose up -d --build`.
3. Check the bot's **Set Voice Channel Status** permission in each voice channel. You can additionally grant **Manage Channels** for status cleanup after leaving. Audio playback still works without status permission.
4. For Python deployments, restart with `python3 main.py`. Play a song or run `/nowplaying` to create the new card, then check its control menu and the voice channel status.

No new environment variables, dependencies, or manual database migration are required. The existing requirements already include discord.py 2.7 or later. Existing slash commands remain available.

## 2.1.0 reliability and runtime

The player serializes each room's playback. Shared controls require the same voice room and the existing DJ policy. Stop cancels pending playback work; old callbacks cannot advance a new song.

During SDK reconnects, the room retains its song and queue for up to 35 seconds while waiting for the existing connection. Recovery restores the saved position and pause state. Explicit stops, confirmed kicks, or exhausted recovery clear the room. Network outages can still interrupt audio.

Spotify resolution has a 25-second overall budget, failed resources are cached for 120 seconds, and collection imports follow pages up to 200 tracks. Private, personalized, or API-restricted playlists may remain unavailable; the bot will not invent a track from the collection title.

Use `./start.sh --check` to inspect Python and package versions. Python 3.10–3.14 is accepted; a new environment prefers 3.12. Existing unsupported environments are preserved and rejected with instructions. Install the updated `requirements.txt` before restarting. The compatibility patch targets discord.py 2.7.x.
