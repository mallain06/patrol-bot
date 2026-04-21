import datetime
import random

import discord
from discord.ext import commands, tasks

import state
from config import (
    TIMEZONE, PATROL_CHANNEL_ID, AOP_CHANNEL_ID, ANNOUNCEMENT_CHANNEL_ID,
    BRIEFING_CHANNEL_ID, BRIEFING_VOICE_CHANNEL_ID, STATS_CHANNEL_ID,
    PING_ROLE_ID, GUILD_ID, MINIMUM_PATROL,
    time_slots, mapLC, mapLS,
)
from database import cursor, conn, record_stat, log_activity, get_inactive_reason, patrol_day_exists, aop_stat_exists
from helpers import (
    styled_embed, build_patrol_embed, build_aop_embed, lock_voting,
    make_bar, send_paginated,
)
from views import PatrolView, AOPView


class TasksCog(commands.Cog):

    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.scheduler.start()
        self.close_votes.start()
        self.briefing_reminder.start()
        self.stats_checker.start()
        self.inactivity_checker.start()

    async def cog_unload(self):
        self.scheduler.cancel()
        self.close_votes.cancel()
        self.briefing_reminder.cancel()
        self.stats_checker.cancel()
        self.inactivity_checker.cancel()

    @tasks.loop(minutes=1)
    async def scheduler(self):
        now = datetime.datetime.now(TIMEZONE)

        if now.hour == 8 and now.minute == 0:
            state.patrol_votes.clear()
            state.cant_make_votes.clear()
            state.aop_votes.clear()
            state.confirmed_start_time = None
            state.patrol_embed_title = "\U0001f693 Patrol Attendance"
            state.aop_embed_title = "\U0001f5fa\ufe0f AOP Voting"
            state.voting_open = True

            patrol_channel = self.bot.get_channel(PATROL_CHANNEL_ID)
            aop_channel = self.bot.get_channel(AOP_CHANNEL_ID)

            role = f"<@&{PING_ROLE_ID}>"

            state.patrol_message = await patrol_channel.send(
                role, embed=build_patrol_embed(state.patrol_embed_title), view=PatrolView()
            )
            state.aop_message = await aop_channel.send(
                role, embed=build_aop_embed(state.aop_embed_title), view=AOPView()
            )
            state.save_session()

    @tasks.loop(minutes=1)
    async def close_votes(self):
        now = datetime.datetime.now(TIMEZONE)

        if now.hour == 18 and now.minute == 30:
            await lock_voting()

            patrol_channel = self.bot.get_channel(PATROL_CHANNEL_ID)
            announcement_channel = self.bot.get_channel(ANNOUNCEMENT_CHANNEL_ID)

            attendance_counts = {time: 0 for time in time_slots}
            for vote in state.patrol_votes.values():
                attendance_counts[vote] += 1

            cumulative = 0
            start_time = None

            for time in time_slots:
                cumulative += attendance_counts[time]
                if cumulative >= MINIMUM_PATROL:
                    start_time = time
                    break

            if not start_time:
                today = now.strftime("%Y-%m-%d")
                if patrol_day_exists(today):
                    return
                cursor.execute(
                    "INSERT INTO patrol_days(day, attendance, cancelled, cant_make) VALUES(?, ?, 1, ?)",
                    (today, len(state.patrol_votes), len(state.cant_make_votes))
                )
                conn.commit()

                embed = styled_embed(
                    "\u274c Patrol Cancelled",
                    f"Minimum attendance not reached.\n\n"
                    f"\U0001f465 **Votes:** `{len(state.patrol_votes)}` / `{MINIMUM_PATROL}` minimum\n"
                    f"\u274c **Can't Make It:** `{len(state.cant_make_votes)}`",
                    discord.Color.red()
                )
                await patrol_channel.send(embed=embed)

                aop_channel = self.bot.get_channel(AOP_CHANNEL_ID)
                aop_embed = styled_embed(
                    "\u274c AOP Cancelled",
                    "Patrol was cancelled \u2014 no AOP tonight.",
                    discord.Color.red()
                )
                await aop_channel.send(embed=aop_embed)

                if announcement_channel:
                    announce = styled_embed(
                        "\u274c Tonight's Patrol Has Been Cancelled",
                        f"Not enough members signed up for tonight's patrol.\n\n"
                        f"\U0001f465 **Signed Up:** `{len(state.patrol_votes)}` / `{MINIMUM_PATROL}` minimum\n"
                        f"\u274c **Can't Make It:** `{len(state.cant_make_votes)}`\n\n"
                        f"Better luck next time!",
                        discord.Color.red()
                    )
                    role = f"<@&{PING_ROLE_ID}>"
                    await announcement_channel.send(role, embed=announce)
                return

            if not state.aop_votes:
                options = mapLC if state.current_map == "LC" else mapLS
                selected_aop = random.choice(options)
            else:
                counts = {}
                for vote in state.aop_votes.values():
                    counts[vote] = counts.get(vote, 0) + 1
                selected_aop = max(counts, key=counts.get)

            today = now.strftime("%Y-%m-%d")
            if patrol_day_exists(today):
                return
            cursor.execute(
                "INSERT INTO patrol_days(day, attendance, cancelled, cant_make) VALUES(?, ?, 0, ?)",
                (today, len(state.patrol_votes), len(state.cant_make_votes))
            )
            if not aop_stat_exists(today):
                cursor.execute("INSERT INTO aop_stats(area, day) VALUES(?, ?)", (selected_aop, today))

            for user_id in state.patrol_votes:
                record_stat(user_id, "patrol_attended")
                log_activity(user_id, "patrol_attended")

            for user_id in state.cant_make_votes:
                record_stat(user_id, "patrol_skipped")
                log_activity(user_id, "patrol_skipped")

            conn.commit()

            state.confirmed_start_time = start_time

            embed = styled_embed("\u2705 Patrol Confirmed", color=discord.Color.green())
            embed.add_field(name="\U0001f550 Start Time", value=f"```{start_time}```", inline=True)
            embed.add_field(name="\U0001f465 Attending", value=f"```{len(state.patrol_votes)}```", inline=True)

            await patrol_channel.send(embed=embed)

            aop_channel = self.bot.get_channel(AOP_CHANNEL_ID)
            aop_embed = styled_embed("\u2705 AOP Confirmed", color=discord.Color.purple())
            aop_embed.add_field(name="\U0001f4cd Area", value=f"```{selected_aop}```", inline=True)
            aop_embed.add_field(name="\U0001f5f3\ufe0f Votes", value=f"```{len(state.aop_votes)}```", inline=True)
            await aop_channel.send(embed=aop_embed)

            if announcement_channel:
                announce = styled_embed(
                    "\U0001f693 Tonight's Patrol is Confirmed!",
                    f"Patrol is happening tonight! Here are the details:\n\n"
                    f"\U0001f550 **Start Time:** {start_time}\n"
                    f"\U0001f4cd **AOP:** {selected_aop}\n"
                    f"\U0001f465 **Members Attending:** {len(state.patrol_votes)}\n\n"
                    f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
                    f"\U0001f4cb Briefing starts **10 minutes** before in <#{BRIEFING_VOICE_CHANNEL_ID}>",
                    discord.Color.green()
                )
                role = f"<@&{PING_ROLE_ID}>"
                state.announcement_message = await announcement_channel.send(role, embed=announce)

            state.save_session()

    @tasks.loop(minutes=1)
    async def briefing_reminder(self):
        if not state.confirmed_start_time:
            return

        now = datetime.datetime.now(TIMEZONE)

        time_str = state.confirmed_start_time.replace(" EST", "")
        start_dt = datetime.datetime.strptime(time_str, "%I:%M %p")
        start_dt = now.replace(hour=start_dt.hour, minute=start_dt.minute, second=0, microsecond=0)

        briefing_dt = start_dt - datetime.timedelta(minutes=10)

        if now.hour == briefing_dt.hour and now.minute == briefing_dt.minute:
            role = f"<@&{PING_ROLE_ID}>"
            channel = self.bot.get_channel(BRIEFING_CHANNEL_ID)

            embed = styled_embed(
                "\U0001f4cb Briefing Reminder",
                f"\u23f0 Patrol starts in **10 minutes** at **{state.confirmed_start_time}**\n\n"
                f"\U0001f50a **Join the briefing:** <#{BRIEFING_VOICE_CHANNEL_ID}>\n\n"
                f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
                f"Make sure you're ready and in the voice channel!",
                discord.Color.orange()
            )

            await channel.send(role, embed=embed)
            state.confirmed_start_time = None
            state.save_session()

    @tasks.loop(minutes=1)
    async def stats_checker(self):
        now = datetime.datetime.now(TIMEZONE)

        if now.hour != 12 or now.minute != 0:
            return

        cursor.execute("SELECT value FROM settings WHERE key = 'last_stats_post'")
        row = cursor.fetchone()

        if row:
            last_date = datetime.datetime.strptime(row[0], "%Y-%m-%d").date()
            if (now.date() - last_date).days < 14:
                return

        await self.post_stats_leaderboard()

    @tasks.loop(minutes=1)
    async def inactivity_checker(self):
        now = datetime.datetime.now(TIMEZONE)

        if now.hour != 12 or now.minute != 0:
            return

        cursor.execute("SELECT value FROM settings WHERE key = 'last_inactivity_post'")
        row = cursor.fetchone()

        if row:
            last_date = datetime.datetime.strptime(row[0], "%Y-%m-%d").date()
            if (now.date() - last_date).days < 14:
                return

        two_weeks_ago = (now.date() - datetime.timedelta(days=14)).strftime("%Y-%m-%d")

        cursor.execute("SELECT DISTINCT user_id FROM activity_log WHERE day >= ?", (two_weeks_ago,))
        active_users = {r[0] for r in cursor.fetchall()}

        guild = self.bot.get_guild(GUILD_ID)
        ping_role = guild.get_role(PING_ROLE_ID)

        if not ping_role:
            return

        inactive = [m for m in ping_role.members if not m.bot and m.id not in active_users]

        if not inactive:
            return

        lines = [
            f"**{i}. {m.display_name}** (<@{m.id}>) \u2014 {get_inactive_reason(m.id)}"
            for i, m in enumerate(inactive, 1)
        ]

        channel = self.bot.get_channel(STATS_CHANNEL_ID)

        today = now.strftime("%Y-%m-%d")
        cursor.execute("INSERT OR REPLACE INTO settings(key, value) VALUES('last_inactivity_post', ?)", (today,))
        conn.commit()

        await send_paginated(self.bot, channel, "\u26a0\ufe0f Inactive Members (Last 2 Weeks)", lines, discord.Color.red(), kind="inactivity")

    async def post_stats_leaderboard(self):
        cursor.execute(
            "SELECT user_id, patrol_votes, patrol_attended, aop_votes, cant_make, patrol_skipped, aop_skipped "
            "FROM members ORDER BY patrol_attended DESC"
        )
        rows = cursor.fetchall()

        if not rows:
            return

        guild = self.bot.get_guild(GUILD_ID)
        channel = self.bot.get_channel(STATS_CHANNEL_ID)

        lines = []
        for i, (user_id, p_votes, p_attended, a_votes, cant, p_skip, a_skip) in enumerate(rows, 1):
            member = guild.get_member(user_id)
            name = member.display_name if member else f"Unknown ({user_id})"

            lines.append(
                f"**{i}. {name}**\n"
                f"Patrol Votes: {p_votes} | Attended: {p_attended} | "
                f"AOP Votes: {a_votes} | Can't Make It: {cant} | "
                f"Patrol Skipped: {p_skip} | AOP Skipped: {a_skip}"
            )

        today = datetime.datetime.now(TIMEZONE).strftime("%Y-%m-%d")
        cursor.execute("INSERT OR REPLACE INTO settings(key, value) VALUES('last_stats_post', ?)", (today,))
        conn.commit()

        await send_paginated(self.bot, channel, "\U0001f4ca Biweekly Stats Leaderboard", lines, discord.Color.blue(), kind="stats")


async def setup(bot):
    await bot.add_cog(TasksCog(bot))
