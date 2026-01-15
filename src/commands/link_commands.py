# slashAI - Discord Bot and MCP Server
# AGPL-3.0 License - https://github.com/mindfulent/slashAI

"""
Discord Account Linking Commands

Slash commands for linking Minecraft accounts to Discord accounts.
Used by the CoreCurriculum recognition system for DM notifications.
"""

import logging
import os
from typing import Optional

import httpx
from discord import app_commands
from discord.ext import commands

logger = logging.getLogger("slashAI.commands.link")

# Recognition API configuration
RECOGNITION_API_URL = os.getenv(
    "RECOGNITION_API_URL", "https://theblock.academy/api/recognition"
)
RECOGNITION_API_KEY = os.getenv("RECOGNITION_API_KEY")


class LinkCommands(commands.Cog):
    """
    Slash commands for Minecraft-Discord account linking.

    Commands:
    - /verify <code> - Complete account linking with a code from Minecraft
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._http_client: Optional[httpx.AsyncClient] = None

    @property
    def http_client(self) -> httpx.AsyncClient:
        """Lazy-initialize HTTP client."""
        if self._http_client is None:
            headers = {"Content-Type": "application/json"}
            if RECOGNITION_API_KEY:
                headers["Authorization"] = f"Bearer {RECOGNITION_API_KEY}"
            self._http_client = httpx.AsyncClient(
                base_url=RECOGNITION_API_URL,
                headers=headers,
                timeout=30.0,
            )
        return self._http_client

    async def cog_unload(self):
        """Clean up HTTP client on unload."""
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None

    @app_commands.command(
        name="verify",
        description="Link your Discord account to Minecraft using a code from /discord link",
    )
    @app_commands.describe(code="The linking code from Minecraft (e.g., TBA-A1B2C3)")
    async def verify(self, interaction, code: str):
        """
        Verify a linking code and connect Discord to Minecraft account.

        Args:
            interaction: The Discord interaction
            code: The linking code generated in Minecraft
        """
        await interaction.response.defer(ephemeral=True)

        # Normalize the code
        code = code.strip().upper()
        if not code.startswith("TBA-"):
            code = f"TBA-{code}"

        logger.info(f"Verifying linking code for Discord user {interaction.user.id}")

        try:
            response = await self.http_client.post(
                "/discord/verify",
                json={
                    "code": code,
                    "discord_id": str(interaction.user.id),
                },
            )

            if response.status_code == 200:
                data = response.json().get("data", {})
                minecraft_username = data.get("minecraft_username") or data.get(
                    "player_uuid", ""
                )[:8]

                await interaction.followup.send(
                    f"✅ **Account linked successfully!**\n\n"
                    f"Your Discord account is now linked to **{minecraft_username}**.\n\n"
                    f"You'll receive DM notifications when your builds are reviewed. "
                    f"You can unlink anytime with `/link remove` in Minecraft.",
                    ephemeral=True,
                )
                logger.info(
                    f"Successfully linked Discord {interaction.user.id} to Minecraft {data.get('player_uuid')}"
                )

            elif response.status_code == 400:
                error = response.json().get("error", "Invalid code")
                await interaction.followup.send(
                    f"❌ **Linking failed:** {error}\n\n"
                    f"Make sure you:\n"
                    f"1. Run `/discord link` in Minecraft first\n"
                    f"2. Use the code within 5 minutes\n"
                    f"3. Enter the code exactly as shown",
                    ephemeral=True,
                )
                logger.warning(f"Linking failed for {interaction.user.id}: {error}")

            else:
                await interaction.followup.send(
                    "❌ **Something went wrong.** Please try again later.",
                    ephemeral=True,
                )
                logger.error(
                    f"Unexpected response {response.status_code}: {response.text}"
                )

        except httpx.TimeoutException:
            await interaction.followup.send(
                "❌ **Request timed out.** Please try again.",
                ephemeral=True,
            )
            logger.error("Timeout while verifying linking code")

        except Exception as e:
            await interaction.followup.send(
                "❌ **An error occurred.** Please try again later.",
                ephemeral=True,
            )
            logger.error(f"Error verifying linking code: {e}", exc_info=True)


async def setup(bot: commands.Bot):
    """Add the cog to the bot."""
    await bot.add_cog(LinkCommands(bot))
