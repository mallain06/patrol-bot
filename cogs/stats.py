import datetime

import discord
from discord.ext import commands
from discord import app_commands

import state
from config import (
    TIMEZONE, GUILD_ID, PING_ROLE_ID, MINIMUM_PATROL, STATS_CHANNEL_ID,
    Period, mapLC, mapLS,
)
from database import cursor, get_cutoff, period_label, get_inactive_reason
from helpers import styled_embed, make_bar, admin_check, send_paginated


class StatsCog(commands.Cog):

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="force_stats")
    async def force_stats(self, interaction: discord.Interaction):
        if not admin_check(interaction):
            await interaction.response.send_message("No permission.", ephemeral=True)
            return

        await interaction.response.send_message("Posting stats leaderboard...", ephemeral=True)

        tasks_cog = self.bot.get_cog("TasksCog")
        if tasks_cog:
            await tasks_cog.post_stats_leaderboard()

    @app_commands.command(name="user_stats")
    async def user_stats(self, interaction: discord.Interaction, member: discord.Member, period: Period = Period.all_time):
        if not admin_check(interaction):
            await interaction.response.send_message("No permission.", ephemeral=True)
            return

        cutoff = get_cutoff(period)

        if cutoff:
            cursor.execute(
                "SELECT "
                "COALESCE(SUM(CASE WHEN action = 'patrol_vote' THEN 1 ELSE 0 END), 0), "
                "COALESCE(SUM(CASE WHEN action = 'cant_make' THEN 1 ELSE 0 END), 0), "
                "COALESCE(SUM(CASE WHEN action = 'aop_vote' THEN 1 ELSE 0 END), 0), "
                "COALESCE(SUM(CASE WHEN action = 'patrol_attended' THEN 1 ELSE 0 END), 0), "
                "COALESCE(SUM(CASE WHEN action = 'patrol_skipped' THEN 1 ELSE 0 END), 0) "
                "FROM activity_log WHERE user_id = ? AND day >= ?",
                (member.id, cutoff)
            )
            row = cursor.fetchone()
            p_votes, cant, a_votes, p_attended, p_skip = row
            a_skip = 0
        else:
            cursor.execute(
                "SELECT patrol_votes, patrol_attended, aop_votes, cant_make, patrol_skipped, aop_skipped FROM members WHERE user_id = ?",
                (member.id,)
            )
            row = cursor.fetchone()

            if not row:
                await interaction.response.send_message(f"No data found for {member.display_name}.", ephemeral=True)
                return

            p_votes, p_attended, a_votes, cant, p_skip, a_skip = row

        total_responses = p_attended + cant + p_skip
        attend_rate = (p_attended / total_responses * 100) if total_responses > 0 else 0

        cursor.execute("SELECT day, action FROM activity_log WHERE user_id = ? ORDER BY day DESC LIMIT 1", (member.id,))
        last_row = cursor.fetchone()
        last_activity = f"{last_row[0]} ({last_row[1].replace('_', ' ')})" if last_row else "Never"

        if last_row:
            last_date = datetime.datetime.strptime(last_row[0], "%Y-%m-%d").date()
            days_ago = (datetime.datetime.now(TIMEZONE).date() - last_date).days
            last_activity += f" ({days_ago}d ago)"

        color = member.color if member.color != discord.Color.default() else discord.Color.blue()
        embed = styled_embed(f"\U0001f4cb Stats for {member.display_name}", f"**Period:** {period_label(period)}", color)

        embed.set_thumbnail(url=member.display_avatar.url)

        rate_bar = make_bar(int(attend_rate), 100, 10)

        embed.add_field(name="\U0001f5f3\ufe0f Patrol Votes", value=f"```{p_votes}```", inline=True)
        embed.add_field(name="\u2705 Attended", value=f"```{p_attended}```", inline=True)
        embed.add_field(name="\U0001f4ca Attendance", value=f"```{attend_rate:.0f}%```\n{rate_bar}", inline=True)
        embed.add_field(name="\U0001f4cd AOP Votes", value=f"```{a_votes}```", inline=True)
        embed.add_field(name="\u274c Can't Make It", value=f"```{cant}```", inline=True)
        embed.add_field(name="\u23ed\ufe0f Skipped", value=f"```{p_skip}```", inline=True)
        embed.add_field(
            name="\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501",
            value=f"\U0001f4c5 **Last Activity:** {last_activity}",
            inline=False
        )

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="check_inactive")
    async def check_inactive(self, interaction: discord.Interaction):
        if not admin_check(interaction):
            await interaction.response.send_message("No permission.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        now = datetime.datetime.now(TIMEZONE)
        two_weeks_ago = (now.date() - datetime.timedelta(days=14)).strftime("%Y-%m-%d")

        cursor.execute("SELECT DISTINCT user_id FROM activity_log WHERE day >= ?", (two_weeks_ago,))
        active_users = {r[0] for r in cursor.fetchall()}

        guild = self.bot.get_guild(GUILD_ID)
        ping_role = guild.get_role(PING_ROLE_ID)

        if not ping_role:
            await interaction.followup.send("Ping role not found.", ephemeral=True)
            return

        inactive = [m for m in ping_role.members if not m.bot and m.id not in active_users]

        if not inactive:
            await interaction.followup.send("No inactive members in the last 2 weeks.", ephemeral=True)
            return

        lines = []
        for i, m in enumerate(inactive, 1):
            reason = get_inactive_reason(m.id)
            lines.append(f"**{i}. {m.display_name}** (<@{m.id}>) \u2014 {reason}")

        await send_paginated(self.bot, interaction.channel, f"\u26a0\ufe0f Inactive Members ({len(inactive)} total)", lines, discord.Color.red(), kind="inactivity")
        await interaction.followup.send(f"Found **{len(inactive)}** inactive members.", ephemeral=True)

    @app_commands.command(name="server_stats")
    async def server_stats(self, interaction: discord.Interaction, period: Period = Period.all_time):
        if not admin_check(interaction):
            await interaction.response.send_message("No permission.", ephemeral=True)
            return

        cutoff = get_cutoff(period)

        if cutoff:
            cursor.execute("SELECT day, attendance, cancelled, cant_make FROM patrol_days WHERE day >= ?", (cutoff,))
        else:
            cursor.execute("SELECT day, attendance, cancelled, cant_make FROM patrol_days")
        patrol_rows = cursor.fetchall()

        cursor.execute("SELECT COUNT(*) FROM members")
        total_members = cursor.fetchone()[0]

        if not patrol_rows:
            await interaction.response.send_message("No patrol data for this period.", ephemeral=True)
            return

        day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        day_counts = {d: 0 for d in day_names}
        day_attendance = {d: [] for d in day_names}

        for day_str, attendance, cancelled, cant_make in patrol_rows:
            dt = datetime.datetime.strptime(day_str, "%Y-%m-%d")
            name = day_names[dt.weekday()]
            day_counts[name] += 1
            day_attendance[name].append(attendance)

        active_days = {d: c for d, c in day_counts.items() if c > 0}
        sorted_days = sorted(active_days.items(), key=lambda x: x[1], reverse=True)
        inactive_days = [d for d, c in day_counts.items() if c == 0]

        day_lines = []
        for name, count in sorted_days:
            avg = sum(day_attendance[name]) / len(day_attendance[name])
            bar = make_bar(count, sorted_days[0][1] if sorted_days else 1)
            day_lines.append(f"{bar} **{name}** \u2014 `{count}` patrols, avg `{avg:.1f}` members")

        total_patrols = len(patrol_rows)
        all_attendance = [a for _, a, _, _ in patrol_rows]
        avg_attendance = sum(all_attendance) / total_patrols
        highest = max(all_attendance)
        lowest = min(all_attendance)

        embed = styled_embed("\U0001f4c8 Server Statistics", f"**Period:** {period_label(period)}", discord.Color.teal())

        embed.add_field(
            name="\U0001f4ca Overview",
            value=(
                f"\U0001f693 Total Patrols: `{total_patrols}`\n"
                f"\U0001f465 Tracked Members: `{total_members}`\n"
                f"\U0001f4c8 Avg Attendance: `{avg_attendance:.1f}`\n"
                f"\u2b06\ufe0f Highest: `{highest}` \u00b7 \u2b07\ufe0f Lowest: `{lowest}`"
            ),
            inline=False
        )

        embed.add_field(
            name="\U0001f4c5 Patrols by Day of Week",
            value="\n".join(day_lines) if day_lines else "No data",
            inline=False
        )

        if inactive_days:
            embed.add_field(
                name="\U0001f6ab Days With No Patrols",
                value=", ".join(inactive_days),
                inline=False
            )

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="activity_stats")
    async def activity_stats(self, interaction: discord.Interaction, period: Period = Period.all_time):
        if not admin_check(interaction):
            await interaction.response.send_message("No permission.", ephemeral=True)
            return

        cutoff = get_cutoff(period)

        if cutoff:
            cursor.execute("SELECT day, attendance, cancelled, cant_make FROM patrol_days WHERE day >= ?", (cutoff,))
        else:
            cursor.execute("SELECT day, attendance, cancelled, cant_make FROM patrol_days")
        patrol_rows = cursor.fetchall()

        if not patrol_rows:
            await interaction.response.send_message("No patrol data for this period.", ephemeral=True)
            return

        day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        day_counts = {d: 0 for d in day_names}
        day_attendance = {d: [] for d in day_names}
        day_cancelled = {d: 0 for d in day_names}
        day_cant_make = {d: [] for d in day_names}

        for day_str, attendance, cancelled, cant_make in patrol_rows:
            dt = datetime.datetime.strptime(day_str, "%Y-%m-%d")
            name = day_names[dt.weekday()]
            day_counts[name] += 1
            day_attendance[name].append(attendance)
            if cancelled:
                day_cancelled[name] += 1
            day_cant_make[name].append(cant_make or 0)

        active_days = {d: c for d, c in day_counts.items() if c > 0}
        sorted_days = sorted(active_days.items(), key=lambda x: x[1], reverse=True)

        total_patrols = len(patrol_rows)
        total_cancelled = sum(1 for _, _, c, _ in patrol_rows if c)
        total_active = total_patrols - total_cancelled
        total_cant_make = sum(cm or 0 for _, _, _, cm in patrol_rows)
        total_responses = sum(a + (cm or 0) for _, a, _, cm in patrol_rows)
        noshow_pct = (total_cant_make / total_responses * 100) if total_responses > 0 else 0

        cancel_pct = (total_cancelled / total_patrols * 100) if total_patrols > 0 else 0
        cancel_bar = make_bar(int(cancel_pct), 100, 10)
        noshow_bar = make_bar(int(noshow_pct), 100, 10)

        embed = styled_embed("\U0001f4c9 Activity & No-Show Stats", f"**Period:** {period_label(period)}", discord.Color.orange())

        embed.add_field(
            name="\U0001f4ca Overview",
            value=(
                f"\U0001f693 Total Patrols: `{total_patrols}` (`{total_active}` active, `{total_cancelled}` cancelled)\n\n"
                f"\u274c **Cancellation Rate:** `{cancel_pct:.0f}%`\n{cancel_bar}\n\n"
                f"\U0001f47b **No-Show Rate:** `{noshow_pct:.0f}%` (`{total_cant_make}` / `{total_responses}` responses)\n{noshow_bar}"
            ),
            inline=False
        )

        day_lines = []
        for name, count in sorted_days:
            total_cm = sum(day_cant_make[name])
            total_resp = sum(day_attendance[name]) + total_cm
            rate = (total_cm / total_resp * 100) if total_resp > 0 else 0
            cancel_rate = (day_cancelled[name] / count * 100) if count > 0 else 0
            c_bar = make_bar(int(cancel_rate), 100, 6)
            day_lines.append(f"**{name}**\n{c_bar} `{cancel_rate:.0f}%` cancelled \u00b7 `{rate:.0f}%` no-show")

        embed.add_field(
            name="\U0001f4c5 Breakdown by Day of Week",
            value="\n".join(day_lines) if day_lines else "No data",
            inline=False
        )

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="aop_breakdown")
    async def aop_breakdown(self, interaction: discord.Interaction, period: Period = Period.all_time):
        if not admin_check(interaction):
            await interaction.response.send_message("No permission.", ephemeral=True)
            return

        cutoff = get_cutoff(period)

        if cutoff:
            cursor.execute("SELECT area, day FROM aop_stats WHERE day >= ?", (cutoff,))
        else:
            cursor.execute("SELECT area, day FROM aop_stats")
        aop_rows = cursor.fetchall()

        if not aop_rows:
            await interaction.response.send_message("No AOP data for this period.", ephemeral=True)
            return

        lc_set = set(mapLC)
        ls_set = set(mapLS)

        lc_rows = [(a, d) for a, d in aop_rows if a in lc_set]
        ls_rows = [(a, d) for a, d in aop_rows if a in ls_set]

        embeds = []

        for map_name, map_rows, map_areas in [
            ("Liberty City", lc_rows, mapLC),
            ("Los Santos", ls_rows, mapLS),
        ]:
            if not map_rows:
                continue

            day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

            aop_counts = {}
            for area, _ in map_rows:
                aop_counts[area] = aop_counts.get(area, 0) + 1

            total = len(map_rows)
            sorted_aops = sorted(aop_counts.items(), key=lambda x: x[1], reverse=True)
            top_count = sorted_aops[0][1] if sorted_aops else 1

            aop_lines = []
            for i, (area, count) in enumerate(sorted_aops):
                pct = count / total * 100
                bar = make_bar(count, top_count)
                medal = ["\U0001f947", "\U0001f948", "\U0001f949"][i] if i < 3 else "\U0001f4cc"
                aop_lines.append(f"{medal} **{area}**\n{bar} `{count}` times ({pct:.0f}%)")

            unused = [a for a in map_areas if a not in aop_counts]

            day_aop_data = {}
            for area, day_str in map_rows:
                if not day_str:
                    continue
                dt = datetime.datetime.strptime(day_str, "%Y-%m-%d")
                dow = day_names[dt.weekday()]
                if dow not in day_aop_data:
                    day_aop_data[dow] = {}
                day_aop_data[dow][area] = day_aop_data[dow].get(area, 0) + 1

            aop_day_lines = []
            for day in day_names:
                if day not in day_aop_data:
                    continue
                areas = day_aop_data[day]
                top_area = max(areas, key=areas.get)
                aop_day_lines.append(f"\U0001f4c5 **{day}** \u2192 **{top_area}** (`{areas[top_area]}` times)")

            embed = styled_embed(
                f"\U0001f5fa\ufe0f AOP Breakdown \u2014 {map_name}",
                f"**Period:** {period_label(period)}\n**Total Patrols:** `{total}`",
                discord.Color.purple()
            )

            embed.add_field(
                name="\U0001f3c6 Area Popularity",
                value="\n\n".join(aop_lines),
                inline=False
            )

            if unused:
                embed.add_field(
                    name="\U0001f6ab Never Selected",
                    value=", ".join(unused),
                    inline=False
                )

            embed.add_field(
                name="\U0001f4c5 Most Popular AOP per Day",
                value="\n".join(aop_day_lines) if aop_day_lines else "No data",
                inline=False
            )

            embeds.append(embed)

        await interaction.response.send_message(embeds=embeds)


async def setup(bot):
    await bot.add_cog(StatsCog(bot))
