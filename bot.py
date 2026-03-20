import os
import discord
from discord.ext import commands, tasks
import datetime
import pytz
import random
import sqlite3
import json
import enum

TOKEN = os.getenv("TOKEN")


class Period(enum.Enum):
    all_time = "All Time"
    last_2_weeks = "Last 2 Weeks"

GUILD_ID = int(os.getenv("GUILD_ID", 0))
PATROL_CHANNEL_ID = int(os.getenv("PATROL_CHANNEL_ID", 0))
AOP_CHANNEL_ID = int(os.getenv("AOP_CHANNEL_ID", 0))
BRIEFING_CHANNEL_ID = int(os.getenv("BRIEFING_CHANNEL_ID", 0))
BRIEFING_VOICE_CHANNEL_ID = int(os.getenv("BRIEFING_VOICE_CHANNEL_ID", 0))
STATS_CHANNEL_ID = int(os.getenv("STATS_CHANNEL_ID", 0))
ANNOUNCEMENT_CHANNEL_ID = int(os.getenv("ANNOUNCEMENT_CHANNEL_ID", 0))
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
area TEXT,
day TEXT
)
""")

cursor.execute("PRAGMA table_info(aop_stats)")
aop_columns = [col[1] for col in cursor.fetchall()]
if "day" not in aop_columns:
    cursor.execute("ALTER TABLE aop_stats ADD COLUMN day TEXT")

cursor.execute("PRAGMA table_info(patrol_days)")
patrol_columns = [col[1] for col in cursor.fetchall()]
if "cancelled" not in patrol_columns:
    cursor.execute("ALTER TABLE patrol_days ADD COLUMN cancelled INTEGER DEFAULT 0")
if "cant_make" not in patrol_columns:
    cursor.execute("ALTER TABLE patrol_days ADD COLUMN cant_make INTEGER DEFAULT 0")

cursor.execute("""
CREATE TABLE IF NOT EXISTS settings(
key TEXT PRIMARY KEY,
value TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS activity_log(
user_id INTEGER,
action TEXT,
day TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS session_state(
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


def log_activity(user_id, action):
    today = datetime.datetime.now(TIMEZONE).strftime("%Y-%m-%d")
    cursor.execute("INSERT INTO activity_log(user_id, action, day) VALUES(?, ?, ?)", (user_id, action, today))
    conn.commit()


def save_session():
    state = {
        "patrol_votes": {str(k): v for k, v in patrol_votes.items()},
        "cant_make_votes": [int(uid) for uid in cant_make_votes],
        "aop_votes": {str(k): v for k, v in aop_votes.items()},
        "confirmed_start_time": confirmed_start_time,
        "voting_open": voting_open,
        "current_map": current_map,
        "patrol_message_id": patrol_message.id if patrol_message else None,
        "patrol_channel_id": patrol_message.channel.id if patrol_message else None,
        "aop_message_id": aop_message.id if aop_message else None,
        "aop_channel_id": aop_message.channel.id if aop_message else None,
        "announcement_message_id": announcement_message.id if announcement_message else None,
        "announcement_channel_id": announcement_message.channel.id if announcement_message else None,
    }
    cursor.execute(
        "INSERT OR REPLACE INTO session_state(key, value) VALUES('state', ?)",
        (json.dumps(state),)
    )
    conn.commit()


async def load_session():
    global patrol_votes, cant_make_votes, aop_votes, confirmed_start_time
    global voting_open, current_map, patrol_message, aop_message, announcement_message

    cursor.execute("SELECT value FROM session_state WHERE key = 'state'")
    row = cursor.fetchone()
    if not row:
        return

    state = json.loads(row[0])

    patrol_votes.clear()
    patrol_votes.update({int(k): v for k, v in state.get("patrol_votes", {}).items()})
    cant_make_votes.clear()
    cant_make_votes.update(state.get("cant_make_votes", []))
    aop_votes.clear()
    aop_votes.update({int(k): v for k, v in state.get("aop_votes", {}).items()})
    confirmed_start_time = state.get("confirmed_start_time")
    voting_open = state.get("voting_open", False)
    current_map = state.get("current_map", "LC")

    # Restore message references
    try:
        pid = state.get("patrol_message_id")
        pcid = state.get("patrol_channel_id")
        if pid and pcid:
            ch = bot.get_channel(pcid)
            if ch:
                patrol_message = await ch.fetch_message(pid)
    except (discord.NotFound, discord.HTTPException):
        patrol_message = None

    try:
        aid = state.get("aop_message_id")
        acid = state.get("aop_channel_id")
        if aid and acid:
            ch = bot.get_channel(acid)
            if ch:
                aop_message = await ch.fetch_message(aid)
    except (discord.NotFound, discord.HTTPException):
        aop_message = None

    try:
        anid = state.get("announcement_message_id")
        ancid = state.get("announcement_channel_id")
        if anid and ancid:
            ch = bot.get_channel(ancid)
            if ch:
                announcement_message = await ch.fetch_message(anid)
    except (discord.NotFound, discord.HTTPException):
        announcement_message = None

    # Re-register persistent views so buttons work after restart
    if patrol_message:
        bot.add_view(PatrolView(), message_id=patrol_message.id)
    if aop_message:
        bot.add_view(AOPView(), message_id=aop_message.id)


def get_inactive_reason(user_id):
    cursor.execute("SELECT action, day FROM activity_log WHERE user_id = ? ORDER BY day DESC", (user_id,))
    rows = cursor.fetchall()

    if not rows:
        return "No activity ever"

    last_date = datetime.datetime.strptime(rows[0][1], "%Y-%m-%d").date()
    days_ago = (datetime.datetime.now(TIMEZONE).date() - last_date).days

    actions = [r[0] for r in rows]
    recent_actions = [r[0] for r in rows[:20]]

    only_cant_make = all(a == "cant_make" for a in recent_actions)
    mostly_cant_make = recent_actions.count("cant_make") > len(recent_actions) * 0.7

    if only_cant_make:
        return f"Only marks can't make it (last: {days_ago}d ago)"
    elif mostly_cant_make:
        return f"Mostly marks can't make it (last: {days_ago}d ago)"
    else:
        return f"Stopped responding ({days_ago}d ago)"


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

patrol_message = None
aop_message = None
patrol_embed_title = "🚓 Patrol Attendance"
aop_embed_title = "🗺️ AOP Voting"
voting_open = False
announcement_message = None

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
"City of Toronto",
"Peel Region",
"York Region",
"Durham Region",
"Halton Region",
"City of Hamilton",
]


# ---------------- HELPERS ----------------

def make_bar(count, total, length=8):
    filled = round(count / total * length) if total > 0 else 0
    return "▓" * filled + "░" * (length - filled)


def styled_embed(title, description=None, color=discord.Color.blue()):
    embed = discord.Embed(title=title, description=description, color=color)
    embed.timestamp = datetime.datetime.now(TIMEZONE)
    embed.set_footer(text="Patrol Bot")
    return embed


# ---------------- AUTOCOMPLETES ----------------

async def time_autocomplete(interaction: discord.Interaction, current: str):
    return [
        discord.app_commands.Choice(name=t, value=t)
        for t in time_slots if current.lower() in t.lower()
    ]


async def area_autocomplete(interaction: discord.Interaction, current: str):
    options = mapLC + mapLS
    return [
        discord.app_commands.Choice(name=a, value=a)
        for a in options if current.lower() in a.lower()
    ][:25]


async def current_map_area_autocomplete(interaction: discord.Interaction, current: str):
    options = mapLC if current_map == "LC" else mapLS
    return [
        discord.app_commands.Choice(name=a, value=a)
        for a in options if current.lower() in a.lower()
    ]


# ---------------- LIVE STATS ----------------

def build_patrol_embed(title="🚓 Patrol Attendance"):

    total = len(patrol_votes)
    status = "✅ Minimum reached!" if total >= MINIMUM_PATROL else f"⏳ Need {MINIMUM_PATROL - total} more"

    desc = f"Vote for tonight's patrol start time.\n{status}\n\n"

    slot_voters = {time: [] for time in time_slots}
    for user_id, time in patrol_votes.items():
        slot_voters[time].append(user_id)

    for time in time_slots:
        voters = slot_voters[time]
        count = len(voters)
        bar = make_bar(count, max(total, 1))
        if voters:
            mentions = ", ".join(f"<@{uid}>" for uid in voters)
            desc += f"🕐 **{time}**\n{bar} `{count}` — {mentions}\n\n"
        else:
            desc += f"🕐 **{time}**\n{bar} `0`\n\n"

    if cant_make_votes:
        mentions = ", ".join(f"<@{uid}>" for uid in cant_make_votes)
        desc += f"━━━━━━━━━━━━━━━━━━\n❌ **Can't Make It** (`{len(cant_make_votes)}`): {mentions}\n"

    desc += f"\n👥 **Total Attending:** `{total}` / `{MINIMUM_PATROL}` minimum"

    embed = styled_embed(title, desc, discord.Color.blue())
    return embed


def build_aop_embed(title="🗺️ AOP Voting"):

    map_name = "Liberty City" if current_map == "LC" else "Los Santos"
    desc = f"Vote for tonight's patrol area.\n📍 **Current Map:** {map_name}\n\n"

    options = mapLC if current_map == "LC" else mapLS
    total = len(aop_votes)
    area_counts = {area: 0 for area in options}
    for area in aop_votes.values():
        if area in area_counts:
            area_counts[area] += 1

    leader = max(area_counts, key=area_counts.get) if total > 0 else None

    for area in options:
        count = area_counts[area]
        pct = (count / total * 100) if total > 0 else 0
        bar = make_bar(count, max(total, 1))
        marker = " 👑" if area == leader else ""
        desc += f"📌 **{area}**{marker}\n{bar} `{count}` votes ({pct:.0f}%)\n\n"

    desc += f"━━━━━━━━━━━━━━━━━━\n🗳️ **Total Votes:** `{total}`"

    embed = styled_embed(title, desc, discord.Color.purple())
    return embed


async def update_patrol_message():
    if patrol_message:
        await patrol_message.edit(embed=build_patrol_embed(patrol_embed_title))


async def update_aop_message():
    if aop_message:
        await aop_message.edit(embed=build_aop_embed(aop_embed_title))


async def lock_voting():
    global voting_open
    voting_open = False

    if patrol_message:
        view = discord.ui.View.from_message(patrol_message)
        for item in view.children:
            item.disabled = True
        await patrol_message.edit(view=view)

    if aop_message:
        view = discord.ui.View.from_message(aop_message)
        for item in view.children:
            item.disabled = True
        await aop_message.edit(view=view)

    save_session()


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
            style=discord.ButtonStyle.primary,
            custom_id=f"patrol_{label}",
        )

        self.time = label

    async def callback(self, interaction: discord.Interaction):

        if not voting_open:
            await interaction.response.send_message("Voting is closed.", ephemeral=True)
            return

        is_new = interaction.user.id not in patrol_votes and interaction.user.id not in cant_make_votes
        cant_make_votes.discard(interaction.user.id)
        patrol_votes[interaction.user.id] = self.time
        if is_new:
            record_stat(interaction.user.id, "patrol_votes")
            log_activity(interaction.user.id, "patrol_vote")
        save_session()

        await interaction.response.send_message(f"You voted for **{self.time}**.", ephemeral=True)
        await update_patrol_message()


class CantMakeButton(discord.ui.Button):

    def __init__(self):

        super().__init__(
            label="Can't Make It",
            emoji="❌",
            style=discord.ButtonStyle.danger,
            custom_id="patrol_cant_make",
        )

    async def callback(self, interaction: discord.Interaction):

        if not voting_open:
            await interaction.response.send_message("Voting is closed.", ephemeral=True)
            return

        is_new = interaction.user.id not in cant_make_votes and interaction.user.id not in patrol_votes
        patrol_votes.pop(interaction.user.id, None)
        cant_make_votes.add(interaction.user.id)
        if is_new:
            record_stat(interaction.user.id, "cant_make")
            log_activity(interaction.user.id, "cant_make")
        save_session()

        await interaction.response.send_message("You marked **Can't Make It**.", ephemeral=True)
        await update_patrol_message()


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
            style=discord.ButtonStyle.secondary,
            custom_id=f"aop_{label}",
        )

        self.option = label

    async def callback(self, interaction: discord.Interaction):

        if not voting_open:
            await interaction.response.send_message("Voting is closed.", ephemeral=True)
            return

        is_new = interaction.user.id not in aop_votes
        aop_votes[interaction.user.id] = self.option
        if is_new:
            record_stat(interaction.user.id, "aop_votes")
            log_activity(interaction.user.id, "aop_vote")
        save_session()

        await interaction.response.send_message(f"You voted for **{self.option}**.", ephemeral=True)
        await update_aop_message()


