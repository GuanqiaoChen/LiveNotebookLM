from __future__ import annotations

import base64

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from app.config import get_settings


router = APIRouter(prefix="/voices", tags=["voices"])

# Voices available in Gemini Live, with descriptions shown in the selection modal
VOICES: list[dict] = [
    {
        "name": "Aoede",
        "description": "Warm and expressive. Natural, friendly tone — great for long conversations.",
    },
    {
        "name": "Charon",
        "description": "Deep and measured. Calm authority — ideal for detailed analysis.",
    },
    {
        "name": "Fenrir",
        "description": "Bold and energetic. Dynamic delivery — best for engaging explanations.",
    },
    {
        "name": "Kore",
        "description": "Clear and precise. Professional tone — perfect for structured summaries.",
    },
    {
        "name": "Puck",
        "description": "Light and playful. Upbeat and accessible — great for casual exploration.",
    },
    {
        "name": "Orbit",
        "description": "Smooth and confident. Balanced presence — works well for any topic.",
    },
    {
        "name": "Zephyr",
        "description": "Gentle and soothing. Relaxed delivery — excellent for nuanced discussion.",
    },
]

# The sentence each voice will say for the preview
_PREVIEW_SENTENCE = (
    "Hello! I'm your AI assistant, ready to help you explore your documents "
    "through natural conversation. Ask me anything!"
)


@router.get("")
async def list_voices() -> list[dict]:
    """Return the list of available voices with names and descriptions."""
    return VOICES


@router.get("/preview/{voice_name}")
async def preview_voice(voice_name: str) -> Response:
    """
    Generate a short audio preview for the given voice using Gemini Live API.
    Returns raw PCM audio (audio/pcm;rate=24000) as a binary response.
    """
    valid_names = {v["name"] for v in VOICES}
    if voice_name not in valid_names:
        raise HTTPException(status_code=404, detail=f"Unknown voice: {voice_name}")

    settings = get_settings()

    from google import genai  # lazy import

    client = genai.Client(
        vertexai=True,
        project=settings.google_cloud_project,
        location=settings.google_cloud_location,
    )

    config = {
        "response_modalities": ["AUDIO"],
        "speech_config": {
            "voice_config": {
                "prebuilt_voice_config": {
                    "voice_name": voice_name,
                }
            }
        },
    }

    audio_chunks: list[bytes] = []

    try:
        async with client.aio.live.connect(
            model=settings.live_notebook_agent_model,
            config=config,
        ) as session:
            await session.send_client_content(
                turns=[
                    {
                        "role": "user",
                        "parts": [{"text": _PREVIEW_SENTENCE}],
                    }
                ],
                turn_complete=True,
            )

            async for message in session.receive():
                server_content = getattr(message, "server_content", None)
                if not server_content:
                    continue

                model_turn = getattr(server_content, "model_turn", None)
                if model_turn and getattr(model_turn, "parts", None):
                    for part in model_turn.parts:
                        inline_data = getattr(part, "inline_data", None)
                        if inline_data and getattr(inline_data, "data", None):
                            audio_chunks.append(inline_data.data)

                if getattr(server_content, "turn_complete", False):
                    break

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Voice preview generation failed: {type(exc).__name__}: {exc}",
        ) from exc

    if not audio_chunks:
        raise HTTPException(status_code=500, detail="No audio received from Gemini Live")

    audio_data = b"".join(audio_chunks)
    return Response(content=audio_data, media_type="audio/pcm;rate=24000")
