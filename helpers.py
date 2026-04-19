import datetime

import discord

import state
from config import (
    time_slots, mapLC, mapLS, MINIMUM_PATROL,
    ADMIN_COMMAND_CHANNEL, ADMIN_ROLE_ID,
)


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


async def send_paginated(channel, title, lines, color):
    pages = paginate_lines(lines)

    for i, page in enumerate(pages):
        suffix = f" (Page {i + 1}/{len(pages)})" if len(pages) > 1 else ""
        embed = styled_embed(f"{title}{suffix}", page, color)
        await channel.send(embed=embed)
