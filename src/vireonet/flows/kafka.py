"""Optional Kafka trigger source (NSP-style event-driven flows).

Off unless VIREONET_KAFKA_BOOTSTRAP is set. Runs inside the worker: it
subscribes to the topics of enabled Kafka-triggered flows (refreshed every
`refresh_seconds`), turns each message into a trigger event and commits the
offset only after the flows were dispatched (at-least-once; the default
dedup key topic:partition:offset makes a redelivery harmless).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

from vireonet.flows.dispatch import FlowDispatcher
from vireonet.structlog_config import get_logger

log = get_logger(__name__)


def message_event(msg: Any) -> dict[str, Any]:
    raw = msg.value
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, (bytes, bytearray)) else raw
    try:
        value: Any = json.loads(text) if isinstance(text, str) else text
    except ValueError:
        value = text
    key = (
        msg.key.decode("utf-8", errors="replace")
        if isinstance(msg.key, (bytes, bytearray))
        else msg.key
    )
    headers = {
        k: (v.decode("utf-8", errors="replace") if isinstance(v, (bytes, bytearray)) else v)
        for k, v in (msg.headers or [])
    }
    return {
        "source": "kafka",
        "event": "message",
        "topic": msg.topic,
        "partition": msg.partition,
        "offset": msg.offset,
        "key": key,
        "value": value,
        "headers": headers,
    }


def aiokafka_factory(settings: Any) -> Callable[[], Any]:
    def make() -> Any:
        from aiokafka import AIOKafkaConsumer

        kw: dict[str, Any] = {
            "bootstrap_servers": settings.kafka_bootstrap,
            "group_id": settings.kafka_group_id,
            "enable_auto_commit": False,
            "auto_offset_reset": settings.kafka_auto_offset_reset,
        }
        if settings.kafka_security_protocol != "PLAINTEXT":
            kw["security_protocol"] = settings.kafka_security_protocol
        if settings.kafka_sasl_mechanism:
            kw.update(
                sasl_mechanism=settings.kafka_sasl_mechanism,
                sasl_plain_username=settings.kafka_username,
                sasl_plain_password=settings.kafka_password,
            )
        return AIOKafkaConsumer(**kw)

    return make


class KafkaTriggerConsumer:
    def __init__(
        self,
        dispatcher: FlowDispatcher,
        consumer_factory: Callable[[], Any],
        *,
        refresh_seconds: float = 30.0,
    ) -> None:
        self._dispatcher = dispatcher
        self._factory = consumer_factory
        self._refresh = refresh_seconds
        self._stop = asyncio.Event()
        self.topics: set[str] = set()
        self.dispatched = 0

    async def wanted_topics(self) -> set[str]:
        flows = await self._dispatcher.store.triggered_flows({"kafka"})
        return {str(f["trigger"].get("topic")) for f in flows if f["trigger"].get("topic")}

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        consumer = self._factory()
        await consumer.start()
        log.info("kafka trigger consumer started")
        try:
            next_refresh = 0.0
            loop = asyncio.get_running_loop()
            while not self._stop.is_set():
                if loop.time() >= next_refresh:
                    topics = await self.wanted_topics()
                    if topics != self.topics:
                        if topics:
                            consumer.subscribe(sorted(topics))
                        else:
                            consumer.unsubscribe()
                        log.info("kafka trigger topics", topics=sorted(topics))
                        self.topics = topics
                    next_refresh = loop.time() + self._refresh
                if not self.topics:
                    await asyncio.sleep(min(self._refresh, 1.0))
                    continue
                batches = await consumer.getmany(timeout_ms=1000, max_records=100)
                for _tp, messages in batches.items():
                    for msg in messages:
                        try:
                            await self._dispatcher.dispatch(message_event(msg), started_by="kafka")
                            self.dispatched += 1
                        except Exception as exc:  # keep consuming; the run log shows failures
                            log.warning(
                                "kafka dispatch failed",
                                topic=msg.topic,
                                offset=msg.offset,
                                error=str(exc),
                            )
                if batches:
                    await consumer.commit()
        finally:
            await consumer.stop()
