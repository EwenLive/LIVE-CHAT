# 📺 Discord Stream Overlay Bot

An interactive alert system for streamers that displays videos, GIFs, and images sent from Discord directly onto your screen.

---

## ✨ Features

* **Multi-Platform Support**: Works with YouTube, TikTok, Twitter (X), Instagram, GIFs, and direct files like MP4, MOV, and JPG.
* **Transparent Overlay**: A frameless window that stays on top of your games or software.
* **Control Panel**: A dedicated mini-window accessible via the system tray to manage the bot.
* **Real-time Interaction**: Displays the Discord username and a custom message under the media.
* **Customizable Settings**: Adjust volume, toggle between 720p/1080p quality, and pause/resume the bot anytime.

---

## 🚀 Quick Start

### 1. Server Setup (Host)
The server handles the Discord commands and broadcasts media to your client.
1.  **Requirements**: Ensure you have [Docker](https://www.docker.com/) installed.
2.  **Configuration**: Create a `.env` file in the root folder and add your bot token:
    ```env
    DISCORD_TOKEN=your_discord_bot_token_here
    ```
3.  **Deployment**: Open your terminal and run the following command to build and start the server and the Ngrok tunnel:
    ```bash
    docker-compose up -d --build
    ```

### 2. Client Setup (Streamer)
No coding or Python installation is required for the streamer.
1.  **Download**: Get the `client.exe` file from this repository.
2.  **Launch**: Simply run `client.exe`.
3.  **Manage**: A small icon will appear in your **System Tray** (bottom right of your taskbar). Click it to open the control panel.

---

## 🎮 How to Use

Once the server is up and your client is running, use the following command in your Discord server:

**`!pop <link_or_attachment> [your message]`**

* **Example with Link**: `!pop https://www.youtube.com/watch?v=dQw4w9WgXcQ Wow check this out!`
* **Example with File**: Upload an image or video directly to Discord and type `!pop` in the caption.
* **Manual Close**: A red "✕" button appears on the overlay to close the current alert manually.

---

## ⚙️ Settings & Controls

By clicking the tray icon, you can access the **Control Window**:
* **Bot Status**: Toggle "Bot Active" or "Bot Paused" to stop receiving new alerts.
* **Resolution**: Switch between **720p** and **1080p**. The overlay will automatically resize.
* **Volume**: Control the audio level of video alerts in real-time.

---

## 🛠️ Built With

* **Backend**: FastAPI & Discord.py.
* **Frontend**: PyQt6 for the overlay and UI.
* **Media**: yt-dlp for video extraction and WebSockets for communication.
* **Infrastructure**: Docker & Ngrok.
