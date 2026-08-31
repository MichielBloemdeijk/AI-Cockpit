"""Durable Jarvis preferences shared by browser and native clients."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.db.session import session_scope
from app.db.tables import JarvisPreference


@dataclass(frozen=True, slots=True)
class JarvisPreferences:
    voice: str
    concision: str
    wake_behavior: str
    developer_mode: bool
    android_privacy: dict
    updated_at: datetime | None


class JarvisPreferencesService:
    async def get(self) -> JarvisPreferences:
        async with session_scope() as session:
            row = await session.get(JarvisPreference, "current")
            if row is None:
                row = JarvisPreference(id="current")
                session.add(row)
                await session.flush()
            return self._view(row)

    async def patch(self, values: dict[str, object]) -> tuple[JarvisPreferences, bool]:
        async with session_scope() as session:
            row = await session.get(JarvisPreference, "current")
            if row is None:
                row = JarvisPreference(id="current")
                session.add(row)
                await session.flush()
            old_voice = row.voice
            for key in ("voice", "concision", "wake_behavior", "developer_mode"):
                if key in values and values[key] is not None:
                    setattr(row, key, values[key])
            if values.get("android_privacy") is not None:
                row.android_privacy_json = dict(values["android_privacy"] or {})
            await session.flush()
            await session.refresh(row)
            return self._view(row), old_voice != row.voice

    @staticmethod
    def _view(row: JarvisPreference) -> JarvisPreferences:
        return JarvisPreferences(
            voice=row.voice,
            concision=row.concision,
            wake_behavior=row.wake_behavior,
            developer_mode=row.developer_mode,
            android_privacy=dict(row.android_privacy_json or {}),
            updated_at=row.updated_at,
        )


jarvis_preferences_service = JarvisPreferencesService()
