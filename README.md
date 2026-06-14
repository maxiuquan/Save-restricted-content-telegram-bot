<div align="center">

# RestrictedContentDL 🌟

**A powerful Telegram bot for downloading and managing restricted content from public and private Telegram channels and groups.**

Built with [Pyrogram](https://github.com/pyrogram/pyrogram) • Async MongoDB (Motor) • Telegram Star Payments

[![Deploy to Heroku](https://www.herokucdn.com/deploy/button.svg)](https://heroku.com/deploy?template=https://github.com/tawhid120/Save-restricted-content-bot-)

</div>

---

## ✨ Features

### 🔗 Auto Link Detection
No commands needed — simply paste any Telegram link and the bot handles the rest automatically:
- **Public links** (`t.me/channel/123`) → downloaded instantly
- **Private links** (`t.me/c/1234567890/123`) → sent to your Saved Messages (requires `/login`)
- **Batch download** → paste a link and the bot asks how many messages to download

### 🎬 YouTube & Multi-Site Downloader
Download videos from YouTube and 1000+ supported websites using the `/ytdl` command, powered by [yt-dlp](https://github.com/yt-dlp/yt-dlp) with [pybalt](https://github.com/nichind/pybalt) as a fallback engine.

**Usage:** `/ytdl <URL>` — e.g., `/ytdl https://www.youtube.com/watch?v=example`

### ⚙️ Advanced Settings Panel
A full interactive settings system (`/settings`) with both toggle and text-input options:
- **Upload Type** — Choose between `DOCUMENT` or `MEDIA` mode
- **Custom Caption** — Template with `{filename}`, `{size}`, `{caption}` placeholders
- **Rename Tag** — Prepend a tag to every downloaded filename
- **Word Delete / Replace** — Auto-filter or substitute words in captions
- **Custom Chat ID** — Forward all downloads to a specific chat or forum topic
- **Spoiler Animation** — Toggle spoiler mode for media
- **Public Channel Clone** — Toggle public channel clone mode

### 💎 Premium Plans with Telegram Star Payments
Three premium tiers purchasable directly with Telegram Stars — activation is instant:

| Feature | Plan 1 (150 ⭐) | Plan 2 (500 ⭐) | Plan 3 (1000 ⭐) |
|---|---|---|---|
| Account Logins | 1 | 5 | 10 |
| Batch Downloads | Up to 1,000 | Up to 2,000 | Unlimited |
| Private Channel/Chat | ✅ | ✅ | ✅ |
| Private Inbox/Bot | ❌ | ✅ | ✅ |
| Validity | 30 days | 30 days | 30 days |

### 🔄 Premium Transfer
Transfer your active premium plan to another user with `/transfer`. The remaining days carry over to the recipient.

### 🔐 Secure Login System
- Phone-number-only login — no API credentials required from users
- All users (free and premium) can log in; free users get 1 account, premium users get plan-based limits
- Two-step verification (2FA) supported
- Sessions are stored securely and can be removed anytime with `/logout`

### 🖼 Custom Thumbnails
Set, view, or remove a custom thumbnail that is applied to all downloaded videos.

### 📊 User Tracking & Logging
- Optional `LOG_GROUP_ID` sends download logs and user activity to a designated Telegram group
- Admin notification on every download with user details

### 🛠 Admin & Developer Tools
- Broadcast messages globally (`/gcast`, `/acast`, `/send`)
- User management (`/stats`, `/users`, `/add`, `/rm`)
- Bot control (`/restart`, `/stop`, `/set`, `/admin`)
- Server diagnostics (`/speedtest`, `/logs`)
- Database utilities (`/migrate`, `/fix_async`, `/fix_status`)

### ⚡ Other Highlights
- **Dynamic Command Prefixes** — Supports `!`, `.`, `#`, `,`, `/` for all commands
- **5-Minute Cooldown** for free users (premium users get unlimited instant access)
- **Interactive Reply Keyboard** — Quick-access buttons for all features
- **Profile Refresh** (`/refresh`) — Sync your latest Telegram profile to the database
- **Multi-Platform Deployment** — Heroku, VPS, or Docker Compose

---

## 📋 Commands

### User Commands

| Command | Description | Access |
|---------|-------------|--------|
| `/start` | Start the bot and see the welcome message | All Users |
| `/help` | View the help menu with all available commands | All Users |
| `/plans` | Browse available premium plans | All Users |
| `/buy` | Purchase a premium plan with Telegram Stars | All Users |
| `/profile` | View your profile and plan status | All Users |
| `/info` | Get detailed account information | All Users |
| `/settings` | Open the interactive settings panel | All Users |
| `/ytdl` | Download videos from YouTube & 1000+ sites | All Users |
| `/login` | Connect your Telegram account for private content | All Users |
| `/logout` | Remove your saved session | All Users |
| `/transfer` | Transfer your premium plan to another user | Premium Users |
| `/setthumb` | Set a custom thumbnail (reply to a photo) | All Users |
| `/getthumb` | View your current custom thumbnail | All Users |
| `/rmthumb` | Remove your custom thumbnail | All Users |
| `/refresh` | Sync your latest Telegram profile to the database | All Users |

### Admin Commands

| Command | Description |
|---------|-------------|
| `/admin` | View the admin command panel |
| `/stats` | Bot statistics (users, premium, downloads, CPU/RAM) |
| `/users` | Paginated list of all users |
| `/add {user} {1\|2\|3}` | Add a user to a premium plan |
| `/rm {user}` | Remove a user from premium |
| `/gcast` | Global broadcast (copy + pin) |
| `/acast` | Global broadcast (forward + pin) |
| `/send` | Send a message to a specific user by ID |
| `/logs` | View or download bot logs |
| `/speedtest` | Run a server speed test |
| `/restart` | Restart the bot |
| `/stop` | Stop the bot |
| `/set` | Set BotFather command list |
| `/migrate` | Migrate database |
| `/fix_async` | Fix async issues |
| `/fix_status` | Check async fix status |

---

## 🚀 Deployment

### 1. Deploy on Heroku (One-Click)

Click the button below to deploy instantly. Configure the environment variables in the Heroku dashboard.

[![Deploy to Heroku](https://www.herokucdn.com/deploy/button.svg)](https://heroku.com/deploy?template=https://github.com/tawhid120/Save-restricted-content-bot-)

#### Heroku CLI Deployment

```bash
# Clone the repository
git clone https://github.com/tawhid120/Save-restricted-content-bot-.git
cd Save-restricted-content-bot-

# Log in to Heroku
heroku login

# Create a Heroku app
heroku create your-app-name

# Set environment variables
heroku config:set API_ID=your_api_id
heroku config:set API_HASH=your_api_hash
heroku config:set BOT_TOKEN=your_bot_token
heroku config:set DEVELOPER_USER_ID=your_user_id
heroku config:set MONGO_URL=your_mongo_url
# DATABASE_URL and DB_URL are optional — they fall back to MONGO_URL
heroku config:set COMMAND_PREFIX="!|.|#|,|/"
heroku config:set LOG_GROUP_ID=your_log_group_id  # optional

# Deploy and scale
git push heroku main
heroku ps:scale worker=1
```

---

### 2. Deploy on VPS

```bash
# Clone the repository
git clone https://github.com/tawhid120/Save-restricted-content-bot-.git
cd Save-restricted-content-bot-

# Install dependencies
pip3 install -r requirements.txt

# Configure environment
cp sample.env .env
nano .env  # Fill in your values

# Run with screen
screen -S restrictedcontentdl
python3 -m bot
# Detach: Ctrl+A then Ctrl+D

# To stop: reattach and Ctrl+C
screen -r restrictedcontentdl
```

---

### 3. Deploy with Docker Compose

```bash
# Clone the repository
git clone https://github.com/tawhid120/Save-restricted-content-bot-.git
cd Save-restricted-content-bot-

# Configure environment
cp sample.env .env
nano .env  # Fill in your values

# Start the bot
docker compose up --build --remove-orphans

# Stop the bot
docker compose down
```

---

## ⚙️ Configuration

Copy `sample.env` to `.env` and fill in the required values:

```env
# Telegram API credentials (required)
API_ID=YOUR_API_ID
API_HASH=YOUR_API_HASH
BOT_TOKEN=YOUR_BOT_TOKEN

# Admin / owner ID (required)
DEVELOPER_USER_ID=YOUR_USER_ID

# User tracking — download logs sent to this group (optional)
# The bot must be an admin in this group with message send permission
# Example: -1001234567890
LOG_GROUP_ID=YOUR_LOG_GROUP_ID

# Database URLs (only MONGO_URL is required; DATABASE_URL and DB_URL fall back to MONGO_URL)
MONGO_URL=YOUR_MONGO_URL
# DATABASE_URL=YOUR_DATABASE_URL
# DB_URL=YOUR_DB_URL

# Command prefixes (required)
COMMAND_PREFIX=!|.|#|,|/
```

| Variable | Required | Description |
|----------|----------|-------------|
| `API_ID` | ✅ | Telegram API ID from [my.telegram.org](https://my.telegram.org) |
| `API_HASH` | ✅ | Telegram API Hash from [my.telegram.org](https://my.telegram.org) |
| `BOT_TOKEN` | ✅ | Bot token from [@BotFather](https://t.me/BotFather) |
| `DEVELOPER_USER_ID` | ✅ | Your Telegram user ID (bot owner/admin) |
| `LOG_GROUP_ID` | ❌ | Group ID for download & user tracking logs (bot must be admin with send permission) |
| `MONGO_URL` | ✅ | MongoDB connection string |
| `DATABASE_URL` | ❌ | Optional: separate MongoDB URL for main DB (falls back to `MONGO_URL`) |
| `DB_URL` | ❌ | Optional: separate MongoDB URL for migration (falls back to `MONGO_URL`) |
| `COMMAND_PREFIX` | ✅ | Pipe-separated command prefixes |

---

## 🧑‍💻 Main Author

**Abir Arafat Chawdhury** — Lead developer of RestrictedContentDL.

- Telegram: [@juktijol](https://t.me/juktijol)
- Channel: [@juktijol](https://t.me/juktijol)

---

## 🙏 Special Thanks

Special thanks to [**@TheSmartBisnu**](https://github.com/bisnuray/RestrictedContentDL) for their contributions to [**RestrictedContentDL**](https://github.com/bisnuray/RestrictedContentDL), particularly for the helper functions in [`utils.py`](https://github.com/bisnuray/RestrictedContentDL/blob/main/helpers/utils.py), which were instrumental in building this project.

---

## 📄 License

This project is licensed under the terms included in the [LICENSE](LICENSE) file.

