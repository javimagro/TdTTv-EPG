#!/usr/bin/env python3
"""Read XMLTV schedules and write the compact TdTWorld EPG binary format."""

import gzip
import re
import struct
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from urllib.request import Request, urlopen


GUIDE_MAGIC = 0x54444744
GUIDE_VERSION = 2
HTTP_TIMEOUT_SECONDS = 30
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


@dataclass
class GuideSchedule:
    programs: list
    current: tuple | None
    next: tuple | None


def parse_xmltv_time(value):
    if not value:
        return None
    try:
        return datetime.strptime(
            value.strip(),
            "%Y%m%d%H%M%S %z",
        ).astimezone(timezone.utc)
    except ValueError:
        return None


def normalize_schedule_key(value):
    if not value:
        return ""
    value = value.split("@", 1)[0]
    normalized_value = unicodedata.normalize("NFD", value)
    normalized_value = "".join(
        character
        for character in normalized_value
        if unicodedata.category(character) != "Mn"
    )
    return re.sub(r"[^a-z0-9]", "", normalized_value.lower())


def xmltv_stream_bytes(url):
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
        return response.read()


def open_xmltv_bytes(data):
    if len(data) >= 2 and data[0] == 0x1F and data[1] == 0x8B:
        return gzip.GzipFile(fileobj=BytesIO(data))
    return BytesIO(data)


def parse_xmltv_schedules(url):
    schedules = {}
    current_programs = {}
    next_programs = {}
    programs_by_channel = {}
    now = datetime.now(timezone.utc)
    with open_xmltv_bytes(xmltv_stream_bytes(url)) as stream:
        for _, element in ET.iterparse(stream, events=("end",)):
            if element.tag != "programme":
                continue
            channel_id = (element.get("channel") or "").strip()
            start = parse_xmltv_time(element.get("start"))
            stop = parse_xmltv_time(element.get("stop"))
            if not channel_id or start is None or stop is None or stop <= start:
                element.clear()
                continue
            program = parse_program(element, start, stop)
            programs_by_channel.setdefault(channel_id, []).append(program)
            if start <= now < stop:
                current_programs[channel_id] = program
            elif start > now:
                existing_next = next_programs.get(channel_id)
                if existing_next is None or start < existing_next[4]:
                    next_programs[channel_id] = program
            element.clear()
    for channel_id, programs in programs_by_channel.items():
        programs.sort(key=lambda program: program[4])
        schedules[channel_id] = GuideSchedule(
            programs=programs,
            current=current_programs.get(channel_id),
            next=next_programs.get(channel_id),
        )
    add_normalized_schedule_aliases(schedules)
    return schedules


def parse_program(element, start, stop):
    values = {"title": "", "desc": "", "category": "", "icon": ""}
    for child in element:
        if child.tag == "icon" and not values["icon"]:
            values["icon"] = (child.get("src") or "").strip()
        elif child.tag in values and not values[child.tag]:
            values[child.tag] = (child.text or "").strip()
    return (
        values["title"],
        values["desc"],
        values["category"],
        values["icon"],
        start,
        stop,
    )


def add_normalized_schedule_aliases(schedules):
    aliases = {}
    for key, schedule in schedules.items():
        normalized_key = normalize_schedule_key(key)
        if normalized_key and normalized_key not in schedules:
            aliases[normalized_key] = schedule
    schedules.update(aliases)


def write_string(output, value):
    data = (value or "").encode("utf-8")
    output.write(struct.pack(">i", len(data)))
    output.write(data)


def write_long(output, value):
    output.write(struct.pack(">q", int(value)))


def write_epg(path, schedules):
    unique_schedules, schedule_ids = collect_unique_schedules(schedules)
    coverage_start, coverage_end = calculate_coverage(unique_schedules)
    with path.open("wb") as output:
        output.write(struct.pack(">ii", GUIDE_MAGIC, GUIDE_VERSION))
        write_long(output, datetime.now(timezone.utc).timestamp() * 1000)
        write_long(output, coverage_start)
        write_long(output, coverage_end)
        output.write(struct.pack(">i", len(unique_schedules)))
        for schedule in unique_schedules:
            write_schedule(output, schedule)
        output.write(struct.pack(">i", len(schedules)))
        for key, schedule in schedules.items():
            write_string(output, key)
            output.write(struct.pack(">i", schedule_ids[id(schedule)]))


def collect_unique_schedules(schedules):
    unique_schedules = []
    schedule_ids = {}
    for schedule in schedules.values():
        schedule_key = id(schedule)
        if schedule_key not in schedule_ids:
            schedule_ids[schedule_key] = len(unique_schedules)
            unique_schedules.append(schedule)
    return unique_schedules, schedule_ids


def calculate_coverage(schedules):
    coverage_start = 0
    coverage_end = 0
    for schedule in schedules:
        for program in schedule.programs:
            start_ms = int(program[4].timestamp() * 1000)
            stop_ms = int(program[5].timestamp() * 1000)
            if coverage_start == 0 or start_ms < coverage_start:
                coverage_start = start_ms
            if stop_ms > coverage_end:
                coverage_end = stop_ms
    return coverage_start, coverage_end


def write_schedule(output, schedule):
    output.write(struct.pack(">i", len(schedule.programs)))
    for program in schedule.programs:
        for value in program[:4]:
            write_string(output, value)
        write_long(output, program[4].timestamp() * 1000)
        write_long(output, program[5].timestamp() * 1000)
