"""One durable price source for both the native optimizer and its dashboard."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, replace
from datetime import datetime, timedelta
from typing import Any

from .const import (
    CONF_PRICE_AREA,
    CONF_PRICE_ENTITY,
    CONF_PRICE_MARKUP,
    CONF_PRICE_PROVIDER,
    CONF_PRICE_VAT,
    CONF_TIBBER_HOME,
)
from .models import PriceSlot
from .price import current_slot, parse_price_slots


def contiguous_prices(slots: tuple[PriceSlot, ...], now: datetime) -> tuple[PriceSlot, ...]:
    """Never plan through a gap or extrapolate a price past its published end."""
    active = current_slot(slots, now)
    if active is None:
        return ()
    result = [active]
    for slot in sorted(slots, key=lambda item: item.start):
        if slot.start == result[-1].end:
            result.append(slot)
    return tuple(result)


def nordpool_slots(
    response: dict,
    now: datetime,
    area: str,
    vat: float,
    markup: float,
) -> tuple[PriceSlot, ...]:
    """Convert published SEK/MWh spot prices into configured SEK/kWh prices."""
    rows = response.get("multiAreaEntries", response.get("multi_area_entries", []))
    normalized = []
    for row in rows:
        value = row.get("entryPerArea", row.get("entry_per_area", {})).get(area)
        if value is None:
            continue
        try:
            price = (float(value) / 1000 + markup / 100) * (1 + vat / 100)
        except (ValueError, TypeError):
            continue
        normalized.append(
            {
                "start": row.get("deliveryStart"),
                "end": row.get("deliveryEnd"),
                "price": price,
            }
        )
    return parse_price_slots({"prices": normalized}, now)


class PriceSource:
    """Fetch only the selected provider and retain still-valid published prices."""

    def __init__(self, hass, config: dict, storage, fetch_json=None) -> None:
        self.hass, self.config, self.storage = hass, config, storage
        self.fetch_json = fetch_json
        self.snapshot: dict[str, Any] = {}
        self._last_attempt: datetime | None = None
        self._effective = "ha_entity"
        self._home = ""
        self._homes: list[str] = []
        self._key = "|".join(
            str(config.get(key, ""))
            for key in (
                CONF_PRICE_PROVIDER,
                CONF_PRICE_ENTITY,
                CONF_PRICE_AREA,
                CONF_PRICE_VAT,
                CONF_PRICE_MARKUP,
                CONF_TIBBER_HOME,
            )
        )
        cached = getattr(storage.state, "price_cache", {})
        self._cache = cached if cached.get("key") == self._key else {}

    def _platform(self, entity_id: str, state) -> str:
        try:
            from homeassistant.helpers import entity_registry as er

            entry = er.async_get(self.hass).async_get(entity_id)
            if entry:
                return entry.platform
        except (ImportError, AttributeError):
            pass
        text = f"{entity_id} {getattr(state, 'attributes', {})}".lower()
        if "tibber" in text:
            return "tibber"
        return "nordpool" if "nordpool" in text else ""

    async def _service(self, domain: str, service: str, data: dict) -> dict:
        if not self.hass.services.has_service(domain, service):
            raise ValueError(f"{domain}_service_unavailable")
        async with asyncio.timeout(20):
            return (
                await self.hass.services.async_call(
                    domain,
                    service,
                    data,
                    blocking=True,
                    return_response=True,
                )
                or {}
            )

    async def _json(self, params: dict) -> dict:
        if self.fetch_json:
            return await self.fetch_json(params)
        from homeassistant.helpers.aiohttp_client import async_get_clientsession

        async with asyncio.timeout(20):
            async with async_get_clientsession(self.hass).get(
                "https://dataportal-api.nordpoolgroup.com/api/DayAheadPrices",
                params=params,
            ) as response:
                if response.status in {204, 404}:
                    return {}
                response.raise_for_status()
                return await response.json()

    async def _tibber(self, now: datetime) -> tuple[PriceSlot, ...]:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        response = await self._service(
            "tibber",
            "get_prices",
            {
                "start": start.isoformat(),
                "end": (start + timedelta(days=2)).isoformat(),
            },
        )
        homes = response.get("prices", {})
        self._homes = list(homes)
        chosen = self.config.get(CONF_TIBBER_HOME, "")
        if not chosen:
            entity_id = self.config.get(CONF_PRICE_ENTITY, "")
            state = self.hass.states.get(entity_id)
            text = f"{entity_id} {getattr(state, 'attributes', {}).get('friendly_name', '')}"
            matches = [home for home in homes if home.casefold() in text.casefold()]
            chosen = (
                matches[0]
                if len(matches) == 1
                else next(iter(homes), "")
                if len(homes) == 1
                else ""
            )
        if not chosen or chosen not in homes:
            raise ValueError("tibber_home_selection_required")
        self._home = chosen
        return parse_price_slots({"prices": homes[chosen]}, now)

    async def _nordpool(self, now: datetime, direct: bool) -> tuple[PriceSlot, ...]:
        area = self.config[CONF_PRICE_AREA]
        vat, markup = self.config[CONF_PRICE_VAT], self.config[CONF_PRICE_MARKUP]
        entry_id = None
        if not direct:
            try:
                from homeassistant.helpers import entity_registry as er

                entity = er.async_get(self.hass).async_get(self.config.get(CONF_PRICE_ENTITY, ""))
                entry_id = (
                    entity.config_entry_id if entity and entity.platform == "nordpool" else None
                )
            except (ImportError, AttributeError):
                pass
            if not entry_id:
                entries = self.hass.config_entries.async_entries("nordpool")
                if len(entries) != 1:
                    raise ValueError("nordpool_entry_selection_required")
                entry_id = entries[0].entry_id
        result = []
        for day in (now.date(), now.date() + timedelta(days=1)):
            try:
                if direct:
                    data = await self._json(
                        {
                            "date": day.isoformat(),
                            "deliveryArea": area,
                            "currency": "SEK",
                        }
                    )
                    result.extend(nordpool_slots(data, now, area, vat, markup))
                else:
                    data = await self._service(
                        "nordpool",
                        "get_prices_for_date",
                        {
                            "config_entry": entry_id,
                            "date": day.isoformat(),
                            "areas": [area],
                            "currency": "SEK",
                        },
                    )
                    rows = parse_price_slots({"prices": data.get(area, [])}, now)
                    result.extend(
                        replace(row, price=(row.price / 1000 + markup / 100) * (1 + vat / 100))
                        for row in rows
                    )
            except Exception:
                if day == now.date():
                    raise
                # Tomorrow may not have been published; never fabricate it.
        return tuple(result)

    async def async_prices(self, now: datetime) -> tuple[PriceSlot, ...]:
        provider = self.config.get(CONF_PRICE_PROVIDER, "auto")
        provider = {"tibber": "ha_tibber", "nordpool": "ha_nordpool", "manual": "ha_entity"}.get(
            provider, provider,
        )
        entity_id = self.config.get(CONF_PRICE_ENTITY, "")
        state = self.hass.states.get(entity_id) if entity_id else None
        platform = self._platform(entity_id, state)
        entity_slots = parse_price_slots(getattr(state, "attributes", {}), now)
        effective = provider
        if provider == "auto":
            effective = {"tibber": "ha_tibber", "nordpool": "ha_nordpool"}.get(
                platform, "ha_entity"
            )
        self._effective = effective
        slots = ()
        error = None
        use_entity = provider in {"auto", "ha_entity"} or (
            provider == "ha_nordpool" and platform == "nordpool"
        )
        if use_entity and len(contiguous_prices(entity_slots, now)) >= 2:
            unit = str(getattr(state, "attributes", {}).get("unit_of_measurement", ""))
            factor = 0.001 if "MWh" in unit else 0.01 if "öre" in unit.lower() else 1
            slots = tuple(replace(slot, price=slot.price * factor) for slot in entity_slots)
        elif effective != "ha_entity":
            due = self._last_attempt is None or (now - self._last_attempt).total_seconds() >= 300
            if due:
                self._last_attempt = now
                try:
                    slots = (
                        await self._tibber(now)
                        if effective == "ha_tibber"
                        else (await self._nordpool(now, effective == "nordpool_direct"))
                    )
                except Exception as err:
                    error = str(err) if isinstance(err, ValueError) else type(err).__name__
            else:
                error = self.snapshot.get("error")
        if slots:
            cached_slots = parse_price_slots({"prices": self._cache.get("slots", [])}, now)
            retained = {slot.start: slot for slot in cached_slots if slot.end > now}
            retained.update({slot.start: slot for slot in slots})
            slots = tuple(sorted(retained.values(), key=lambda slot: slot.start))
            self._cache = {
                "key": self._key,
                "slots": [asdict(slot) for slot in slots],
                "fetched_at": now.isoformat(),
                "home": self._home,
            }
            # Store JSON-compatible timestamps in HA's persistent cache.
            self._cache["slots"] = [
                dict(row, start=row["start"].isoformat(), end=row["end"].isoformat())
                for row in self._cache["slots"]
            ]
            self.storage.state.price_cache = self._cache
        if not slots and self._cache:
            slots = parse_price_slots({"prices": self._cache.get("slots", [])}, now)
        usable = contiguous_prices(slots, now)
        ready = len(usable) >= 2
        if not ready and not error:
            error = ("price_entity_has_no_forecast" if effective == "ha_entity"
                     else "no_published_price_coverage")
        self.snapshot = {
            "entity_platform": platform,
            "tibber_service_available": self.hass.services.has_service("tibber", "get_prices"),
            "nordpool_service_available": self.hass.services.has_service(
                "nordpool", "get_prices_for_date",
            ),
            "provider": provider,
            "effective_provider": effective,
            "currency": "SEK",
            "entity_id": entity_id or None,
            "area": self.config[CONF_PRICE_AREA],
            "vat_percent": self.config[CONF_PRICE_VAT],
            "markup_ore_kwh": self.config[CONF_PRICE_MARKUP],
            "tibber_home": self._home or self._cache.get("home", ""),
            "available_homes": self._homes,
            "fetched_at": self._cache.get("fetched_at"),
            "status": "ready_cached" if ready and error else "ready" if ready else "waiting",
            "error": error,
            "coverage_end": usable[-1].end.isoformat() if usable else None,
            "slots": [
                dict(start=s.start.isoformat(), end=s.end.isoformat(), price=s.price) for s in slots
            ],
        }
        return usable if ready else ()
