import discord

import state
from config import time_slots, mapLC, mapLS
from database import record_stat, log_activity
from helpers import update_patrol_message, update_aop_message


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
        if not state.voting_open:
            await interaction.response.send_message("Voting is closed.", ephemeral=True)
            return

        is_new = interaction.user.id not in state.patrol_votes and interaction.user.id not in state.cant_make_votes
        state.cant_make_votes.discard(interaction.user.id)
        state.patrol_votes[interaction.user.id] = self.time
        if is_new:
            record_stat(interaction.user.id, "patrol_votes")
            log_activity(interaction.user.id, "patrol_vote")
        state.save_session()

        await interaction.response.send_message(f"You voted for **{self.time}**.", ephemeral=True)
        await update_patrol_message()


class CantMakeButton(discord.ui.Button):

    def __init__(self):
        super().__init__(
            label="Can't Make It",
            emoji="\u274c",
            style=discord.ButtonStyle.danger,
            custom_id="patrol_cant_make",
        )

    async def callback(self, interaction: discord.Interaction):
        if not state.voting_open:
            await interaction.response.send_message("Voting is closed.", ephemeral=True)
            return

        is_new = interaction.user.id not in state.cant_make_votes and interaction.user.id not in state.patrol_votes
        state.patrol_votes.pop(interaction.user.id, None)
        state.cant_make_votes.add(interaction.user.id)
        if is_new:
            record_stat(interaction.user.id, "cant_make")
            log_activity(interaction.user.id, "cant_make")
        state.save_session()

        await interaction.response.send_message("You marked **Can't Make It**.", ephemeral=True)
        await update_patrol_message()


class AOPView(discord.ui.View):

    def __init__(self):
        super().__init__(timeout=None)

        options = mapLC if state.current_map == "LC" else mapLS

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
        if not state.voting_open:
            await interaction.response.send_message("Voting is closed.", ephemeral=True)
            return

        is_new = interaction.user.id not in state.aop_votes
        state.aop_votes[interaction.user.id] = self.option
        if is_new:
            record_stat(interaction.user.id, "aop_votes")
            log_activity(interaction.user.id, "aop_vote")
        state.save_session()

        await interaction.response.send_message(f"You voted for **{self.option}**.", ephemeral=True)
        await update_aop_message()
