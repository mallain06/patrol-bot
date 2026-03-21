import random

import discord
from discord.ext import commands
from discord import app_commands

import state
from config import (
    TIMEZONE, PATROL_CHANNEL_ID, AOP_CHANNEL_ID, BRIEFING_CHANNEL_ID,
    BRIEFING_VOICE_CHANNEL_ID, PING_ROLE_ID, GUILD_ID, MINIMUM_PATROL,
    time_slots, mapLC, mapLS,
)
from database import cursor, conn, ensure_member, get_inactive_reason, patrol_day_exists
from helpers import (
    styled_embed, build_patrol_embed, build_aop_embed,
    admin_check, time_autocomplete, current_map_area_autocomplete,
    send_paginated,
)
from views import PatrolView, AOPView

import datetime


class TestingCog(commands.Cog):

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="test_patrol_vote")
    async def test_patrol_vote(self, interaction: discord.Interaction):
        if not admin_check(interaction):
            await interaction.response.send_message("No permission.", ephemeral=True)
            return

        state.patrol_embed_title = "\U0001f693 Patrol Attendance (TEST)"
        state.voting_open = True
        patrol_channel = self.bot.get_channel(PATROL_CHANNEL_ID)

        role = f"<@&{PING_ROLE_ID}>"
        state.patrol_message = await patrol_channel.send(
            role, embed=build_patrol_embed(state.patrol_embed_title), view=PatrolView()
        )
        state.save_session()
        await interaction.response.send_message("Test patrol vote posted.", ephemeral=True)

    @app_commands.command(name="test_aop_vote")
    async def test_aop_vote(self, interaction: discord.Interaction):
        if not admin_check(interaction):
            await interaction.response.send_message("No permission.", ephemeral=True)
            return

        state.aop_embed_title = "\U0001f5fa\ufe0f AOP Voting (TEST)"
        state.voting_open = True
        aop_channel = self.bot.get_channel(AOP_CHANNEL_ID)

        state.aop_message = await aop_channel.send(
            embed=build_aop_embed(state.aop_embed_title), view=AOPView()
        )
        state.save_session()
        await interaction.response.send_message("Test AOP vote posted.", ephemeral=True)

    @app_commands.command(name="test_close_votes")
    async def test_close_votes(self, interaction: discord.Interaction):
        if not admin_check(interaction):
            await interaction.response.send_message("No permission.", ephemeral=True)
            return

        patrol_channel = self.bot.get_channel(PATROL_CHANNEL_ID)

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
            embed = styled_embed(
                "\u274c Patrol Cancelled (TEST)",
                f"Minimum attendance not reached.\n\n"
                f"\U0001f465 **Votes:** `{len(state.patrol_votes)}` / `{MINIMUM_PATROL}` minimum\n"
                f"\u274c **Can't Make It:** `{len(state.cant_make_votes)}`",
                discord.Color.red()
            )
            await patrol_channel.send(embed=embed)
            await interaction.response.send_message("Test close votes: patrol cancelled (not enough votes).", ephemeral=True)
            return

        if not state.aop_votes:
            options = mapLC if state.current_map == "LC" else mapLS
            selected_aop = random.choice(options)
        else:
            counts = {}
            for vote in state.aop_votes.values():
                counts[vote] = counts.get(vote, 0) + 1
            selected_aop = max(counts, key=counts.get)

        state.confirmed_start_time = start_time

        embed = styled_embed("\u2705 Patrol Confirmed (TEST)", color=discord.Color.green())
        embed.add_field(name="\U0001f550 Start Time", value=f"```{start_time}```", inline=True)
        embed.add_field(name="\U0001f465 Attending", value=f"```{len(state.patrol_votes)}```", inline=True)

        await patrol_channel.send(embed=embed)
        await interaction.response.send_message(f"Test close votes: patrol confirmed at {start_time}, AOP: {selected_aop}.", ephemeral=True)

    @app_commands.command(name="test_briefing")
    async def test_briefing(self, interaction: discord.Interaction):
        if not admin_check(interaction):
            await interaction.response.send_message("No permission.", ephemeral=True)
            return

        role = f"<@&{PING_ROLE_ID}>"
        channel = self.bot.get_channel(BRIEFING_CHANNEL_ID)

        time_display = state.confirmed_start_time or "7:00 PM EST"

        embed = styled_embed(
            "\U0001f4cb Briefing Reminder (TEST)",
            f"\u23f0 Patrol starts in **10 minutes** at **{time_display}**\n\n"
            f"\U0001f50a **Join the briefing:** <#{BRIEFING_VOICE_CHANNEL_ID}>\n\n"
            f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
            f"Make sure you're ready and in the voice channel!",
            discord.Color.orange()
        )

        await channel.send(role, embed=embed)
        await interaction.response.send_message("Test briefing reminder posted.", ephemeral=True)

    @app_commands.command(name="test_cancel")
    async def test_cancel(self, interaction: discord.Interaction):
        if not admin_check(interaction):
            await interaction.response.send_message("No permission.", ephemeral=True)
            return

        embed = styled_embed(
            "\u274c Patrol Cancelled (TEST)",
            "Cancelled by administration.",
            discord.Color.red()
        )

        await self.bot.get_channel(PATROL_CHANNEL_ID).send(embed=embed)
        await interaction.response.send_message("Test cancel posted.", ephemeral=True)

    @app_commands.command(name="test_override_time")
    @app_commands.autocomplete(time=time_autocomplete)
    async def test_override_time(self, interaction: discord.Interaction, time: str):
        if not admin_check(interaction):
            await interaction.response.send_message("No permission.", ephemeral=True)
            return

        embed = styled_embed(
            "\u26a0\ufe0f Patrol Override (TEST)",
            f"\U0001f550 Patrol will begin at **{time}**",
            discord.Color.gold()
        )

        await self.bot.get_channel(PATROL_CHANNEL_ID).send(embed=embed)
        await interaction.response.send_message("Test time override posted.", ephemeral=True)

    @app_commands.command(name="test_override_aop")
    @app_commands.autocomplete(area=current_map_area_autocomplete)
    async def test_override_aop(self, interaction: discord.Interaction, area: str):
        if not admin_check(interaction):
            await interaction.response.send_message("No permission.", ephemeral=True)
            return

        embed = styled_embed(
            "\u26a0\ufe0f AOP Override (TEST)",
            f"\U0001f4cd AOP has been set to **{area}**",
            discord.Color.gold()
        )

        await self.bot.get_channel(AOP_CHANNEL_ID).send(embed=embed)
        await interaction.response.send_message("Test AOP override posted.", ephemeral=True)

    @app_commands.command(name="test_fake_data")
    async def test_fake_data(self, interaction: discord.Interaction, days: int = 30):
        if not admin_check(interaction):
            await interaction.response.send_message("No permission.", ephemeral=True)
            return

        from config import TIMEZONE

        guild = self.bot.get_guild(GUILD_ID)
        members = [m for m in guild.members if not m.bot]

        if not members:
            await interaction.response.send_message("No non-bot members found.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        options = mapLC + mapLS
        today = datetime.datetime.now(TIMEZONE).date()
        patrols_added = 0

        for i in range(days):
            day = today - datetime.timedelta(days=i + 1)
            day_str = day.strftime("%Y-%m-%d")

            attending = random.sample(members, k=random.randint(2, min(len(members), 8)))
            not_coming = random.sample(
                [m for m in members if m not in attending],
                k=min(random.randint(0, 3), len(members) - len(attending))
            )

            cancelled = 1 if len(attending) < MINIMUM_PATROL else 0

            if patrol_day_exists(day_str):
                continue
            cursor.execute(
                "INSERT INTO patrol_days(day, attendance, cancelled, cant_make) VALUES(?, ?, ?, ?)",
                (day_str, len(attending), cancelled, len(not_coming))
            )

            if not cancelled:
                area = random.choice(options)
                cursor.execute("INSERT INTO aop_stats(area, day) VALUES(?, ?)", (area, day_str))

            for m in attending:
                ensure_member(m.id)
                cursor.execute(
                    "UPDATE members SET patrol_votes = patrol_votes + 1, patrol_attended = patrol_attended + 1 WHERE user_id = ?",
                    (m.id,)
                )

            for m in not_coming:
                ensure_member(m.id)
                cursor.execute(
                    "UPDATE members SET cant_make = cant_make + 1, patrol_skipped = patrol_skipped + 1 WHERE user_id = ?",
                    (m.id,)
                )

            for m in random.sample(members, k=random.randint(1, min(len(members), 6))):
                ensure_member(m.id)
                cursor.execute("UPDATE members SET aop_votes = aop_votes + 1 WHERE user_id = ?", (m.id,))

            patrols_added += 1

        conn.commit()

        await interaction.followup.send(f"Added **{patrols_added}** fake patrol days.", ephemeral=True)

    @app_commands.command(name="test_clear_data")
    async def test_clear_data(self, interaction: discord.Interaction):
        if not admin_check(interaction):
            await interaction.response.send_message("No permission.", ephemeral=True)
            return

        cursor.execute("DELETE FROM patrol_days")
        cursor.execute("DELETE FROM aop_stats")
        cursor.execute("DELETE FROM members")
        cursor.execute("DELETE FROM settings")
        cursor.execute("DELETE FROM activity_log")
        conn.commit()

        await interaction.response.send_message("All data cleared.", ephemeral=True)

    @app_commands.command(name="test_inactivity")
    async def test_inactivity(self, interaction: discord.Interaction):
        if not admin_check(interaction):
            await interaction.response.send_message("No permission.", ephemeral=True)
            return

        now = datetime.datetime.now(TIMEZONE)
        two_weeks_ago = (now.date() - datetime.timedelta(days=14)).strftime("%Y-%m-%d")

        cursor.execute("SELECT DISTINCT user_id FROM activity_log WHERE day >= ?", (two_weeks_ago,))
        active_users = {r[0] for r in cursor.fetchall()}

        guild = self.bot.get_guild(GUILD_ID)
        ping_role = guild.get_role(PING_ROLE_ID)

        if not ping_role:
            await interaction.response.send_message("Ping role not found.", ephemeral=True)
            return

        inactive = [m for m in ping_role.members if not m.bot and m.id not in active_users]

        if not inactive:
            await interaction.response.send_message("No inactive members.", ephemeral=True)
            return

        lines = [
            f"**{i}. {m.display_name}** (<@{m.id}>) \u2014 {get_inactive_reason(m.id)}"
            for i, m in enumerate(inactive, 1)
        ]

        await interaction.response.defer(ephemeral=True)
        await send_paginated(
            interaction.channel, "\u26a0\ufe0f Inactive Members (TEST)", lines, discord.Color.red()
        )
        await interaction.followup.send(f"Found {len(inactive)} inactive members.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(TestingCog(bot))
