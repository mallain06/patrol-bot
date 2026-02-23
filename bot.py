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
BRIEFING_VOICE_CHANNEL_ID = int(os.getenv("BRIEFING_VOICE_CHANNEL_ID", 0))
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

cursor.execute("""
CREATE TABLE IF NOT EXISTS settings(
key TEXT PRIMARY KEY,
value TEXT
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
confirmed_start_time = None

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
    briefing_reminder.start()
    stats_checker.start()

    tree.copy_global_to(guild=discord.Object(id=GUILD_ID))
    await tree.sync(guild=discord.Object(id=GUILD_ID))


# ---------------- SCHEDULER ----------------

@tasks.loop(minutes=1)
async def scheduler():

    now = datetime.datetime.now(TIMEZONE)

    if now.hour == 8 and now.minute == 0:

        global confirmed_start_time
        patrol_votes.clear()
        cant_make_votes.clear()
        aop_votes.clear()
        confirmed_start_time = None

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

        global confirmed_start_time
        confirmed_start_time = start_time

        embed.add_field(name="AOP", value=selected_aop)
        embed.add_field(name="Members Attending", value=str(len(patrol_votes)))
        embed.add_field(name="Minimum Required", value=str(MINIMUM_PATROL))
        embed.add_field(name="Start Time", value=start_time)

        await patrol_channel.send(embed=embed)


# ---------------- BRIEFING REMINDER ----------------

@tasks.loop(minutes=1)
async def briefing_reminder():

    global confirmed_start_time

    if not confirmed_start_time:
        return

    now = datetime.datetime.now(TIMEZONE)

    # Parse the start time (e.g. "7:00 PM EST")
    time_str = confirmed_start_time.replace(" EST", "")
    start_dt = datetime.datetime.strptime(time_str, "%I:%M %p")
    start_dt = now.replace(hour=start_dt.hour, minute=start_dt.minute, second=0, microsecond=0)

    # Convert 12h to 24h is handled by strptime, but adjust for PM
    briefing_dt = start_dt - datetime.timedelta(minutes=10)

    if now.hour == briefing_dt.hour and now.minute == briefing_dt.minute:

        role = f"<@&{PING_ROLE_ID}>"
        channel = bot.get_channel(BRIEFING_CHANNEL_ID)

        embed = discord.Embed(
            title="📋 Briefing Reminder",
            description=f"Patrol starts in **10 minutes** at **{confirmed_start_time}**.\nJoin the briefing: <#{BRIEFING_VOICE_CHANNEL_ID}>",
            color=discord.Color.orange()
        )

        await channel.send(role, embed=embed)
        confirmed_start_time = None


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


@tree.command(name="override_aop")
async def override_aop(interaction: discord.Interaction, area: str):

    if not admin_check(interaction):
        await interaction.response.send_message("No permission.", ephemeral=True)
        return

    embed = discord.Embed(
        title="⚠️ AOP Override",
        description=f"AOP has been set to **{area}**",
        color=discord.Color.gold()
    )

    await interaction.response.send_message("AOP override sent.", ephemeral=True)
    await bot.get_channel(AOP_CHANNEL_ID).send(embed=embed)


@tree.command(name="force_stats")
async def force_stats(interaction: discord.Interaction):

    if not admin_check(interaction):
        await interaction.response.send_message("No permission.", ephemeral=True)
        return

    await interaction.response.send_message("Posting stats leaderboard...", ephemeral=True)
    await post_stats_leaderboard()


@tree.command(name="user_stats")
async def user_stats(interaction: discord.Interaction, member: discord.Member):

    if not admin_check(interaction):
        await interaction.response.send_message("No permission.", ephemeral=True)
        return

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

    embed = discord.Embed(
        title=f"📋 Stats for {member.display_name}",
        color=member.color if member.color != discord.Color.default() else discord.Color.blue()
    )

    embed.set_thumbnail(url=member.display_avatar.url)

    embed.add_field(name="Patrol Votes", value=str(p_votes), inline=True)
    embed.add_field(name="Patrols Attended", value=str(p_attended), inline=True)
    embed.add_field(name="Attendance Rate", value=f"{attend_rate:.0f}%", inline=True)
    embed.add_field(name="AOP Votes", value=str(a_votes), inline=True)
    embed.add_field(name="Can't Make It", value=str(cant), inline=True)
    embed.add_field(name="Patrol Skipped", value=str(p_skip), inline=True)
    embed.add_field(name="AOP Skipped", value=str(a_skip), inline=True)

    await interaction.response.send_message(embed=embed)


@tree.command(name="server_stats")
async def server_stats(interaction: discord.Interaction):

    if not admin_check(interaction):
        await interaction.response.send_message("No permission.", ephemeral=True)
        return

    cursor.execute("SELECT day, attendance FROM patrol_days")
    patrol_rows = cursor.fetchall()

    cursor.execute("SELECT area FROM aop_stats")
    aop_rows = cursor.fetchall()

    cursor.execute("SELECT COUNT(*) FROM members")
    total_members = cursor.fetchone()[0]

    if not patrol_rows:
        await interaction.response.send_message("No patrol data yet.", ephemeral=True)
        return

    # Day of week breakdown
    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    day_counts = {d: 0 for d in day_names}
    day_attendance = {d: [] for d in day_names}

    for day_str, attendance in patrol_rows:
        dt = datetime.datetime.strptime(day_str, "%Y-%m-%d")
        name = day_names[dt.weekday()]
        day_counts[name] += 1
        day_attendance[name] += [attendance]

    active_days = {d: c for d, c in day_counts.items() if c > 0}
    sorted_days = sorted(active_days.items(), key=lambda x: x[1], reverse=True)

    inactive_days = [d for d, c in day_counts.items() if c == 0]

    day_lines = []
    for name, count in sorted_days:
        avg = sum(day_attendance[name]) / len(day_attendance[name])
        day_lines.append(f"**{name}** — {count} patrols, avg {avg:.1f} members")

    # AOP popularity
    aop_counts = {}
    for (area,) in aop_rows:
        aop_counts[area] = aop_counts.get(area, 0) + 1

    sorted_aops = sorted(aop_counts.items(), key=lambda x: x[1], reverse=True)
    aop_lines = [f"**{area}** — {count} times" for area, count in sorted_aops]

    # Overall stats
    total_patrols = len(patrol_rows)
    all_attendance = [a for _, a in patrol_rows]
    avg_attendance = sum(all_attendance) / total_patrols
    highest = max(all_attendance)
    lowest = min(all_attendance)

    embed = discord.Embed(
        title="📈 Server Statistics",
        color=discord.Color.teal()
    )

    embed.add_field(
        name="Overview",
        value=(
            f"Total Patrols: **{total_patrols}**\n"
            f"Tracked Members: **{total_members}**\n"
            f"Avg Attendance: **{avg_attendance:.1f}**\n"
            f"Highest Attendance: **{highest}**\n"
            f"Lowest Attendance: **{lowest}**"
        ),
        inline=False
    )

    embed.add_field(
        name="Patrols by Day of Week",
        value="\n".join(day_lines) if day_lines else "No data",
        inline=False
    )

    if inactive_days:
        embed.add_field(
            name="Days With No Patrols",
            value=", ".join(inactive_days),
            inline=False
        )

    embed.add_field(
        name="AOP Popularity",
        value="\n".join(aop_lines) if aop_lines else "No data",
        inline=False
    )

    await interaction.response.send_message(embed=embed)


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


# ---------------- STATS LEADERBOARD ----------------

async def post_stats_leaderboard():

    cursor.execute("SELECT user_id, patrol_votes, patrol_attended, aop_votes, cant_make, patrol_skipped, aop_skipped FROM members ORDER BY patrol_attended DESC")
    rows = cursor.fetchall()

    if not rows:
        return

    guild = bot.get_guild(GUILD_ID)
    channel = bot.get_channel(STATS_CHANNEL_ID)

    embed = discord.Embed(
        title="📊 Biweekly Stats Leaderboard",
        color=discord.Color.blue()
    )

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

    embed.description = "\n\n".join(lines)

    today = datetime.datetime.now(TIMEZONE).strftime("%Y-%m-%d")
    cursor.execute("INSERT OR REPLACE INTO settings(key, value) VALUES('last_stats_post', ?)", (today,))
    conn.commit()

    await channel.send(embed=embed)


@tasks.loop(minutes=1)
async def stats_checker():

    now = datetime.datetime.now(TIMEZONE)

    if now.hour != 12 or now.minute != 0:
        return

    cursor.execute("SELECT value FROM settings WHERE key = 'last_stats_post'")
    row = cursor.fetchone()

    if row:
        last_date = datetime.datetime.strptime(row[0], "%Y-%m-%d").date()
        if (now.date() - last_date).days < 14:
            return

    await post_stats_leaderboard()


bot.run(TOKEN)