# ---------------- BOT READY ----------------

@bot.event
async def on_ready():

    print("Bot Online")

    await load_session()
    print(f"Session restored: voting_open={voting_open}, patrol_votes={len(patrol_votes)}, aop_votes={len(aop_votes)}")

    scheduler.start()
    close_votes.start()
    briefing_reminder.start()
    stats_checker.start()
    inactivity_checker.start()

    tree.copy_global_to(guild=discord.Object(id=GUILD_ID))
    await tree.sync(guild=discord.Object(id=GUILD_ID))


# ---------------- SCHEDULER ----------------

@tasks.loop(minutes=1)
async def scheduler():

    now = datetime.datetime.now(TIMEZONE)

    if now.hour == 8 and now.minute == 0:

        global confirmed_start_time, patrol_message, aop_message, patrol_embed_title, aop_embed_title, voting_open
        patrol_votes.clear()
        cant_make_votes.clear()
        aop_votes.clear()
        confirmed_start_time = None
        patrol_embed_title = "🚓 Patrol Attendance"
        aop_embed_title = "🗺️ AOP Voting"
        voting_open = True

        patrol_channel = bot.get_channel(PATROL_CHANNEL_ID)
        aop_channel = bot.get_channel(AOP_CHANNEL_ID)

        role = f"<@&{PING_ROLE_ID}>"

        patrol_message = await patrol_channel.send(role, embed=build_patrol_embed(patrol_embed_title), view=PatrolView())
        aop_message = await aop_channel.send(embed=build_aop_embed(aop_embed_title), view=AOPView())
        save_session()


