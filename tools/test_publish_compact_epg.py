#!/usr/bin/env python3

import gzip
import io
import struct
import unittest
from datetime import datetime, timedelta, timezone

from epg_format import GuideSchedule
from publish_compact_epg import FANART_MAGIC, FANART_VERSION, build_fanart_entries


class FanartPublisherTest(unittest.TestCase):
    def test_secondary_art_is_attached_to_primary_program_time(self):
        start = datetime(2026, 8, 21, 18, tzinfo=timezone.utc)
        primary = GuideSchedule(
            programs=[("El Programa", "", "", "", start, start + timedelta(hours=1))],
            current=None,
            next=None,
        )
        secondary = GuideSchedule(
            programs=[(
                "El Programa",
                "",
                "",
                "https://images.example/fanart.jpg",
                start + timedelta(minutes=5),
                start + timedelta(hours=1),
            )],
            current=None,
            next=None,
        )
        channel = {"name": "Canal", "epgId": "Canal.TV", "sourceIds": ["Canal.es"]}

        entries = build_fanart_entries(
            {"Canal.TV": primary},
            [channel],
            {"Canal.es": secondary},
        )

        self.assertEqual(
            [("Canal.TV", int(start.timestamp() * 1000), "https://images.example/fanart.jpg")],
            entries,
        )

    def test_binary_header_is_versioned_and_deterministic(self):
        temporary = io.BytesIO()
        with gzip.GzipFile(fileobj=temporary, mode="wb", filename="", mtime=0) as stream:
            stream.write(struct.pack(">iii", FANART_MAGIC, FANART_VERSION, 0))

        with gzip.GzipFile(fileobj=io.BytesIO(temporary.getvalue()), mode="rb") as stream:
            self.assertEqual(
                (FANART_MAGIC, FANART_VERSION, 0),
                struct.unpack(">iii", stream.read(12)),
            )


if __name__ == "__main__":
    unittest.main()
