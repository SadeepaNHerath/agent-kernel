from __future__ import annotations

import shlex

from agentkernel.api import RESTAPI
from agentkernel.openai import OpenAIModule
from agentkernel.telegram import AgentTelegramRequestHandler

from agent import AGENTS
from tool import (
    approve_campaign_package,
    approve_caption_pack,
    approve_flyer,
    approve_flyer_content,
    create_event_context,
    draft_flyer_content,
    edit_caption_pack,
    edit_flyer_content,
    generate_caption_pack,
    generate_flyer,
    get_campaign_status,
    get_event_context,
    ingest_direct_campaign_assets,
    publish_campaign,
    update_event_context,
)

OpenAIModule(AGENTS)


def _parts(command: str) -> tuple[str, list[str]]:
    pieces = shlex.split(command)
    if not pieces:
        return "", []
    return pieces[0].lower(), pieces[1:]


def _arg(args: list[str], index: int, default: str = "") -> str:
    return args[index] if index < len(args) else default


class CampaignTelegramHandler(AgentTelegramRequestHandler):
    async def _handle_command(self, chat_id: int, command: str):
        cmd, args = _parts(command)

        try:
            if cmd in {"/start", "/help"}:
                await self._send_message(
                    chat_id,
                    "\n".join(
                        [
                            "CampaignKernel commands:",
                            "/new_event <name>",
                            "/context <event_id> <style notes>",
                            "/brief <event_id> <campaign brief>",
                            "/approve_content <event_id> <campaign_id>",
                            "/generate_flyer <event_id> <campaign_id>",
                            "/approve_flyer <event_id> <campaign_id>",
                            "/caption <event_id> <campaign_id>",
                            "/approve_caption <event_id> <campaign_id>",
                            "/approve_campaign <event_id> <campaign_id>",
                            "/publish <event_id> <campaign_id> instagram facebook linkedin",
                            "/export <event_id> <campaign_id> whatsapp",
                            "/direct <event_id> flyer=<path> caption=<caption text>",
                            "/status <event_id> [campaign_id]",
                        ]
                    ),
                )
                return

            if cmd == "/new_event":
                await self._send_message(chat_id, create_event_context(" ".join(args)))
                return

            if cmd == "/context":
                event_id = _arg(args, 0)
                notes = " ".join(args[1:])
                await self._send_message(chat_id, update_event_context(event_id=event_id, theme_notes=notes))
                return

            if cmd == "/style_summary":
                await self._send_message(chat_id, get_event_context(event_id=_arg(args, 0)))
                return

            if cmd == "/brief":
                event_id = _arg(args, 0)
                brief = " ".join(args[1:])
                await self._send_message(chat_id, draft_flyer_content(event_id=event_id, campaign_brief=brief))
                return

            if cmd == "/edit":
                event_id = _arg(args, 0)
                campaign_id = _arg(args, 1)
                instruction = " ".join(args[2:])
                status = get_campaign_status(event_id=event_id, campaign_id=campaign_id)
                if '"status": "caption_draft"' in status or '"status": "caption_approved"' in status:
                    await self._send_message(chat_id, edit_caption_pack(event_id, campaign_id, instruction))
                else:
                    await self._send_message(chat_id, edit_flyer_content(event_id, campaign_id, instruction))
                return

            if cmd == "/approve_content":
                await self._send_message(
                    chat_id, approve_flyer_content(event_id=_arg(args, 0), campaign_id=_arg(args, 1))
                )
                return

            if cmd == "/generate_flyer":
                await self._send_message(
                    chat_id,
                    generate_flyer(
                        event_id=_arg(args, 0), campaign_id=_arg(args, 1), design_instruction=" ".join(args[2:])
                    ),
                )
                return

            if cmd == "/approve_flyer":
                await self._send_message(chat_id, approve_flyer(event_id=_arg(args, 0), campaign_id=_arg(args, 1)))
                return

            if cmd == "/caption":
                await self._send_message(
                    chat_id,
                    generate_caption_pack(
                        event_id=_arg(args, 0), campaign_id=_arg(args, 1), user_direction=" ".join(args[2:])
                    ),
                )
                return

            if cmd == "/approve_caption":
                await self._send_message(
                    chat_id, approve_caption_pack(event_id=_arg(args, 0), campaign_id=_arg(args, 1))
                )
                return

            if cmd == "/approve_campaign":
                await self._send_message(
                    chat_id, approve_campaign_package(event_id=_arg(args, 0), campaign_id=_arg(args, 1))
                )
                return

            if cmd == "/publish":
                await self._send_message(
                    chat_id,
                    publish_campaign(event_id=_arg(args, 0), campaign_id=_arg(args, 1), targets=" ".join(args[2:])),
                )
                return

            if cmd == "/export":
                await self._send_message(
                    chat_id,
                    publish_campaign(
                        event_id=_arg(args, 0), campaign_id=_arg(args, 1), targets=" ".join(args[2:]) or "whatsapp"
                    ),
                )
                return

            if cmd == "/direct":
                event_id = _arg(args, 0)
                raw = " ".join(args[1:])
                flyer_path = ""
                caption = raw
                if "flyer=" in raw:
                    flyer_path = raw.split("flyer=", 1)[1].split(" caption=", 1)[0].strip()
                if "caption=" in raw:
                    caption = raw.split("caption=", 1)[1].strip()
                await self._send_message(
                    chat_id,
                    ingest_direct_campaign_assets(event_id=event_id, flyer_path=flyer_path, caption_text=caption),
                )
                return

            if cmd == "/status":
                await self._send_message(
                    chat_id, get_campaign_status(event_id=_arg(args, 0), campaign_id=_arg(args, 1))
                )
                return

            await self._process_agent_message(chat_id, command)

        except Exception as error:
            await self._send_message(chat_id, f"CampaignKernel command failed: {error}")


if __name__ == "__main__":
    RESTAPI.run([CampaignTelegramHandler()])
