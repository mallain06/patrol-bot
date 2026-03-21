import datetime
import random

import discord
from discord.ext import commands
from discord import app_commands

import state
from config import (
    TIMEZONE, PATROL_CHANNEL_ID, AOP_CHANNEL_ID, ANNOUNCEMENT_CHANNEL_ID,
    BRIEFING_VOICE_CHANNEL_ID, PING_ROLE_ID, MINIMUM_PATROL,
    time_slots, mapLC, mapLS,
)
from database import cursor, conn, record_stat, log_activity, patrol_day_exists, aop_stat_exists
from helpers import (
    styled_embed, build_patrol_embed, build_aop_embed, lock_voting,
    admin_check, time_autocomplete, area_autocomplete, current_map_area_autocomplete,
)
from views import PatrolView, AOPView


class AdminCog(commands.Cog):

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="help", description="Show all admin commands and bot features")
    async def help_menu(self, interaction: discord.Interaction):
        if not admin_check(interaction):
            await interaction.response.send_message("No permission.", ephemeral=True)
            return

        embed = styled_embed("Patrol Bot — Admin Help", "All available admin commands grouped by category.", discord.Color.blue())

        embed.add_field(
            name="\U0001f5f3\ufe0f Voting Management",
            value=(
                "`/open_votes` — Open both patrol and AOP voting\n"
                "`/open_patrol_vote` — Open only patrol voting\n"
                "`/open_aop_vote` — Open only AOP voting\n"
                "`/close_patrol_votes` — Close patrol voting, confirm attendance & start time\n"
                "`/close_aop_votes` — Close AOP voting, select winning area"
            ),
            inline=False
        )

        embed.add_field(
            name="\U0001f693 Patrol Control",
            value=(
                "`/start_patrol <time> <area>` — Force start patrol with a specific time and area\n"
                "`/cancel_patrol` — Cancel tonight's patrol\n"
                "`/override_patrol_time <time>` — Override the confirmed patrol start time\n"
                "`/override_aop <area>` — Override the confirmed AOP area"
            ),
            inline=False
        )

        embed.add_field(
            name="\U0001f5fa\ufe0f Map Management",
            value=(
                "`/maplc` — Switch map to Liberty City (resets AOP votes)\n"
                "`/mapls` — Switch map to Los Santos (resets AOP votes)"
            ),
            inline=False
        )

        embed.add_field(
            name="\U0001f4ca Statistics",
            value=(
                "`/force_stats` — Manually post the stats leaderboard\n"
                "`/user_stats <member> [period]` — View a member's individual stats\n"
                "`/check_inactive` — List inactive members (past 2 weeks)\n"
                "`/server_stats [period]` — View overall server statistics\n"
                "`/activity_stats [period]` — View activity and no-show rates\n"
                "`/aop_breakdown [period]` — View AOP area popularity breakdown"
            ),
            inline=False
        )

        embed.add_field(
            name="\u2699\ufe0f Other",
            value=(
                "`/clear_stats` — Clear all statistics data (**destructive**)\n"
                "`/help` — Show this help menu"
            ),
            inline=False
        )

        embed.add_field(
            name="\U0001f916 Automated Tasks",
            value=(
                "These run automatically — no command needed:\n"
                "\u2022 **8:00 AM** — Opens patrol & AOP voting\n"
                "\u2022 **6:30 PM** — Closes voting & records attendance\n"
                "\u2022 **10 min before patrol** — Posts briefing reminder\n"
                "\u2022 **Every 2 weeks** — Posts stats leaderboard & inactivity check"
            ),
            inline=False
        )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="close_patrol_votes")
    async def close_patrol_votes(self, interaction: discord.Interaction):
        if not admin_check(interaction):
            await interaction.response.send_message("No permission.", ephemeral=True)
            return

        await lock_voting()

        patrol_channel = self.bot.get_channel(PATROL_CHANNEL_ID)
        announcement_channel = self.bot.get_channel(ANNOUNCEMENT_CHANNEL_ID)
        now = datetime.datetime.now(TIMEZONE)

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
            if not patrol_day_exists(today):
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

            aop_embed = styled_embed(
                "\u274c AOP Cancelled",
                "Patrol was cancelled \u2014 no AOP tonight.",
                discord.Color.red()
            )
            await self.bot.get_channel(AOP_CHANNEL_ID).send(embed=aop_embed)

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

            await interaction.response.send_message("Patrol votes closed \u2014 cancelled (not enough votes).", ephemeral=True)
            return

        today = now.strftime("%Y-%m-%d")
        if patrol_day_exists(today):
            await interaction.response.send_message("Stats already recorded for today.", ephemeral=True)
            return
        cursor.execute(
            "INSERT INTO patrol_days(day, attendance, cancelled, cant_make) VALUES(?, ?, 0, ?)",
            (today, len(state.patrol_votes), len(state.cant_make_votes))
        )

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

        if announcement_channel:
            announce = styled_embed(
                "\U0001f693 Tonight's Patrol is Confirmed!",
                f"Patrol is happening tonight! Here are the details:\n\n"
                f"\U0001f550 **Start Time:** {start_time}\n"
                f"\U0001f465 **Members Attending:** {len(state.patrol_votes)}\n\n"
                f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
                f"\U0001f4cb Briefing starts **10 minutes** before in <#{BRIEFING_VOICE_CHANNEL_ID}>",
                discord.Color.green()
            )
            role = f"<@&{PING_ROLE_ID}>"
            state.announcement_message = await announcement_channel.send(role, embed=announce)

        state.save_session()
        await interaction.response.send_message(f"Patrol votes closed \u2014 confirmed at {start_time}.", ephemeral=True)

    @app_commands.command(name="close_aop_votes")
    async def close_aop_votes(self, interaction: discord.Interaction):
        if not admin_check(interaction):
            await interaction.response.send_message("No permission.", ephemeral=True)
            return

        now = datetime.datetime.now(TIMEZONE)
        today = now.strftime("%Y-%m-%d")

        if not state.aop_votes:
            options = mapLC if state.current_map == "LC" else mapLS
            selected_aop = random.choice(options)
        else:
            counts = {}
            for vote in state.aop_votes.values():
                counts[vote] = counts.get(vote, 0) + 1
            selected_aop = max(counts, key=counts.get)

        if not aop_stat_exists(today):
            cursor.execute("INSERT INTO aop_stats(area, day) VALUES(?, ?)", (selected_aop, today))
            conn.commit()

        embed = styled_embed("\u2705 AOP Confirmed", color=discord.Color.purple())
        embed.add_field(name="\U0001f4cd Area", value=f"```{selected_aop}```", inline=True)
        embed.add_field(name="\U0001f5f3\ufe0f Votes", value=f"```{len(state.aop_votes)}```", inline=True)
        role = f"<@&{PING_ROLE_ID}>"
        await self.bot.get_channel(AOP_CHANNEL_ID).send(role, embed=embed)

        if state.announcement_message:
            old_embed = state.announcement_message.embeds[0]
            desc = old_embed.description or ""
            lines = desc.split("\n")
            new_lines = []
            for line in lines:
                if line.startswith("\U0001f4cd"):
                    new_lines.append(f"\U0001f4cd **AOP:** {selected_aop}")
                else:
                    new_lines.append(line)
            if not any(l.startswith("\U0001f4cd") for l in lines):
                for i, line in enumerate(new_lines):
                    if line.startswith("\U0001f550"):
                        new_lines.insert(i + 1, f"\U0001f4cd **AOP:** {selected_aop}")
                        break
            new_embed = styled_embed("\U0001f693 Tonight's Patrol is Confirmed!", "\n".join(new_lines), discord.Color.green())
            state.announcement_message = await state.announcement_message.edit(embed=new_embed)

        await interaction.response.send_message(f"AOP votes closed \u2014 {selected_aop}.", ephemeral=True)

    @app_commands.command(name="start_patrol")
    @app_commands.autocomplete(time=time_autocomplete, area=area_autocomplete)
    async def start_patrol(self, interaction: discord.Interaction, time: str, area: str):
        if not admin_check(interaction):
            await interaction.response.send_message("No permission.", ephemeral=True)
            return

        await lock_voting()

        state.confirmed_start_time = time

        patrol_channel = self.bot.get_channel(PATROL_CHANNEL_ID)
        announcement_channel = self.bot.get_channel(ANNOUNCEMENT_CHANNEL_ID)

        embed = styled_embed("\u2705 Patrol Confirmed", "\u26a1 Forced start by administration.", discord.Color.green())
        embed.add_field(name="\U0001f550 Start Time", value=f"```{time}```", inline=True)
        embed.add_field(name="\U0001f465 Attending", value=f"```{len(state.patrol_votes) if state.patrol_votes else 'N/A'}```", inline=True)

        now = datetime.datetime.now(TIMEZONE)
        today = now.strftime("%Y-%m-%d")
        if not patrol_day_exists(today):
            cursor.execute(
                "INSERT INTO patrol_days(day, attendance, cancelled, cant_make) VALUES(?, ?, 0, ?)",
                (today, len(state.patrol_votes), len(state.cant_make_votes))
            )
        if not aop_stat_exists(today):
            cursor.execute("INSERT INTO aop_stats(area, day) VALUES(?, ?)", (area, today))
        conn.commit()

        await patrol_channel.send(embed=embed)

        if announcement_channel:
            announce = styled_embed(
                "\U0001f693 Tonight's Patrol is Confirmed!",
                f"Patrol is happening tonight! Here are the details:\n\n"
                f"\U0001f550 **Start Time:** {time}\n"
                f"\U0001f4cd **AOP:** {area}\n"
                f"\U0001f465 **Members Attending:** {len(state.patrol_votes) if state.patrol_votes else 'N/A'}\n\n"
                f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
                f"\U0001f4cb Briefing starts **10 minutes** before in <#{BRIEFING_VOICE_CHANNEL_ID}>",
                discord.Color.green()
            )
            role = f"<@&{PING_ROLE_ID}>"
            state.announcement_message = await announcement_channel.send(role, embed=announce)

        state.save_session()
        await interaction.response.send_message(f"Patrol force started at {time}, AOP: {area}.", ephemeral=True)

    @app_commands.command(name="cancel_patrol")
    async def cancel_patrol(self, interaction: discord.Interaction):
        if not admin_check(interaction):
            await interaction.response.send_message("No permission.", ephemeral=True)
            return

        await lock_voting()

        embed = styled_embed(
            "\u274c Patrol Cancelled",
            "Cancelled by administration.",
            discord.Color.red()
        )
        await self.bot.get_channel(PATROL_CHANNEL_ID).send(embed=embed)

        aop_embed = styled_embed(
            "\u274c AOP Cancelled",
            "Patrol was cancelled \u2014 no AOP tonight.",
            discord.Color.red()
        )
        await self.bot.get_channel(AOP_CHANNEL_ID).send(embed=aop_embed)

        announcement_channel = self.bot.get_channel(ANNOUNCEMENT_CHANNEL_ID)
        if announcement_channel:
            if state.announcement_message:
                cancel_embed = styled_embed(
                    "\u274c Tonight's Patrol Has Been Cancelled",
                    "Patrol has been cancelled by administration.",
                    discord.Color.red()
                )
                state.announcement_message = await state.announcement_message.edit(embed=cancel_embed)
            else:
                announce = styled_embed(
                    "\u274c Tonight's Patrol Has Been Cancelled",
                    f"Patrol has been cancelled by administration.\n\n"
                    f"\U0001f465 **Signed Up:** `{len(state.patrol_votes)}` / `{MINIMUM_PATROL}` minimum\n"
                    f"\u274c **Can't Make It:** `{len(state.cant_make_votes)}`\n\n"
                    f"Better luck next time!",
                    discord.Color.red()
                )
                role = f"<@&{PING_ROLE_ID}>"
                await announcement_channel.send(role, embed=announce)

        now = datetime.datetime.now(TIMEZONE)
        today = now.strftime("%Y-%m-%d")
        if not patrol_day_exists(today):
            cursor.execute(
                "INSERT INTO patrol_days(day, attendance, cancelled, cant_make) VALUES(?, ?, 1, ?)",
                (today, len(state.patrol_votes), len(state.cant_make_votes))
            )
            conn.commit()

        state.save_session()
        await interaction.response.send_message("Patrol cancelled.", ephemeral=True)

    @app_commands.command(name="open_votes")
    async def open_votes(self, interaction: discord.Interaction):
        if not admin_check(interaction):
            await interaction.response.send_message("No permission.", ephemeral=True)
            return

        state.patrol_votes.clear()
        state.cant_make_votes.clear()
        state.aop_votes.clear()
        state.voting_open = True
        state.patrol_embed_title = "\U0001f693 Patrol Attendance"
        state.aop_embed_title = "\U0001f5fa\ufe0f AOP Voting"

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
        await interaction.response.send_message("Patrol and AOP voting opened.", ephemeral=True)

    @app_commands.command(name="open_patrol_vote")
    async def open_patrol_vote(self, interaction: discord.Interaction):
        if not admin_check(interaction):
            await interaction.response.send_message("No permission.", ephemeral=True)
            return

        state.patrol_votes.clear()
        state.cant_make_votes.clear()
        state.voting_open = True
        state.patrol_embed_title = "\U0001f693 Patrol Attendance"

        patrol_channel = self.bot.get_channel(PATROL_CHANNEL_ID)
        role = f"<@&{PING_ROLE_ID}>"

        state.patrol_message = await patrol_channel.send(
            role, embed=build_patrol_embed(state.patrol_embed_title), view=PatrolView()
        )
        state.save_session()
        await interaction.response.send_message("Patrol voting opened.", ephemeral=True)

    @app_commands.command(name="open_aop_vote")
    async def open_aop_vote(self, interaction: discord.Interaction):
        if not admin_check(interaction):
            await interaction.response.send_message("No permission.", ephemeral=True)
            return

        state.aop_votes.clear()
        state.voting_open = True
        state.aop_embed_title = "\U0001f5fa\ufe0f AOP Voting"

        aop_channel = self.bot.get_channel(AOP_CHANNEL_ID)
        role = f"<@&{PING_ROLE_ID}>"

        state.aop_message = await aop_channel.send(
            role, embed=build_aop_embed(state.aop_embed_title), view=AOPView()
        )
        state.save_session()
        await interaction.response.send_message("AOP voting opened.", ephemeral=True)

    @app_commands.command(name="override_patrol_time")
    @app_commands.autocomplete(time=time_autocomplete)
    async def override_patrol_time(self, interaction: discord.Interaction, time: str):
        if not admin_check(interaction):
            await interaction.response.send_message("No permission.", ephemeral=True)
            return

        state.confirmed_start_time = time

        embed = styled_embed(
            "\u26a0\ufe0f Patrol Override",
            f"\U0001f550 Patrol will begin at **{time}**",
            discord.Color.gold()
        )
        await self.bot.get_channel(PATROL_CHANNEL_ID).send(embed=embed)

        if state.announcement_message:
            old_embed = state.announcement_message.embeds[0]
            desc = old_embed.description or ""
            desc = "\n".join(
                f"\U0001f550 **Start Time:** {time}" if line.startswith("\U0001f550") else line
                for line in desc.split("\n")
            )
            new_embed = styled_embed("\U0001f693 Tonight's Patrol is Confirmed!", desc, discord.Color.green())
            state.announcement_message = await state.announcement_message.edit(embed=new_embed)

        state.save_session()
        await interaction.response.send_message("Override sent.", ephemeral=True)

    @app_commands.command(name="override_aop")
    @app_commands.autocomplete(area=current_map_area_autocomplete)
    async def override_aop(self, interaction: discord.Interaction, area: str):
        if not admin_check(interaction):
            await interaction.response.send_message("No permission.", ephemeral=True)
            return

        embed = styled_embed(
            "\u26a0\ufe0f AOP Override",
            f"\U0001f4cd AOP has been set to **{area}**",
            discord.Color.gold()
        )
        await self.bot.get_channel(AOP_CHANNEL_ID).send(embed=embed)

        if state.announcement_message:
            old_embed = state.announcement_message.embeds[0]
            desc = old_embed.description or ""
            lines = desc.split("\n")
            new_lines = []
            for line in lines:
                if line.startswith("\U0001f4cd"):
                    new_lines.append(f"\U0001f4cd **AOP:** {area}")
                else:
                    new_lines.append(line)
            if not any(l.startswith("\U0001f4cd") for l in lines):
                for i, line in enumerate(new_lines):
                    if line.startswith("\U0001f550"):
                        new_lines.insert(i + 1, f"\U0001f4cd **AOP:** {area}")
                        break
            new_embed = styled_embed("\U0001f693 Tonight's Patrol is Confirmed!", "\n".join(new_lines), discord.Color.green())
            state.announcement_message = await state.announcement_message.edit(embed=new_embed)

        await interaction.response.send_message("AOP override sent.", ephemeral=True)

    @app_commands.command(name="maplc")
    async def map_lc(self, interaction: discord.Interaction):
        if not admin_check(interaction):
            return

        state.current_map = "LC"

        msg = "Map switched to LC."
        if state.voting_open and state.aop_message:
            state.aop_votes.clear()
            new_view = AOPView()
            await state.aop_message.edit(embed=build_aop_embed(state.aop_embed_title), view=new_view)
            msg += " AOP votes have been reset for the new map."

        state.save_session()
        await interaction.response.send_message(msg)

    @app_commands.command(name="mapls")
    async def map_ls(self, interaction: discord.Interaction):
        if not admin_check(interaction):
            return

        state.current_map = "LS"

        msg = "Map switched to LS."
        if state.voting_open and state.aop_message:
            state.aop_votes.clear()
            new_view = AOPView()
            await state.aop_message.edit(embed=build_aop_embed(state.aop_embed_title), view=new_view)
            msg += " AOP votes have been reset for the new map."

        state.save_session()
        await interaction.response.send_message(msg)


    @app_commands.command(name="clear_stats")
    async def clear_stats(self, interaction: discord.Interaction):
        if not admin_check(interaction):
            await interaction.response.send_message("No permission.", ephemeral=True)
            return

        cursor.execute("DELETE FROM patrol_days")
        cursor.execute("DELETE FROM aop_stats")
        cursor.execute("DELETE FROM members")
        cursor.execute("DELETE FROM activity_log")
        cursor.execute("DELETE FROM settings")
        conn.commit()

        await interaction.response.send_message("All stats data has been cleared.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(AdminCog(bot))