# ---------------- CLOSE VOTES ----------------

@tasks.loop(minutes=1)
async def close_votes():

    now = datetime.datetime.now(TIMEZONE)

    if now.hour == 18 and now.minute == 30:

        await lock_voting()

        patrol_channel = bot.get_channel(PATROL_CHANNEL_ID)
        announcement_channel = bot.get_channel(ANNOUNCEMENT_CHANNEL_ID)

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

            today = now.strftime("%Y-%m-%d")
            cursor.execute("INSERT INTO patrol_days(day, attendance, cancelled, cant_make) VALUES(?, ?, 1, ?)", (today, len(patrol_votes), len(cant_make_votes)))
            conn.commit()

            embed = styled_embed(
                "❌ Patrol Cancelled",
                f"Minimum attendance not reached.\n\n"
                f"👥 **Votes:** `{len(patrol_votes)}` / `{MINIMUM_PATROL}` minimum\n"
                f"❌ **Can't Make It:** `{len(cant_make_votes)}`",
                discord.Color.red()
            )

            await patrol_channel.send(embed=embed)

            if announcement_channel:
                announce = styled_embed(
                    "❌ Tonight's Patrol Has Been Cancelled",
                    f"Not enough members signed up for tonight's patrol.\n\n"
                    f"👥 **Signed Up:** `{len(patrol_votes)}` / `{MINIMUM_PATROL}` minimum\n"
                    f"❌ **Can't Make It:** `{len(cant_make_votes)}`\n\n"
                    f"Better luck next time!",
                    discord.Color.red()
                )
                role = f"<@&{PING_ROLE_ID}>"
                await announcement_channel.send(role, embed=announce)
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
        cursor.execute("INSERT INTO patrol_days(day, attendance, cancelled, cant_make) VALUES(?, ?, 0, ?)", (today, len(patrol_votes), len(cant_make_votes)))
        cursor.execute("INSERT INTO aop_stats(area, day) VALUES(?, ?)", (selected_aop, today))

        for user_id in patrol_votes:
            record_stat(user_id, "patrol_attended")

        for user_id in cant_make_votes:
            record_stat(user_id, "patrol_skipped")

        conn.commit()

        global confirmed_start_time
        confirmed_start_time = start_time

        embed = styled_embed("✅ Patrol Confirmed", color=discord.Color.green())
        embed.add_field(name="🕐 Start Time", value=f"```{start_time}```", inline=True)
        embed.add_field(name="👥 Attending", value=f"```{len(patrol_votes)}```", inline=True)

        await patrol_channel.send(embed=embed)

        if announcement_channel:
            global announcement_message
            announce = styled_embed(
                "🚓 Tonight's Patrol is Confirmed!",
                f"Patrol is happening tonight! Here are the details:\n\n"
                f"🕐 **Start Time:** {start_time}\n"
                f"📍 **AOP:** {selected_aop}\n"
                f"👥 **Members Attending:** {len(patrol_votes)}\n\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"📋 Briefing starts **10 minutes** before in <#{BRIEFING_VOICE_CHANNEL_ID}>",
                discord.Color.green()
            )
            role = f"<@&{PING_ROLE_ID}>"
            announcement_message = await announcement_channel.send(role, embed=announce)

        save_session()


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

        embed = styled_embed(
            "📋 Briefing Reminder",
            f"⏰ Patrol starts in **10 minutes** at **{confirmed_start_time}**\n\n"
            f"🔊 **Join the briefing:** <#{BRIEFING_VOICE_CHANNEL_ID}>\n\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"Make sure you're ready and in the voice channel!",
            discord.Color.orange()
        )

        await channel.send(role, embed=embed)
        confirmed_start_time = None
        save_session()


# ---------------- ADMIN CHECK ----------------

def admin_check(interaction):

    if interaction.channel.id != ADMIN_COMMAND_CHANNEL:
        return False

    return ADMIN_ROLE_ID in [r.id for r in interaction.user.roles]


# ---------------- ADMIN COMMANDS ----------------

