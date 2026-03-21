import discord
from discord.ext import commands

from config import (
    WELCOME_CHANNEL_ID, GOODBYE_CHANNEL_ID,
    RULES_CHANNEL_ID, SERVER_LINKS_CHANNEL_ID, RESOURCES_CHANNEL_ID,
    GENERAL_CHANNEL_ID, SUPPORT_CHANNEL_ID,
)
from helpers import styled_embed


class WelcomeCog(commands.Cog):

    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        channel = self.bot.get_channel(WELCOME_CHANNEL_ID)
        if not channel:
            return

        embed = styled_embed(
            "\U0001f44b Welcome!",
            f"Hey there {member.mention} welcome to **Greater Ontario Roleplay**-"
            f"*A division of **Greater Ontario Gaming***, one of the top, and "
            f"*the* longest standing Canadian FiveM Roleplay servers. We are glad you are here!\n\n"
            f"Be sure to check out these important channels "
            f"<#{RULES_CHANNEL_ID}> <#{SERVER_LINKS_CHANNEL_ID}> <#{RESOURCES_CHANNEL_ID}> "
            f"and if you need more help you can post in <#{GENERAL_CHANNEL_ID}> "
            f"or open a <#{SUPPORT_CHANNEL_ID}>\n\n"
            f"*Thanks for joining us and Good luck!*",
            discord.Color.green()
        )
        embed.set_thumbnail(url=member.display_avatar.url)

        await channel.send(embed=embed)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        channel = self.bot.get_channel(GOODBYE_CHANNEL_ID)
        if not channel:
            return

        embed = styled_embed(
            "\U0001f44b Goodbye!",
            f"**{member.display_name}** has left the server. We hope to see you again!",
            discord.Color.red()
        )
        embed.set_thumbnail(url=member.display_avatar.url)

        await channel.send(embed=embed)


async def setup(bot):
    await bot.add_cog(WelcomeCog(bot))
