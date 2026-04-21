import datetime
import json
import re

import discord

import state
from config import (
    time_slots, mapLC, mapLS, MINIMUM_PATROL,
    ADMIN_COMMAND_CHANNEL, ADMIN_ROLE_ID,
)
from database import cursor, conn


# ---------------- UTILITIES ----------------

def make_bar(count, total, length=8):
    filled = round(count / total * length) if total > 0 else 0
    return "\u2593" * filled + "\u2591" * (length - filled)


def styled_embed(title, description=None, color=discord.Color.blue()):
    from config import TIMEZONE
    embed = discord.Embed(title=title, description=description, color=color)
    embed.timestamp = datetime.datetime.now(TIMEZONE)
    embed.set_footer(text="Patrol Bot")
    return embed


# ---------------- EMBED BUILDERS ----------------

def build_patrol_embed(title="\U0001f693 Patrol Attendance"):
    total = len(state.patrol_votes)
    status = "\u2705 Minimum reached!" if total >= MINIMUM_PATROL else f"\u23f3 Need {MINIMUM_PATROL - total} more"

    desc = f"Vote for tonight's patrol start time.\n{status}\n\n"

    slot_voters = {time: [] for time in time_slots}
    for user_id, time in state.patrol_votes.items():
        slot_voters[time].append(user_id)

    for time in time_slots:
        voters = slot_voters[time]
        count = len(voters)
        bar = make_bar(count, max(total, 1))
        if voters:
            mentions = ", ".join(f"<@{uid}>" for uid in voters)
            desc += f"\U0001f550 **{time}**\n{bar} `{count}` \u2014 {mentions}\n\n"
        else:
            desc += f"\U0001f550 **{time}**\n{bar} `0`\n\n"

    if state.cant_make_votes:
        mentions = ", ".join(f"<@{uid}>" for uid in state.cant_make_votes)
        desc += f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n\u274c **Can't Make It** (`{len(state.cant_make_votes)}`): {mentions}\n"

    desc += f"\n\U0001f465 **Total Attending:** `{total}` / `{MINIMUM_PATROL}` minimum"

    embed = styled_embed(title, desc, discord.Color.blue())
    return embed


def build_aop_embed(title="\U0001f5fa\ufe0f AOP Voting"):
    map_name = "Liberty City" if state.current_map == "LC" else "Los Santos"
    desc = f"Vote for tonight's patrol area.\n\U0001f4cd **Current Map:** {map_name}\n\n"

    options = mapLC if state.current_map == "LC" else mapLS
    total = len(state.aop_votes)
    area_counts = {area: 0 for area in options}
    for area in state.aop_votes.values():
        if area in area_counts:
            area_counts[area] += 1

    leader = max(area_counts, key=area_counts.get) if total > 0 else None

    for area in options:
        count = area_counts[area]
        pct = (count / total * 100) if total > 0 else 0
        bar = make_bar(count, max(total, 1))
        marker = " \U0001f451" if area == leader else ""
        desc += f"\U0001f4cc **{area}**{marker}\n{bar} `{count}` votes ({pct:.0f}%)\n\n"

    desc += f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n\U0001f5f3\ufe0f **Total Votes:** `{total}`"

    embed = styled_embed(title, desc, discord.Color.purple())
    return embed


async def update_patrol_message():
    if state.patrol_message:
        await state.patrol_message.edit(embed=build_patrol_embed(state.patrol_embed_title))


async def update_aop_message():
    if state.aop_message:
        await state.aop_message.edit(embed=build_aop_embed(state.aop_embed_title))


async def lock_voting():
    state.voting_open = False

    if state.patrol_message:
        view = discord.ui.View.from_message(state.patrol_message)
        for item in view.children:
            item.disabled = True
        await state.patrol_message.edit(view=view)

    if state.aop_message:
        view = discord.ui.View.from_message(state.aop_message)
        for item in view.children:
            item.disabled = True
        await state.aop_message.edit(view=view)

    state.save_session()


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
    options = mapLC if state.current_map == "LC" else mapLS
    return [
        discord.app_commands.Choice(name=a, value=a)
        for a in options if current.lower() in a.lower()
    ]