@tree.command(name="close_patrol_votes")
async def close_patrol_votes(interaction: discord.Interaction):

    if not admin_check(interaction):
        await interaction.response.send_message("No permission.", ephemeral=True)
        return

    await lock_voting()

    patrol_channel = bot.get_channel(PATROL_CHANNEL_ID)
    announcement_channel = bot.get_channel(ANNOUNCEMENT_CHANNEL_ID)
    now = datetime.datetime.now(TIMEZONE)

    attendance_counts = {time: 0 for time in time_slots}
    for vote in patrol_votes.values():
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
        cursor.execute("INSERT INTO patrol_days(day, attendance, cancelled, cant_make) VALUES(?, ?, 1, ?)", (today, len(patrol_votes), len(cant_make_votes)))
        conn.commit()

        embed = styled_embed(
            "❌ Patrol Cancelled",
            f"Minimum attendance not reached.\n\n"
            f"👥 **Votes:** `{len(patrol_votes)}` / `{MINIMUM_PATROL}` minimum\n"
            f"❌ **Can't Make It:** `{len(cant_make_votes)}`",
            discord.Color.red()
        )

        await patrol_channel.send(embed=embed)

        if announcement_channel:
            announce = styled_embed(
                "❌ Tonight's Patrol Has Been Cancelled",
                f"Not enough members signed up for tonight's patrol.\n\n"
                f"👥 **Signed Up:** `{len(patrol_votes)}` / `{MINIMUM_PATROL}` minimum\n"
                f"❌ **Can't Make It:** `{len(cant_make_votes)}`\n\n"
                f"Better luck next time!",
                discord.Color.red()
            )
            role = f"<@&{PING_ROLE_ID}>"
            await announcement_channel.send(role, embed=announce)

        await interaction.response.send_message("Patrol votes closed — cancelled (not enough votes).", ephemeral=True)
        return

    today = now.strftime("%Y-%m-%d")
    cursor.execute("INSERT INTO patrol_days(day, attendance, cancelled, cant_make) VALUES(?, ?, 0, ?)", (today, len(patrol_votes), len(cant_make_votes)))

    for user_id in patrol_votes:
        record_stat(user_id, "patrol_attended")

    for user_id in cant_make_votes:
        record_stat(user_id, "patrol_skipped")

    conn.commit()

    global confirmed_start_time
    confirmed_start_time = start_time

    embed = styled_embed("✅ Patrol Confirmed", color=discord.Color.green())
    embed.add_field(name="🕐 Start Time", value=f"```{start_time}```", inline=True)
    embed.add_field(name="👥 Attending", value=f"```{len(patrol_votes)}```", inline=True)

    await patrol_channel.send(embed=embed)

    if announcement_channel:
        global announcement_message
        announce = styled_embed(
            "🚓 Tonight's Patrol is Confirmed!",
            f"Patrol is happening tonight! Here are the details:\n\n"
            f"🕐 **Start Time:** {start_time}\n"
            f"👥 **Members Attending:** {len(patrol_votes)}\n\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📋 Briefing starts **10 minutes** before in <#{BRIEFING_VOICE_CHANNEL_ID}>",
            discord.Color.green()
        )
        role = f"<@&{PING_ROLE_ID}>"
        announcement_message = await announcement_channel.send(role, embed=announce)

    save_session()
    await interaction.response.send_message(f"Patrol votes closed — confirmed at {start_time}.", ephemeral=True)


@tree.command(name="close_aop_votes")
async def close_aop_votes(interaction: discord.Interaction):

    if not admin_check(interaction):
        await interaction.response.send_message("No permission.", ephemeral=True)
        return

    global announcement_message

    now = datetime.datetime.now(TIMEZONE)
    today = now.strftime("%Y-%m-%d")

    if not aop_votes:
        options = mapLC if current_map == "LC" else mapLS
        selected_aop = random.choice(options)
    else:
        counts = {}
        for vote in aop_votes.values():
            counts[vote] = counts.get(vote, 0) + 1
        selected_aop = max(counts, key=counts.get)

    cursor.execute("INSERT INTO aop_stats(area, day) VALUES(?, ?)", (selected_aop, today))
    conn.commit()

    embed = styled_embed(
        "🗺️ AOP Result",
        f"📍 Tonight's AOP: **{selected_aop}**\n\n"
        f"🗳️ **Total Votes:** `{len(aop_votes)}`",
        discord.Color.purple()
    )

    await bot.get_channel(AOP_CHANNEL_ID).send(embed=embed)

    if announcement_message:
        old_embed = announcement_message.embeds[0]
        desc = old_embed.description or ""
        lines = desc.split("\n")
        new_lines = []
        for line in lines:
            if line.startswith("📍"):
                new_lines.append(f"📍 **AOP:** {selected_aop}")
            else:
                new_lines.append(line)
        if not any(l.startswith("📍") for l in lines):
            for i, line in enumerate(new_lines):
                if line.startswith("🕐"):
                    new_lines.insert(i + 1, f"📍 **AOP:** {selected_aop}")
                    break
        new_embed = styled_embed("🚓 Tonight's Patrol is Confirmed!", "\n".join(new_lines), discord.Color.green())
        announcement_message = await announcement_message.edit(embed=new_embed)

    await interaction.response.send_message(f"AOP votes closed — {selected_aop}.", ephemeral=True)


@tree.command(name="start_patrol")
@discord.app_commands.autocomplete(time=time_autocomplete, area=area_autocomplete)
async def start_patrol(interaction: discord.Interaction, time: str, area: str):

    if not admin_check(interaction):
        await interaction.response.send_message("No permission.", ephemeral=True)
        return

    await lock_voting()

    global confirmed_start_time
    confirmed_start_time = time

    patrol_channel = bot.get_channel(PATROL_CHANNEL_ID)
    announcement_channel = bot.get_channel(ANNOUNCEMENT_CHANNEL_ID)

    embed = styled_embed("✅ Patrol Confirmed", "⚡ Forced start by administration.", discord.Color.green())
    embed.add_field(name="🕐 Start Time", value=f"```{time}```", inline=True)
    embed.add_field(name="👥 Attending", value=f"```{len(patrol_votes) if patrol_votes else 'N/A'}```", inline=True)

    now = datetime.datetime.now(TIMEZONE)
    today = now.strftime("%Y-%m-%d")
    cursor.execute("INSERT INTO patrol_days(day, attendance, cancelled, cant_make) VALUES(?, ?, 0, ?)", (today, len(patrol_votes), len(cant_make_votes)))
    cursor.execute("INSERT INTO aop_stats(area, day) VALUES(?, ?)", (area, today))
    conn.commit()

    await patrol_channel.send(embed=embed)

    if announcement_channel:
        global announcement_message
        announce = styled_embed(
            "🚓 Tonight's Patrol is Confirmed!",
            f"Patrol is happening tonight! Here are the details:\n\n"
            f"🕐 **Start Time:** {time}\n"
            f"📍 **AOP:** {area}\n"
            f"👥 **Members Attending:** {len(patrol_votes) if patrol_votes else 'N/A'}\n\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📋 Briefing starts **10 minutes** before in <#{BRIEFING_VOICE_CHANNEL_ID}>",
            discord.Color.green()
        )
        role = f"<@&{PING_ROLE_ID}>"
        announcement_message = await announcement_channel.send(role, embed=announce)

    save_session()
    await interaction.response.send_message(f"Patrol force started at {time}, AOP: {area}.", ephemeral=True)


