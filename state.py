import json

import discord

from database import cursor, conn


# Mutable state shared across modules
patrol_votes = {}
cant_make_votes = set()
aop_votes = {}
confirmed_start_time = None
voting_open = False
current_map = "LC"

patrol_message = None
aop_message = None
announcement_message = None
patrol_embed_title = "Patrol Attendance"
aop_embed_title = "AOP Voting"


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


async def load_session(bot):
    global patrol_votes, cant_make_votes, aop_votes, confirmed_start_time
    global voting_open, current_map, patrol_message, aop_message, announcement_message

    from views import PatrolView, AOPView

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

    if patrol_message:
        bot.add_view(PatrolView(), message_id=patrol_message.id)
    if aop_message:
        bot.add_view(AOPView(), message_id=aop_message.id)

    from helpers import PaginatedEmbedView, _load_paginated
    for kind in ("stats", "inactivity"):
        data = _load_paginated(kind)
        if data and data.get("message_id"):
            bot.add_view(PaginatedEmbedView(kind), message_id=data["message_id"])
