# Patrol Bot

A Discord bot for managing daily patrol scheduling, AOP (Area of Patrol) voting, attendance tracking, and statistics for roleplay communities.

## Features

- **Daily Patrol Voting** — Members vote for a patrol start time each morning. Votes close at 6:30 PM EST with a minimum attendance threshold.
- **AOP Voting** — Members vote on the patrol area. Supports LC and LS map sets, switchable by admins.
- **Briefing Reminder** — Automatically pings 10 minutes before the confirmed patrol start time to join the briefing voice chat.
- **Biweekly Stats Leaderboard** — Posts a ranked leaderboard to the stats channel every 14 days.
- **Admin Commands** — Cancel patrols, override start times/AOP, view per-user and server-wide statistics.

## Commands

| Command | Description |
|---|---|
| `/cancel_patrol` | Cancel tonight's patrol |
| `/override_patrol_time <time>` | Override the patrol start time |
| `/override_aop <area>` | Override the AOP |
| `/maplc` | Switch AOP options to LC map |
| `/mapls` | Switch AOP options to LS map |
| `/user_stats <member>` | View a specific member's statistics |
| `/server_stats` | View server-wide patrol statistics |
| `/force_stats` | Manually post the biweekly stats leaderboard |

All commands require the admin role and must be used in the admin command channel.

## Environment Variables

| Variable | Description |
|---|---|
| `TOKEN` | Discord bot token |
| `GUILD_ID` | Discord server ID |
| `PATROL_CHANNEL_ID` | Channel for patrol voting and announcements |
| `AOP_CHANNEL_ID` | Channel for AOP voting and overrides |
| `BRIEFING_CHANNEL_ID` | Channel for briefing reminders |
| `STATS_CHANNEL_ID` | Channel for biweekly stats leaderboard |
| `ADMIN_COMMAND_CHANNEL` | Channel where admin commands are allowed |
| `PING_ROLE_ID` | Role to ping for voting and briefing reminders |
| `ADMIN_ROLE_ID` | Role required to use admin commands |
| `DATABASE_PATH` | Path to SQLite database (default: `patrol_stats.db`) |

## Docker

### Run with Docker

```bash
docker run -d \
  -e TOKEN=your-bot-token \
  -e GUILD_ID=123456789 \
  -e PATROL_CHANNEL_ID=123456789 \
  -e AOP_CHANNEL_ID=123456789 \
  -e BRIEFING_CHANNEL_ID=123456789 \
  -e STATS_CHANNEL_ID=123456789 \
  -e ADMIN_COMMAND_CHANNEL=123456789 \
  -e PING_ROLE_ID=123456789 \
  -e ADMIN_ROLE_ID=123456789 \
  -v patrol-data:/app/data \
  ghcr.io/alexb715/patrol-bot:latest
```

### Build locally

```bash
docker build -t patrol-bot .
```

## Running without Docker

```bash
pip install -r requirements.txt
python bot.py
```