@tree.command(name="cancel_patrol")
async def cancel_patrol(interaction: discord.Interaction):

    if not admin_check(interaction):
        await interaction.response.send_message("No permission.", ephemeral=True)
        return

    global announcement_message

    await lock_voting()

    embed = styled_embed(
        "❌ Patrol Cancelled",
        "Cancelled by administration.",
        discord.Color.red()
    )

    await bot.get_channel(PATROL_CHANNEL_ID).send(embed=embed)

    announcement_channel = bot.get_channel(ANNOUNCEMENT_CHANNEL_ID)
    if announcement_channel:
        if announcement_message:
            cancel_embed = styled_embed(
                "❌ Tonight's Patrol Has Been Cancelled",
                "Patrol has been cancelled by administration.",
                discord.Color.red()
            )
            announcement_message = await announcement_message.edit(embed=cancel_embed)
        else:
            announce = styled_embed(
                "❌ Tonight's Patrol Has Been Cancelled",
                f"Patrol has been cancelled by administration.\n\n"
                f"👥 **Signed Up:** `{len(patrol_votes)}` / `{MINIMUM_PATROL}` minimum\n"
                f"❌ **Can't Make It:** `{len(cant_make_votes)}`\n\n"
                f"Better luck next time!",
                discord.Color.red()
            )
            role = f"<@&{PING_ROLE_ID}>"
            await announcement_channel.send(role, embed=announce)

    now = datetime.datetime.now(TIMEZONE)
    today = now.strftime("%Y-%m-%d")
    cursor.execute("INSERT INTO patrol_days(day, attendance, cancelled, cant_make) VALUES(?, ?, 1, ?)", (today, len(patrol_votes), len(cant_make_votes)))
    conn.commit()

    save_session()
    await interaction.response.send_message("Patrol cancelled.", ephemeral=True)


@tree.command(name="open_votes")
async def open_votes(interaction: discord.Interaction):

    if not admin_check(interaction):
        await interaction.response.send_message("No permission.", ephemeral=True)
        return

    global patrol_message, aop_message, patrol_embed_title, aop_embed_title, voting_open
    patrol_votes.clear()
    cant_make_votes.clear()
    aop_votes.clear()
    voting_open = True
    patrol_embed_title = "🚓 Patrol Attendance"
    aop_embed_title = "🗺️ AOP Voting"

    patrol_channel = bot.get_channel(PATROL_CHANNEL_ID)
    aop_channel = bot.get_channel(AOP_CHANNEL_ID)
    role = f"<@&{PING_ROLE_ID}>"

    patrol_message = await patrol_channel.send(role, embed=build_patrol_embed(patrol_embed_title), view=PatrolView())
    aop_message = await aop_channel.send(embed=build_aop_embed(aop_embed_title), view=AOPView())
    save_session()
    await interaction.response.send_message("Patrol and AOP voting opened.", ephemeral=True)


@tree.command(name="open_patrol_vote")
async def open_patrol_vote(interaction: discord.Interaction):

    if not admin_check(interaction):
        await interaction.response.send_message("No permission.", ephemeral=True)
        return

    global patrol_message, patrol_embed_title, voting_open
    patrol_votes.clear()
    cant_make_votes.clear()
    voting_open = True
    patrol_embed_title = "🚓 Patrol Attendance"

    patrol_channel = bot.get_channel(PATROL_CHANNEL_ID)
    role = f"<@&{PING_ROLE_ID}>"

    patrol_message = await patrol_channel.send(role, embed=build_patrol_embed(patrol_embed_title), view=PatrolView())
    save_session()
    await interaction.response.send_message("Patrol voting opened.", ephemeral=True)


@tree.command(name="open_aop_vote")
async def open_aop_vote(interaction: discord.Interaction):

    if not admin_check(interaction):
        await interaction.response.send_message("No permission.", ephemeral=True)
        return

    global aop_message, aop_embed_title, voting_open
    aop_votes.clear()
    voting_open = True
    aop_embed_title = "🗺️ AOP Voting"

    aop_channel = bot.get_channel(AOP_CHANNEL_ID)

    aop_message = await aop_channel.send(embed=build_aop_embed(aop_embed_title), view=AOPView())
    save_session()
    await interaction.response.send_message("AOP voting opened.", ephemeral=True)


@tree.command(name="override_patrol_time")
@discord.app_commands.autocomplete(time=time_autocomplete)
async def override_patrol_time(interaction: discord.Interaction, time: str):

    if not admin_check(interaction):
        await interaction.response.send_message("No permission.", ephemeral=True)
        return

    global confirmed_start_time, announcement_message
    confirmed_start_time = time

    embed = styled_embed(
        "⚠️ Patrol Override",
        f"🕐 Patrol will begin at **{time}**",
        discord.Color.gold()
    )

    await bot.get_channel(PATROL_CHANNEL_ID).send(embed=embed)

    if announcement_message:
        old_embed = announcement_message.embeds[0]
        desc = old_embed.description or ""
        desc = "\n".join(
            f"🕐 **Start Time:** {time}" if line.startswith("🕐") else line
            for line in desc.split("\n")
        )
        new_embed = styled_embed("🚓 Tonight's Patrol is Confirmed!", desc, discord.Color.green())
        announcement_message = await announcement_message.edit(embed=new_embed)

    save_session()
    await interaction.response.send_message("Override sent.", ephemeral=True)


@tree.command(name="override_aop")
@discord.app_commands.autocomplete(area=current_map_area_autocomplete)
async def override_aop(interaction: discord.Interaction, area: str):

    if not admin_check(interaction):
        await interaction.response.send_message("No permission.", ephemeral=True)
        return

    global announcement_message

    embed = styled_embed(
        "⚠️ AOP Override",
        f"📍 AOP has been set to **{area}**",
        discord.Color.gold()
    )

    await bot.get_channel(AOP_CHANNEL_ID).send(embed=embed)

    if announcement_message:
        old_embed = announcement_message.embeds[0]
        desc = old_embed.description or ""
        lines = desc.split("\n")
        new_lines = []
        for line in lines:
            if line.startswith("📍"):
                new_lines.append(f"📍 **AOP:** {area}")
            else:
                new_lines.append(line)
        # If no AOP line existed, add it after start time
        if not any(l.startswith("📍") for l in lines):
            for i, line in enumerate(new_lines):
                if line.startswith("🕐"):
                    new_lines.insert(i + 1, f"📍 **AOP:** {area}")
                    break
        new_embed = styled_embed("🚓 Tonight's Patrol is Confirmed!", "\n".join(new_lines), discord.Color.green())
        announcement_message = await announcement_message.edit(embed=new_embed)

    await interaction.response.send_message("AOP override sent.", ephemeral=True)


