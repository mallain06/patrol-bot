import os
import discord
from discord.ext import commands, tasks
import datetime
import pytz
import random
import sqlite3

TOKEN = os.getenv("TOKEN")

GUILD_ID = int(os.getenv("GUILD_ID", 0))
PATROL_CHANNEL_ID = int(os.getenv("PATROL_CHANNEL_ID", 0))
AOP_CHANNEL_ID = int(os.getenv("AOP_CHANNEL_ID", 0))
BRIEFING_CHANNEL_ID = int(os.getenv("BRIEFING_CHANNEL_ID", 0))
STATS_CHANNEL_ID = int(os.getenv("STATS_CHANNEL_ID", 0))
ADMIN_COMMAND_CHANNEL = int(os.getenv("ADMIN_COMMAND_CHANNEL", 0))

PING_ROLE_ID = int(os.getenv("PING_ROLE_ID", 0))
ADMIN_ROLE_ID = int(os.getenv("ADMIN_ROLE_ID", 0))

MINIMUM_PATROL = 4

TIMEZONE = pytz.timezone("US/Eastern")

bot = commands.Bot(command_prefix="!", intents=discord.Intents.all())
tree = bot.tree


# ---------------- DATABASE ----------------

conn = sqlite3.connect(os.getenv("DATABASE_PATH", "patrol_stats.db"))
conn.execute("PRAGMA journal_mode=WAL")
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS members(
user_id INTEGER PRIMARY KEY,
patrol_votes INTEGER DEFAULT 0,
cant_make INTEGER DEFAULT 0,
aop_votes INTEGER DEFAULT 0,
patrol_attended INTEGER DEFAULT 0,
patrol_skipped INTEGER DEFAULT 0,
aop_skipped INTEGER DEFAULT 0
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS patrol_days(
day TEXT,
attendance INTEGER
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS aop_stats(
area TEXT
)
""")

conn.commit()


def ensure_member(user_id):
    cursor.execute("INSERT OR IGNORE INTO members(user_id) VALUES(?)", (user_id,))
    conn.commit()


def record_stat(user_id, column):
    ensure_member(user_id)
    cursor.execute(f"UPDATE members SET {column} = {column} + 1 WHERE user_id = ?", (user_id,))
    conn.commit()


# ---------------- VARIABLES ----------------

time_slots = [
"7:00 PM EST",
"7:30 PM EST",
"8:00 PM EST",
"8:30 PM EST",
"9:00 PM EST"
]

patrol_votes = {}
cant_make_votes = set()
aop_votes = {}

current_map = "LC"

mapLS = [
"City of Orillia",
"City Of Barrie",
"Simcoe County Central",
"Kawartha Lakes",
"Peterborough",
"Northumberland",
"Prince Edward County"
]

mapLC = [
"Toronto Downtown",
"Toronto Etobicoke",
"York Region",
"Peel Region",
"Halton Region",
"Steeltown"
]


# ---------------- VIEWS ----------------

class PatrolView(discord.ui.View):

    def __init__(self):
        super().__init__(timeout=None)

        for time in time_slots:
            self.add_item(PatrolButton(time))

        self.add_item(CantMakeButton())


class PatrolButton(discord.ui.Button):

    def __init__(self, label):

        super().__init__(
            label=label,
            style=discord.ButtonStyle.primary
        )

        self.time = label

    async def callback(self, interaction: discord.Interaction):

        cant_make_votes.discard(interaction.user.id)
        patrol_votes[interaction.user.id] = self.time
        record_stat(interaction.user.id, "patrol_votes")

        await interaction.response.send_message(f"You voted for **{self.time}**.", ephemeral=True)


class CantMakeButton(discord.ui.Button):

    def __init__(self):

        super().__init__(
            label="Can't Make It",
            emoji="❌",
            style=discord.ButtonStyle.danger
        )

    async def callback(self, interaction: discord.Interaction):

        patrol_votes.pop(interaction.user.id, None)
        cant_make_votes.add(interaction.user.id)
        record_stat(interaction.user.id, "cant_make")

        await interaction.response.send_message("You marked **Can't Make It**.", ephemeral=True)


class AOPView(discord.ui.View):

    def __init__(self):

        super().__init__(timeout=None)

        options = mapLC if current_map == "LC" else mapLS

        for option in options:
            self.add_item(AOPButton(option))


class AOPButton(discord.ui.Button):

    def __init__(self, label):

        super().__init__(
            label=label,
            style=discord.ButtonStyle.secondary
        )

        self.option = label

    async def callback(self, interaction: discord.Interaction):

        aop_votes[interaction.user.id] = self.option
        record_stat(interaction.user.id, "aop_votes")

        await interaction.response.send_message(f"You voted for **{self.option}**.", ephemeral=True)


# ---------------- BOT READY ----------------

@bot.event
async def on_ready():

    print("Bot Online")

    scheduler.start()
    close_votes.start()

    tree.copy_global_to(guild=discord.Object(id=GUILD_ID))
    await tree.sync(guild=discord.Object(id=GUILD_ID))


# ---------------- SCHEDULER ----------------

@tasks.loop(minutes=1)
async def scheduler():

    now = datetime.datetime.now(TIMEZONE)

    if now.hour == 8 and now.minute == 0:

        patrol_votes.clear()
        cant_make_votes.clear()
        aop_votes.clear()

        patrol_channel = bot.get_channel(PATROL_CHANNEL_ID)
        aop_channel = bot.get_channel(AOP_CHANNEL_ID)

        role = f"<@&{PING_ROLE_ID}>"

        embed = discord.Embed(
            title="🚓 Patrol Attendance",
            description="Vote for tonight's patrol start time.\nMinimum **4 members required**.",
            color=discord.Color.blue()
        )

        await patrol_channel.send(role, embed=embed, view=PatrolView())

        aop_embed = discord.Embed(
            title="🗺️ AOP Voting",
            description="Vote for tonight's patrol area.",
            color=discord.Color.purple()
        )

        await aop_channel.send(embed=aop_embed, view=AOPView())


# ---------------- CLOSE VOTES ----------------

@tasks.loop(minutes=1)
async def close_votes():

    now = datetime.datetime.now(TIMEZONE)

    if now.hour == 18 and now.minute == 30:

        patrol_channel = bot.get_channel(PATROL_CHANNEL_ID)

        attendance_counts = {time:0 for time in time_slots}

        for vote in patrol_votes.values():
            attendance_counts[vote]+=1

        cumulative = 0
        start_time = None

        for time in time_slots:

            cumulative += attendance_counts[time]

            if cumulative >= MINIMUM_PATROL:
                start_time = time
                break

        if not start_time:

            embed = discord.Embed(
                title="❌ Patrol Cancelled",
                description="Minimum attendance not reached.",
                color=discord.Color.red()
            )

            await patrol_channel.send(embed=embed)
            return


        if not aop_votes:

            options = mapLC if current_map == "LC" else mapLS
            selected_aop = random.choice(options)

        else:

            counts = {}

            for vote in aop_votes.values():
                counts[vote] = counts.get(vote,0)+1

            selected_aop = max(counts, key=counts.get)


        today = now.strftime("%Y-%m-%d")
        cursor.execute("INSERT INTO patrol_days(day, attendance) VALUES(?, ?)", (today, len(patrol_votes)))
        cursor.execute("INSERT INTO aop_stats(area) VALUES(?)", (selected_aop,))

        for user_id in patrol_votes:
            record_stat(user_id, "patrol_attended")

        for user_id in cant_make_votes:
            record_stat(user_id, "patrol_skipped")

        conn.commit()

        embed = discord.Embed(
            title="🚓 Patrol Confirmed",
            color=discord.Color.green()
        )

        embed.add_field(name="AOP", value=selected_aop)
        embed.add_field(name="Members Attending", value=str(len(patrol_votes)))
        embed.add_field(name="Minimum Required", value=str(MINIMUM_PATROL))
        embed.add_field(name="Start Time", value=start_time)

        await patrol_channel.send(embed=embed)


# ---------------- ADMIN CHECK ----------------

def admin_check(interaction):

    if interaction.channel.id != ADMIN_COMMAND_CHANNEL:
        return False

    return ADMIN_ROLE_ID in [r.id for r in interaction.user.roles]


# ---------------- ADMIN COMMANDS ----------------

@tree.command(name="cancel_patrol")
async def cancel_patrol(interaction: discord.Interaction):

    if not admin_check(interaction):
        await interaction.response.send_message("No permission.", ephemeral=True)
        return

    embed = discord.Embed(
        title="❌ Patrol Cancelled",
        description="Cancelled by administration.",
        color=discord.Color.red()
    )

    await interaction.response.send_message("Patrol cancelled.", ephemeral=True)
    await bot.get_channel(PATROL_CHANNEL_ID).send(embed=embed)


@tree.command(name="override_patrol_time")
async def override_patrol_time(interaction: discord.Interaction, time:str):

    if not admin_check(interaction):
        await interaction.response.send_message("No permission.", ephemeral=True)
        return

    embed = discord.Embed(
        title="⚠️ Patrol Override",
        description=f"Patrol will begin at **{time}**",
        color=discord.Color.gold()
    )

    await interaction.response.send_message("Override sent.", ephemeral=True)
    await bot.get_channel(PATROL_CHANNEL_ID).send(embed=embed)


@tree.command(name="maplc")
async def map_lc(interaction: discord.Interaction):

    global current_map

    if not admin_check(interaction):
        return

    current_map = "LC"

    await interaction.response.send_message("Map switched to LC.")


@tree.command(name="mapls")
async def map_ls(interaction: discord.Interaction):

    global current_map

    if not admin_check(interaction):
        return

    current_map = "LS"

    await interaction.response.send_message("Map switched to LS.")


bot.run(TOKEN)