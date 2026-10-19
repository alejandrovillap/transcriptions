"""Web API for audio devices (Windows build, audio_sources.py): lists the microphones and
outputs for the settings dialog. server.py only does `app.include_router(audio_api.router)`."""

import asyncio

from fastapi import APIRouter

router = APIRouter()


def _list() -> dict:
    import audio_sources
    return audio_sources.list_devices()


@router.get("/api/audio/devices")
async def audio_devices():
    try:
        return await asyncio.to_thread(_list)
    except Exception as e:   # soundcard missing or no audio devices
        return {"mics": [], "outputs": [], "error": str(e)}