@tree.command(name="force_stats")
async def force_stats(interaction: discord.Interaction):

    if not admin_check(interaction):
        await interaction.response.send_message("No permission.", ephemeral=True)
        return

    await interaction.response.send_message("Posting stats leaderboard...", ephemeral=True)
    await post_stats_leaderboard()


@tree.command(name="user_stats")
async def user_stats(interaction: discord.Interaction, member: discord.Member, period: Period = Period.all_time):

    if not admin_check(interaction):
        await interaction.response.send_message("No permission.", ephemeral=True)
        return

    cutoff = get_cutoff(period)

    if cutoff:
        cursor.execute(
            "SELECT "
            "COALESCE(SUM(CASE WHEN action = 'patrol_vote' THEN 1 ELSE 0 END), 0), "
            "COALESCE(SUM(CASE WHEN action = 'cant_make' THEN 1 ELSE 0 END), 0), "
            "COALESCE(SUM(CASE WHEN action = 'aop_vote' THEN 1 ELSE 0 END), 0) "
            "FROM activity_log WHERE user_id = ? AND day >= ?",
            (member.id, cutoff)
        )
        row = cursor.fetchone()
        p_votes, cant, a_votes = row
        p_attended = p_votes
        p_skip = cant
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

    # Last activity
    cursor.execute("SELECT day, action FROM activity_log WHERE user_id = ? ORDER BY day DESC LIMIT 1", (member.id,))
    last_row = cursor.fetchone()
    last_activity = f"{last_row[0]} ({last_row[1].replace('_', ' ')})" if last_row else "Never"

    # Days since last activity
    if last_row:
        last_date = datetime.datetime.strptime(last_row[0], "%Y-%m-%d").date()
        days_ago = (datetime.datetime.now(TIMEZONE).date() - last_date).days
        last_activity += f" ({days_ago}d ago)"

    color = member.color if member.color != discord.Color.default() else discord.Color.blue()
    embed = styled_embed(f"📋 Stats for {member.display_name}", f"**Period:** {period_label(period)}", color)

    embed.set_thumbnail(url=member.display_avatar.url)

    rate_bar = make_bar(int(attend_rate), 100, 10)

    embed.add_field(name="🗳️ Patrol Votes", value=f"```{p_votes}```", inline=True)
    embed.add_field(name="✅ Attended", value=f"```{p_attended}```", inline=True)
    embed.add_field(name="📊 Attendance", value=f"```{attend_rate:.0f}%```\n{rate_bar}", inline=True)
    embed.add_field(name="📍 AOP Votes", value=f"```{a_votes}```", inline=True)
    embed.add_field(name="❌ Can't Make It", value=f"```{cant}```", inline=True)
    embed.add_field(name="⏭️ Skipped", value=f"```{p_skip}```", inline=True)
    embed.add_field(name="━━━━━━━━━━━━━━━━━━", value=f"📅 **Last Activity:** {last_activity}", inline=False)

    await interaction.response.send_message(embed=embed)