# ---------------- ADMIN CHECK ----------------

def admin_check(interaction):
    if interaction.channel.id != ADMIN_COMMAND_CHANNEL:
        return False
    return ADMIN_ROLE_ID in [r.id for r in interaction.user.roles]


# ---------------- PAGINATION ----------------

PAGINATED_KEY_PREFIX = "paginated_"


def paginate_lines(lines, max_length=1800):
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


def _build_page_embeds(title, lines, color):
    pages = paginate_lines(lines)
    total = len(pages)
    return [
        styled_embed(
            f"{title}" + (f" (Page {i + 1}/{total})" if total > 1 else ""),
            page,
            color,
        )
        for i, page in enumerate(pages)
    ], total


def _save_paginated(kind, message, title, color, lines):
    data = {
        "message_id": message.id,
        "channel_id": message.channel.id,
        "title": title,
        "color": color.value,
        "lines": lines,
    }
    cursor.execute(
        "INSERT OR REPLACE INTO settings(key, value) VALUES(?, ?)",
        (PAGINATED_KEY_PREFIX + kind, json.dumps(data)),
    )
    conn.commit()


def _load_paginated(kind):
    cursor.execute("SELECT value FROM settings WHERE key = ?", (PAGINATED_KEY_PREFIX + kind,))
    row = cursor.fetchone()
    if not row:
        return None
    return json.loads(row[0])


def _clear_paginated(kind):
    cursor.execute("DELETE FROM settings WHERE key = ?", (PAGINATED_KEY_PREFIX + kind,))
    conn.commit()


async def _disable_previous_paginator(bot, kind):
    data = _load_paginated(kind)
    if not data:
        return
    try:
        channel = bot.get_channel(data["channel_id"])
        if not channel:
            return
        message = await channel.fetch_message(data["message_id"])
        view = discord.ui.View.from_message(message)
        for item in view.children:
            item.disabled = True
        await message.edit(view=view)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        pass


class PaginatedEmbedView(discord.ui.View):

    def __init__(self, kind):
        super().__init__(timeout=None)
        self.kind = kind
        self.prev_button = _PaginatedStepButton(kind, "Previous", -1)
        self.next_button = _PaginatedStepButton(kind, "Next", +1)
        self.add_item(self.prev_button)
        self.add_item(self.next_button)

    def refresh_edge_state(self, index, total):
        self.prev_button.disabled = index <= 0
        self.next_button.disabled = index >= total - 1


class _PaginatedStepButton(discord.ui.Button):

    def __init__(self, kind, label, delta):
        direction = "prev" if delta < 0 else "next"
        super().__init__(
            label=label,
            style=discord.ButtonStyle.secondary,
            custom_id=f"paginated_{kind}_{direction}",
        )
        self.kind = kind
        self.delta = delta

    async def callback(self, interaction):
        data = _load_paginated(self.kind)
        if not data:
            await interaction.response.send_message(
                "This paginator is no longer active.", ephemeral=True
            )
            return

        title = data["title"]
        color = discord.Color(data["color"])
        lines = data["lines"]

        embeds, total = _build_page_embeds(title, lines, color)
        if total == 0:
            return

        current_idx = 0
        if interaction.message.embeds:
            match = re.search(r"\(Page (\d+)/\d+\)", interaction.message.embeds[0].title or "")
            if match:
                current_idx = int(match.group(1)) - 1

        new_idx = max(0, min(total - 1, current_idx + self.delta))

        view = PaginatedEmbedView(self.kind)
        view.refresh_edge_state(new_idx, total)
        await interaction.response.edit_message(embed=embeds[new_idx], view=view)


async def send_paginated(bot, channel, title, lines, color, kind):
    await _disable_previous_paginator(bot, kind)

    embeds, total = _build_page_embeds(title, lines, color)
    if total == 0:
        _clear_paginated(kind)
        return

    if total == 1:
        await channel.send(embed=embeds[0])
        _clear_paginated(kind)
        return

    view = PaginatedEmbedView(kind)
    view.refresh_edge_state(0, total)
    message = await channel.send(embed=embeds[0], view=view)
    _save_paginated(kind, message, title, color, lines)
