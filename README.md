# DJ Donut — Discord Multi-Room Music Bot Cluster（多房間音樂機器人）

<div align="center">

![Python](https://img.shields.io/badge/Python-3.10%20%7C%203.11%20%7C%203.12-3776AB?logo=python&logoColor=white)
![Discord.py](https://img.shields.io/badge/Discord.py-v2.7.0%2B-5865F2?logo=discord&logoColor=white)
![Lavalink](https://img.shields.io/badge/Lavalink-v4.0%2B-FF3366?logo=audacity&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Supported-2496ED?logo=docker&logoColor=white)
![i18n](https://img.shields.io/badge/i18n-6%20Languages-4CAF50)
![License](https://img.shields.io/badge/License-MIT-blue.svg)

**A Discord music bot cluster for managing multiple voice rooms.**  
**Run 1 to 5 bots in a single process, with independent playback in up to five voice rooms.**

[Invite Bots](#official-bots--實作機器人邀請) • [Features](#key-features--核心功能) • [Commands](#slash-commands-reference--斜線指令一覽) • [Quick Start](#quick-start--快速開始) • [Audio Settings](#audio-settings--音訊設定) • [Feedback](#support--feedback--問題回饋與優化方案) • [License](#license)

</div>

---

## Official Bots / 實作機器人邀請

可以直接將已架設的 **DJ Donut** 機器人矩陣邀請至你的 Discord 伺服器，立即體驗多房間獨立音樂播放：

| 機器人節點 | 邀請連結 (OAuth2 URL) | 權限設定 |
| :--- | :--- | :--- |
| **DJ Donut 1 號 (主節點)** | [點此邀請 1 號機器人](https://discord.com/oauth2/authorize?client_id=1549028983641743462&permissions=8&integration_type=0&scope=bot) | 管理員權限 (`permissions=8`) |
| **DJ Donut 2 號** | [點此邀請 2 號機器人](https://discord.com/oauth2/authorize?client_id=1549258600155979956&permissions=8&integration_type=0&scope=bot) | 管理員權限 (`permissions=8`) |
| **DJ Donut 3 號** | [點此邀請 3 號機器人](https://discord.com/oauth2/authorize?client_id=1549259098761990184&permissions=8&integration_type=0&scope=bot) | 管理員權限 (`permissions=8`) |
| **DJ Donut 4 號** | [點此邀請 4 號機器人](https://discord.com/oauth2/authorize?client_id=1549259563646062743&permissions=8&integration_type=0&scope=bot) | 管理員權限 (`permissions=8`) |
| **DJ Donut 5 號** | [點此邀請 5 號機器人](https://discord.com/oauth2/authorize?client_id=1549259919696592976&permissions=8&integration_type=0&scope=bot) | 管理員權限 (`permissions=8`) |

> 💡 **多房間分流運作說明**：
> - 只要將多隻機器人邀請至同一伺服器，使用者在不同語音房輸入 `/play` 時，系統會**自動指派空閒的機器人進房**，實現多個房間同時獨立播放音樂！
> - 若伺服器內只有 1 隻機器人，當該機器人正在播放時，其他房間將無法使用，直到播放結束或邀請更多機器人進群。

---

## Key Features / 核心功能

- **Multi-room playback（多房間播放）**:
  - Run up to five Discord music bots in one Python process or Pterodactyl panel.
  - Use `/play <query>` to play music. The dispatcher checks the user’s voice channel and assigns an available bot when needed.
  - Each of the five rooms can have its own playback, queue, volume, and audio effects.

- **Dual-engine architecture（雙引擎架構）**:
  - **Engine A (Lavalink v4 + Wavelink)**: Supports Opus passthrough, JDA-NAS clock scheduling, and real-time 32-bit float DSP equalization.
  - **Engine B (FFmpeg)**: Runs without a separate Lavalink server. Includes an Opus transcoding pipeline, a 16 MB ring buffer, a one-thread CPU limit (`-threads 1`), and headers intended to help with YouTube 403 errors.

- **Localization（多國語言）**:
  - Supports **English (🇺🇸)**, **繁體中文 (🇹🇼)**, **简体中文 (🇨🇳)**, **日本語 (🇯🇵)**, **한국어 (🇰🇷)**, and **Español (🇪🇸)**.
  - Detects each user’s Discord client language through `interaction.locale`.
  - Server administrators can set the default language with `/language`.

- **Autoplay（自動接歌）**:
  - Run `/autoplay` to enable radio mode. When the queue ends, the bot looks for tracks with a similar style.

- **Lyrics（歌詞）**:
  - Run `/lyrics [song]` to look up synchronized or plain lyrics from LRCLIB and Netease Cloud. No API key is required for these lookups.

- **24/7 mode（常駐模式）**:
  - Run `/247` to keep the bot in a voice channel when listeners leave or playback is paused.

- **Spotify and YouTube links（連結解析）**:
  - Paste a Spotify track, playlist, or album link to retrieve its track information and play the results. Availability may depend on the source and its current restrictions.

- **Equalizer and bass boost（等化器與重低音）**:
  - Adjust bass boost with `/bassboost`, from +4 dB to +20 dB.
  - Use `/filter` to select sound presets, including Rock, Pop, Clear Vocal, Crisp Treble, Nightcore, Vaporwave, and 8D.

- **Resource usage（資源使用量）**:
  - The project targets roughly 70–120 MB of RAM for all five bots combined. Actual usage depends on the workload and environment.

---

## Slash Commands Reference / 斜線指令一覽

| Command (指令) | Parameters (參數) | Description (EN) | 說明 (繁體中文) |
| :--- | :--- | :--- | :--- |
| **/play** | `query`, `[bot_id]` | Smart play (Auto-dispatches idle bot or targeted bot 1~5) | 點歌（自動分配可用機器人，也可指定 1～5 號） |
| **/autoplay** | *None* | Toggle smart autoplay radio mode when queue ends | 切換自動接歌模式 (歌單結束自動接續播放) |
| **/lyrics** | `[song]` | View synced or plain lyrics for current / queried song | 查詢當前或指定歌曲的動態/純文字即時歌詞 |
| **/247** | *None* | Toggle 24/7 permanent stay-in-room mode | 切換 24/7 永久常駐語音房模式 |
| **/language** | `language` | Set server default language (en / zh-TW / ja / ko / es) | 設定伺服器預設語言 |
| **/status** | *None* | Live cluster dashboard for all 5 bot nodes | 查看各機器人的連線狀態、所在語音房與分流資訊 |
| **/nowplaying**| *None* | View track details, bitrate, engine & interactive controller | 查看目前歌曲、位元率及互動控制器 |
| **/queue** | *None* | View upcoming queued songs in your voice room | 查看你所在語音房的待播歌單隊列 |
| **/skip** | *None* | Skip currently playing song in your room | 跳過你當前語音房的歌曲 |
| **/stop** | `[force_all]` | Stop playback and leave voice room (or force-stop all bots) | 停止播放並退出語音房（或強制全部退出） |
| **/bassboost** | `level` | Adjust real-time Bass Boost (Off / Low / Medium / High / Extreme) | 調整重低音增強程度 |
| **/filter** | `preset` | Apply audio filters (Rock / Pop / Vocal / Nightcore / 8D / etc.) | 套用音效濾鏡 (搖滾/夜核/8D等) |
| **/bitrate** | `[kbps]` | Maximize voice channel bitrate up to 384kbps | 查看或調整目前語音房的位元率 |
| **/engine** | *None* | View Dual-Engine (Lavalink v4 / Native) real-time diagnostics | 查看雙引擎架構即時運作狀態與音訊規格 |

---

## Quick Start / 快速開始

### Option 1: Run with Python Locally (本機或 VPS 部署)

1. **Clone repository & install dependencies**:
   ```bash
   git clone https://github.com/asian0626/DJ-Donut.git
   cd DJ-Donut
   pip install -r requirements.txt
   ```
   *Make sure `ffmpeg` is installed on your system:*
   - Ubuntu / Debian: `sudo apt update && sudo apt install -y ffmpeg`
   - macOS: `brew install ffmpeg`
   - Windows: Download from [gyan.dev](https://www.gyan.dev/ffmpeg/builds/) and add to PATH.

2. **Configure Environment Variables**:
   Copy `.env.example` to `.env`:
   ```bash
   cp .env.example .env
   ```
   Edit `.env` and fill in your Discord Bot Token(s):
   ```env
   BOT_TOKEN_1=your_first_bot_token_here
   BOT_TOKEN_2=your_second_bot_token_here
   ...
   ```
   *(Configure as many tokens as you want from 1 to 5; the system automatically starts that many bots!)*

3. **Start the Bot Cluster**:
   ```bash
   python3 main.py
   ```

---

### Option 2: Run with Docker / Docker Compose (容器化部署)

1. **Configure `.env`**:
   ```bash
   cp .env.example .env
   # Edit .env with your bot tokens
   ```

2. **Start with Docker Compose**:
   ```bash
   docker compose up -d --build
   ```
   *Logs can be viewed with:*
   ```bash
   docker compose logs -f
   ```

---

### Option 3: Pterodactyl Panel (翼龍面板單一進程部署)

1. Download or git-clone this repository into your Pterodactyl server root folder.
2. Ensure your server image is **Python 3.10 / 3.11 / 3.12** with FFmpeg pre-installed.
3. Configure your tokens in `.env`.
4. Set the startup command to:
   ```bash
   python3 main.py
   ```
   *(or `python3 bot.py`)*
5. Click **Start**! All configured bots will log in simultaneously under that single panel process.

---

## Audio Settings / 音訊設定

If playback sounds muffled or the bass is weak, check these Discord settings:

### 1. Check Discord noise suppression
Discord’s noise suppression is designed for voice chat and may filter parts of music, such as drums, bass, or reverb.
- **Desktop (PC/Mac)**:
  1. Click **User Settings (Gear)** -> **Voice & Video**.
  2. Scroll to **Noise Suppression** -> Select **None** (Turn off Krisp).
  3. Turn off **Echo Cancellation** and **Advanced Voice Activity**.
- **Mobile (iOS/Android)**:
  Inside the voice room, swipe up and turn off **Noise Suppression**.

### 2. Adjust the channel bitrate (`/bitrate`)
Voice-channel bitrate depends on the server and channel settings. Run `/bitrate` if supported, or ask a server administrator to adjust the channel bitrate.

---

## Tech Stack & Architecture

```mermaid
flowchart TD
    User["Discord User"] -->|"/play Query"| Master["Master Dispatcher (Bot 1)"]
    Master --> Route{"Voice Channel Check"}
    Route -->|Bot in Room| Existing["Dispatch to Existing Bot"]
    Route -->|No Bot in Room| Idle["Pick Idle Node (Bot 1~5)"]
    Existing --> Engine
    Idle --> Engine
    
    subgraph "Dual-Engine Audio Pipeline"
        Engine{"Engine Selector"}
        Engine -->|Lavalink v4 Available| LV4["Lavalink v4 Node<br/>(Opus Direct Passthrough + 32-bit DSP)"]
        Engine -->|Native Fallback| FFmpeg["FFmpeg Studio Engine<br/>(384k VBR, Anti-403, 16MB Prebuffer)"]
    end
    
    LV4 --> Voice["Discord Voice Room (Stereo 48kHz)"]
    FFmpeg --> Voice
```

- **Runtime**: Python 3.11+ asyncio event loop
- **Discord API**: `discord.py` 2.7+ with voice and application slash command tree
- **Audio Engines**: Lavalink v4 (Wavelink 3.4+) & FFmpeg Studio Transcoder
- **Extractor**: `yt-dlp` (Opus direct stream selector + Android/Web client bypass headers)
- **Lyrics Engine**: LRCLIB synchronized API + Netease fallback
- **i18n**: Built-in 6-language locale resolver (`en`, `zh-TW`, `zh-CN`, `ja`, `ko`, `es`)

---

## Support & Feedback / 問題回饋與優化方案

若在使用過程中有任何問題、故障回報或效能優化方案，歡迎隨時聯絡：

- **電子郵件 (Email)**: [ian986260903@a1365.pro](mailto:ian986260903@a1365.pro)
- **GitHub Issues**: [提交 Issue / 功能建議](https://github.com/asian0626/DJ-Donut/issues)

---

## License

This project is licensed under the [MIT License](LICENSE). You may use, modify, and redistribute it under the terms of that license.
