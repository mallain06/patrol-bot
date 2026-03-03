# Patrol Bot

A Discord bot for managing daily patrol scheduling, AOP (Area of Patrol) voting, attendance tracking, and statistics for roleplay communities.

## Features

- **Daily Patrol Voting** — Members vote for a patrol start time each morning. Votes close at 6:30 PM EST with a minimum attendance threshold.
- **AOP Voting** — Members vote on the patrol area. Supports LC and LS map sets, switchable by admins.
- **Persistent Session State** — Votes, messages, and voting status survive bot restarts via SQLite persistence.
- **Briefing Reminder** — Automatically pings 10 minutes before the confirmed patrol start time to join the briefing voice chat.
- **Cancel Announcements** — Cancelling a patrol notifies the announcement channel and edits any existing announcement.
- **Smart Map Switching** — Changing the map while AOP voting is open resets votes and refreshes buttons automatically.
- **Duplicate Vote Protection** — Changing your vote doesn't inflate stat counters; only the first vote is tracked.
- **Command Autocomplete** — Time slots and area names are suggested as you type in slash commands.
- **Biweekly Stats Leaderboard** — Posts a ranked leaderboard to the stats channel every 14 days.
- **Admin Commands** — Cancel patrols, override start times/AOP, view per-user and server-wide statistics.

## Commands

| Command | Description |
|---|---|
| `/open_votes` | Open both patrol and AOP voting |
| `/open_patrol_vote` | Open patrol voting only |
| `/open_aop_vote` | Open AOP voting only |
| `/close_patrol_votes` | Close patrol votes and determine start time |
| `/close_aop_votes` | Close AOP votes and announce the winner |
| `/start_patrol <time> <area>` | Force start a patrol with a specific time and area |
| `/cancel_patrol` | Cancel tonight's patrol and notify announcement channel |
| `/override_patrol_time <time>` | Override the patrol start time |
| `/override_aop <area>` | Override the AOP |
| `/maplc` | Switch AOP options to LC map (resets AOP votes if open) |
| `/mapls` | Switch AOP options to LS map (resets AOP votes if open) |
| `/user_stats <member>` | View a specific member's statistics |
| `/server_stats` | View server-wide patrol statistics |
| `/activity_stats` | View cancellation and no-show statistics |
| `/aop_breakdown` | View AOP popularity breakdown |
| `/check_inactive` | List inactive members from the last 2 weeks |
| `/force_stats` | Manually post the biweekly stats leaderboard |

All commands require the admin role and must be used in the admin command channel.

## Environment Variables

| Variable | Description |
|---|---|
| `TOKEN` | Discord bot token |
| `GUILD_ID` | Discord server ID |
| `PATROL_CHANNEL_ID` | Channel for patrol voting and announcements |
| `AOP_CHANNEL_ID` | Channel for AOP voting and overrides |
| `BRIEFING_CHANNEL_ID` | Text channel for briefing reminders |
| `BRIEFING_VOICE_CHANNEL_ID` | Voice channel linked in briefing reminders |
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
  -e BRIEFING_VOICE_CHANNEL_ID=123456789 \
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