@tree.command(name="check_inactive")
async def check_inactive(interaction: discord.Interaction):

    if not admin_check(interaction):
        await interaction.response.send_message("No permission.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    now = datetime.datetime.now(TIMEZONE)
    two_weeks_ago = (now.date() - datetime.timedelta(days=14)).strftime("%Y-%m-%d")

    cursor.execute("SELECT DISTINCT user_id FROM activity_log WHERE day >= ?", (two_weeks_ago,))
    active_users = {r[0] for r in cursor.fetchall()}

    guild = bot.get_guild(GUILD_ID)
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
        lines.append(f"**{i}. {m.display_name}** (<@{m.id}>) — {reason}")

    await send_paginated(interaction.channel, f"⚠️ Inactive Members ({len(inactive)} total)", lines, discord.Color.red())
    await interaction.followup.send(f"Found **{len(inactive)}** inactive members.", ephemeral=True)


def get_cutoff(period: Period):
    if period == Period.last_2_weeks:
        return (datetime.datetime.now(TIMEZONE).date() - datetime.timedelta(days=14)).strftime("%Y-%m-%d")
    return None


def period_label(period: Period):
    return period.value


@tree.command(name="server_stats")
async def server_stats(interaction: discord.Interaction, period: Period = Period.all_time):

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
        day_lines.append(f"{bar} **{name}** — `{count}` patrols, avg `{avg:.1f}` members")

    total_patrols = len(patrol_rows)
    all_attendance = [a for _, a, _, _ in patrol_rows]
    avg_attendance = sum(all_attendance) / total_patrols
    highest = max(all_attendance)
    lowest = min(all_attendance)

    embed = styled_embed(f"📈 Server Statistics", f"**Period:** {period_label(period)}", discord.Color.teal())

    embed.add_field(
        name="📊 Overview",
        value=(
            f"🚓 Total Patrols: `{total_patrols}`\n"
            f"👥 Tracked Members: `{total_members}`\n"
            f"📈 Avg Attendance: `{avg_attendance:.1f}`\n"
            f"⬆️ Highest: `{highest}` · ⬇️ Lowest: `{lowest}`"
        ),
        inline=False
    )

    embed.add_field(
        name="📅 Patrols by Day of Week",
        value="\n".join(day_lines) if day_lines else "No data",
        inline=False
    )

    if inactive_days:
        embed.add_field(
            name="🚫 Days With No Patrols",
            value=", ".join(inactive_days),
            inline=False
        )

    await interaction.response.send_message(embed=embed)


@tree.command(name="activity_stats")
async def activity_stats(interaction: discord.Interaction, period: Period = Period.all_time):

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

    embed = styled_embed(f"📉 Activity & No-Show Stats", f"**Period:** {period_label(period)}", discord.Color.orange())

    embed.add_field(
        name="📊 Overview",
        value=(
            f"🚓 Total Patrols: `{total_patrols}` (`{total_active}` active, `{total_cancelled}` cancelled)\n\n"
            f"❌ **Cancellation Rate:** `{cancel_pct:.0f}%`\n{cancel_bar}\n\n"
            f"👻 **No-Show Rate:** `{noshow_pct:.0f}%` (`{total_cant_make}` / `{total_responses}` responses)\n{noshow_bar}"
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
        day_lines.append(f"**{name}**\n{c_bar} `{cancel_rate:.0f}%` cancelled · `{rate:.0f}%` no-show")

    embed.add_field(
        name="📅 Breakdown by Day of Week",
        value="\n".join(day_lines) if day_lines else "No data",
        inline=False
    )

    await interaction.response.send_message(embed=embed)


@tree.command(name="aop_breakdown")
async def aop_breakdown(interaction: discord.Interaction, period: Period = Period.all_time):

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
            medal = ["🥇", "🥈", "🥉"][i] if i < 3 else "📌"
            aop_lines.append(f"{medal} **{area}**\n{bar} `{count}` times ({pct:.0f}%)")

        # Unused areas
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
            aop_day_lines.append(f"📅 **{day}** → **{top_area}** (`{areas[top_area]}` times)")

        embed = styled_embed(f"🗺️ AOP Breakdown — {map_name}", f"**Period:** {period_label(period)}\n**Total Patrols:** `{total}`", discord.Color.purple())

        embed.add_field(
            name="🏆 Area Popularity",
            value="\n\n".join(aop_lines),
            inline=False
        )

        if unused:
            embed.add_field(
                name="🚫 Never Selected",
                value=", ".join(unused),
                inline=False
            )

        embed.add_field(
            name="📅 Most Popular AOP per Day",
            value="\n".join(aop_day_lines) if aop_day_lines else "No data",
            inline=False
        )

        embeds.append(embed)

    await interaction.response.send_message(embeds=embeds)


@tree.command(name="maplc")
async def map_lc(interaction: discord.Interaction):

    global current_map, aop_message

    if not admin_check(interaction):
        return

    current_map = "LC"

    msg = "Map switched to LC."
    if voting_open and aop_message:
        aop_votes.clear()
        new_view = AOPView()
        await aop_message.edit(embed=build_aop_embed(aop_embed_title), view=new_view)
        msg += " AOP votes have been reset for the new map."

    save_session()
    await interaction.response.send_message(msg)


@tree.command(name="mapls")
async def map_ls(interaction: discord.Interaction):

    global current_map, aop_message

    if not admin_check(interaction):
        return

    current_map = "LS"

    msg = "Map switched to LS."
    if voting_open and aop_message:
        aop_votes.clear()
        new_view = AOPView()
        await aop_message.edit(embed=build_aop_embed(aop_embed_title), view=new_view)
        msg += " AOP votes have been reset for the new map."

    save_session()
    await interaction.response.send_message(msg)


# ---------------- PAGINATION ----------------

def paginate_lines(lines, max_length=4000):
    pages = []
    current = []
    length = 0

    for line in lines:
        line_len = len(line) + 2
        if length + line_len > max_length and current:
            pages.append("\n\n".join(current))
            current = []
            length = 0
        current.append(line)
        length += line_len

    if current:
        pages.append("\n\n".join(current))

    return pages


async def send_paginated(channel, title, lines, color):
    pages = paginate_lines(lines)

    for i, page in enumerate(pages):
        suffix = f" (Page {i + 1}/{len(pages)})" if len(pages) > 1 else ""
        embed = styled_embed(f"{title}{suffix}", page, color)
        await channel.send(embed=embed)


# ---------------- STATS LEADERBOARD ----------------

async def post_stats_leaderboard():

    cursor.execute("SELECT user_id, patrol_votes, patrol_attended, aop_votes, cant_make, patrol_skipped, aop_skipped FROM members ORDER BY patrol_attended DESC")
    rows = cursor.fetchall()

    if not rows:
        return

    guild = bot.get_guild(GUILD_ID)
    channel = bot.get_channel(STATS_CHANNEL_ID)

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

    await send_paginated(channel, "📊 Biweekly Stats Leaderboard", lines, discord.Color.blue())


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


# ---------------- INACTIVITY CHECKER ----------------

@tasks.loop(minutes=1)
async def inactivity_checker():

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

    guild = bot.get_guild(GUILD_ID)
    ping_role = guild.get_role(PING_ROLE_ID)

    if not ping_role:
        return

    inactive = [m for m in ping_role.members if not m.bot and m.id not in active_users]

    if not inactive:
        return

    lines = [f"**{i}. {m.display_name}** (<@{m.id}>) — {get_inactive_reason(m.id)}" for i, m in enumerate(inactive, 1)]

    channel = bot.get_channel(STATS_CHANNEL_ID)

    today = now.strftime("%Y-%m-%d")
    cursor.execute("INSERT OR REPLACE INTO settings(key, value) VALUES('last_inactivity_post', ?)", (today,))
    conn.commit()

    await send_paginated(channel, "⚠️ Inactive Members (Last 2 Weeks)", lines, discord.Color.red())


# ---------------- TEST COMMANDS ----------------

@tree.command(name="test_patrol_vote")
async def test_patrol_vote(interaction: discord.Interaction):

    if not admin_check(interaction):
        await interaction.response.send_message("No permission.", ephemeral=True)
        return

    global patrol_message, patrol_embed_title, voting_open
    patrol_embed_title = "🚓 Patrol Attendance (TEST)"
    voting_open = True
    patrol_channel = bot.get_channel(PATROL_CHANNEL_ID)

    role = f"<@&{PING_ROLE_ID}>"
    patrol_message = await patrol_channel.send(role, embed=build_patrol_embed(patrol_embed_title), view=PatrolView())
    save_session()
    await interaction.response.send_message("Test patrol vote posted.", ephemeral=True)


@tree.command(name="test_aop_vote")
async def test_aop_vote(interaction: discord.Interaction):

    if not admin_check(interaction):
        await interaction.response.send_message("No permission.", ephemeral=True)
        return

    global aop_message, aop_embed_title, voting_open
    aop_embed_title = "🗺️ AOP Voting (TEST)"
    voting_open = True
    aop_channel = bot.get_channel(AOP_CHANNEL_ID)

    aop_message = await aop_channel.send(embed=build_aop_embed(aop_embed_title), view=AOPView())
    save_session()
    await interaction.response.send_message("Test AOP vote posted.", ephemeral=True)


@tree.command(name="test_close_votes")
async def test_close_votes(interaction: discord.Interaction):

    if not admin_check(interaction):
        await interaction.response.send_message("No permission.", ephemeral=True)
        return

    patrol_channel = bot.get_channel(PATROL_CHANNEL_ID)

    attendance_counts = {time: 0 for time in time_slots}
    for vote in patrol_votes.values():
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
            "❌ Patrol Cancelled (TEST)",
            f"Minimum attendance not reached.\n\n"
            f"👥 **Votes:** `{len(patrol_votes)}` / `{MINIMUM_PATROL}` minimum\n"
            f"❌ **Can't Make It:** `{len(cant_make_votes)}`",
            discord.Color.red()
        )
        await patrol_channel.send(embed=embed)
        await interaction.response.send_message("Test close votes: patrol cancelled (not enough votes).", ephemeral=True)
        return

    if not aop_votes:
        options = mapLC if current_map == "LC" else mapLS
        selected_aop = random.choice(options)
    else:
        counts = {}
        for vote in aop_votes.values():
            counts[vote] = counts.get(vote, 0) + 1
        selected_aop = max(counts, key=counts.get)

    global confirmed_start_time
    confirmed_start_time = start_time

    embed = styled_embed("✅ Patrol Confirmed (TEST)", color=discord.Color.green())
    embed.add_field(name="🕐 Start Time", value=f"```{start_time}```", inline=True)
    embed.add_field(name="👥 Attending", value=f"```{len(patrol_votes)}```", inline=True)

    await patrol_channel.send(embed=embed)
    await interaction.response.send_message(f"Test close votes: patrol confirmed at {start_time}, AOP: {selected_aop}.", ephemeral=True)


@tree.command(name="test_briefing")
async def test_briefing(interaction: discord.Interaction):

    if not admin_check(interaction):
        await interaction.response.send_message("No permission.", ephemeral=True)
        return

    role = f"<@&{PING_ROLE_ID}>"
    channel = bot.get_channel(BRIEFING_CHANNEL_ID)

    time_display = confirmed_start_time or "7:00 PM EST"

    embed = styled_embed(
        "📋 Briefing Reminder (TEST)",
        f"⏰ Patrol starts in **10 minutes** at **{time_display}**\n\n"
        f"🔊 **Join the briefing:** <#{BRIEFING_VOICE_CHANNEL_ID}>\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Make sure you're ready and in the voice channel!",
        discord.Color.orange()
    )

    await channel.send(role, embed=embed)
    await interaction.response.send_message("Test briefing reminder posted.", ephemeral=True)


@tree.command(name="test_cancel")
async def test_cancel(interaction: discord.Interaction):

    if not admin_check(interaction):
        await interaction.response.send_message("No permission.", ephemeral=True)
        return

    embed = styled_embed(
        "❌ Patrol Cancelled (TEST)",
        "Cancelled by administration.",
        discord.Color.red()
    )

    await bot.get_channel(PATROL_CHANNEL_ID).send(embed=embed)
    await interaction.response.send_message("Test cancel posted.", ephemeral=True)


@tree.command(name="test_override_time")
@discord.app_commands.autocomplete(time=time_autocomplete)
async def test_override_time(interaction: discord.Interaction, time: str):

    if not admin_check(interaction):
        await interaction.response.send_message("No permission.", ephemeral=True)
        return

    embed = styled_embed(
        "⚠️ Patrol Override (TEST)",
        f"🕐 Patrol will begin at **{time}**",
        discord.Color.gold()
    )

    await bot.get_channel(PATROL_CHANNEL_ID).send(embed=embed)
    await interaction.response.send_message("Test time override posted.", ephemeral=True)


@tree.command(name="test_override_aop")
@discord.app_commands.autocomplete(area=current_map_area_autocomplete)
async def test_override_aop(interaction: discord.Interaction, area: str):

    if not admin_check(interaction):
        await interaction.response.send_message("No permission.", ephemeral=True)
        return

    embed = styled_embed(
        "⚠️ AOP Override (TEST)",
        f"📍 AOP has been set to **{area}**",
        discord.Color.gold()
    )

    await bot.get_channel(AOP_CHANNEL_ID).send(embed=embed)
    await interaction.response.send_message("Test AOP override posted.", ephemeral=True)


@tree.command(name="test_fake_data")
async def test_fake_data(interaction: discord.Interaction, days: int = 30):

    if not admin_check(interaction):
        await interaction.response.send_message("No permission.", ephemeral=True)
        return

    guild = bot.get_guild(GUILD_ID)
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
        not_coming = random.sample([m for m in members if m not in attending], k=min(random.randint(0, 3), len(members) - len(attending)))

        cancelled = 1 if len(attending) < MINIMUM_PATROL else 0

        cursor.execute(
            "INSERT INTO patrol_days(day, attendance, cancelled, cant_make) VALUES(?, ?, ?, ?)",
            (day_str, len(attending), cancelled, len(not_coming))
        )

        if not cancelled:
            area = random.choice(options)
            cursor.execute("INSERT INTO aop_stats(area, day) VALUES(?, ?)", (area, day_str))

        for m in attending:
            ensure_member(m.id)
            cursor.execute("UPDATE members SET patrol_votes = patrol_votes + 1, patrol_attended = patrol_attended + 1 WHERE user_id = ?", (m.id,))

        for m in not_coming:
            ensure_member(m.id)
            cursor.execute("UPDATE members SET cant_make = cant_make + 1, patrol_skipped = patrol_skipped + 1 WHERE user_id = ?", (m.id,))

        for m in random.sample(members, k=random.randint(1, min(len(members), 6))):
            ensure_member(m.id)
            cursor.execute("UPDATE members SET aop_votes = aop_votes + 1 WHERE user_id = ?", (m.id,))

        patrols_added += 1

    conn.commit()

    await interaction.followup.send(f"Added **{patrols_added}** fake patrol days.", ephemeral=True)


@tree.command(name="test_clear_data")
async def test_clear_data(interaction: discord.Interaction):

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


@tree.command(name="test_inactivity")
async def test_inactivity(interaction: discord.Interaction):

    if not admin_check(interaction):
        await interaction.response.send_message("No permission.", ephemeral=True)
        return

    now = datetime.datetime.now(TIMEZONE)
    two_weeks_ago = (now.date() - datetime.timedelta(days=14)).strftime("%Y-%m-%d")

    cursor.execute("SELECT DISTINCT user_id FROM activity_log WHERE day >= ?", (two_weeks_ago,))
    active_users = {r[0] for r in cursor.fetchall()}

    guild = bot.get_guild(GUILD_ID)
    ping_role = guild.get_role(PING_ROLE_ID)

    if not ping_role:
        await interaction.response.send_message("Ping role not found.", ephemeral=True)
        return

    inactive = [m for m in ping_role.members if not m.bot and m.id not in active_users]

    if not inactive:
        await interaction.response.send_message("No inactive members.", ephemeral=True)
        return

    lines = [f"**{i}. {m.display_name}** (<@{m.id}>) — {get_inactive_reason(m.id)}" for i, m in enumerate(inactive, 1)]

    await interaction.response.defer(ephemeral=True)
    await send_paginated(interaction.channel, "⚠️ Inactive Members (TEST)", lines, discord.Color.red())
    await interaction.followup.send(f"Found {len(inactive)} inactive members.", ephemeral=True)



bot.run(TOKEN)
