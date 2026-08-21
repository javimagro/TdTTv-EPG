#!/usr/bin/env python3
"""Build a validated, versioned EPG containing only configured generalist channels."""

import argparse
import gzip
import hashlib
import json
import shutil
import struct
import unicodedata
from collections import defaultdict
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from epg_format import (
    GUIDE_MAGIC,
    GUIDE_VERSION,
    GuideSchedule,
    normalize_schedule_key,
    parse_xmltv_schedules,
    write_epg,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "tools" / "epg" / "generalist_channels.json"
FANART_MAGIC = 0x54444641
FANART_VERSION = 1


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--country", required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", type=Path)
    source.add_argument("--input-binary", type=Path)
    parser.add_argument("--fanart-input", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--asset-output", type=Path)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    return parser.parse_args()


def read_exact(stream, length):
    data = stream.read(length)
    if len(data) != length:
        raise RuntimeError("Truncated EPG binary")
    return data


def read_int(stream):
    return struct.unpack(">i", read_exact(stream, 4))[0]


def read_long(stream):
    return struct.unpack(">q", read_exact(stream, 8))[0]


def read_string(stream):
    length = read_int(stream)
    if length < 0:
        raise RuntimeError("Negative EPG string length")
    return read_exact(stream, length).decode("utf-8")


def timestamp(value):
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc)


def read_binary_schedules(path):
    schedules = []
    with path.open("rb") as stream:
        magic = read_int(stream)
        version = read_int(stream)
        if magic != GUIDE_MAGIC or version != GUIDE_VERSION:
            raise RuntimeError(f"Unsupported EPG binary: {path}")
        read_long(stream)
        read_long(stream)
        read_long(stream)
        schedule_count = read_int(stream)
        if schedule_count < 0:
            raise RuntimeError("Negative EPG schedule count")
        for _ in range(schedule_count):
            programs = []
            program_count = read_int(stream)
            if program_count < 0:
                raise RuntimeError("Negative EPG program count")
            for _ in range(program_count):
                programs.append((
                    read_string(stream),
                    read_string(stream),
                    read_string(stream),
                    read_string(stream),
                    timestamp(read_long(stream)),
                    timestamp(read_long(stream)),
                ))
            schedules.append(GuideSchedule(programs=programs, current=None, next=None))
        aliases = {}
        alias_count = read_int(stream)
        if alias_count < 0:
            raise RuntimeError("Negative EPG alias count")
        for _ in range(alias_count):
            key = read_string(stream)
            schedule_index = read_int(stream)
            if schedule_index < 0 or schedule_index >= len(schedules):
                raise RuntimeError("Invalid EPG schedule index")
            aliases[key] = schedules[schedule_index]
        if stream.read(1):
            raise RuntimeError("Trailing data in EPG binary")
    return aliases


def normalized_index(schedules):
    result = {}
    for key, schedule in schedules.items():
        normalized = normalize_schedule_key(key)
        if normalized and normalized not in result:
            result[normalized] = schedule
    return result


def normalize_program_title(value):
    decomposed = unicodedata.normalize("NFKD", value or "")
    return "".join(character.lower() for character in decomposed if character.isalnum())


def valid_image_url(value):
    normalized = (value or "").strip()
    if len(normalized) > 2048:
        return ""
    if not normalized.startswith(("https://", "http://")):
        return ""
    return normalized


def fanart_schedule_for_channel(source, channel):
    candidate_ids = channel.get("sourceIds", []) + [channel["epgId"]]
    return next(
        (source.get(normalize_schedule_key(candidate)) for candidate in candidate_ids
         if source.get(normalize_schedule_key(candidate)) is not None),
        None,
    )


def secondary_fanart_index(schedule):
    result = defaultdict(list)
    if schedule is None:
        return result
    for program in schedule.programs:
        image_url = valid_image_url(program[3])
        title_key = normalize_program_title(program[0])
        if image_url and title_key:
            result[title_key].append(program)
    return result


def select_secondary_fanart(program, candidates):
    best_url = ""
    best_overlap = 0.0
    closest_url = ""
    closest_start_difference = float("inf")
    for candidate in candidates:
        overlap = min(program[5], candidate[5]) - max(program[4], candidate[4])
        overlap_seconds = overlap.total_seconds()
        if overlap_seconds > best_overlap:
            best_url = valid_image_url(candidate[3])
            best_overlap = overlap_seconds
        start_difference = abs((program[4] - candidate[4]).total_seconds())
        if start_difference < closest_start_difference:
            closest_url = valid_image_url(candidate[3])
            closest_start_difference = start_difference
    return best_url or closest_url


def build_fanart_entries(compact, channels, fanart_schedules):
    entries = []
    fanart_source = normalized_index(fanart_schedules)
    for channel in channels:
        epg_id = channel["epgId"]
        schedule = compact.get(epg_id)
        if schedule is None:
            continue
        secondary = secondary_fanart_index(
            fanart_schedule_for_channel(fanart_source, channel))
        for program in schedule.programs:
            image_url = valid_image_url(program[3])
            if not image_url:
                title_key = normalize_program_title(program[0])
                image_url = select_secondary_fanart(program, secondary.get(title_key, []))
            if image_url:
                entries.append((epg_id, int(program[4].timestamp() * 1000), image_url))
    entries.sort(key=lambda entry: (entry[0], entry[1], entry[2]))
    return entries


def write_binary_string(stream, value):
    encoded = value.encode("utf-8")
    stream.write(struct.pack(">i", len(encoded)))
    stream.write(encoded)


def write_fanart_gzip(entries, destination):
    with destination.open("wb") as output_stream:
        with gzip.GzipFile(fileobj=output_stream, mode="wb", filename="", mtime=0) as stream:
            stream.write(struct.pack(">iii", FANART_MAGIC, FANART_VERSION, len(entries)))
            for channel_id, start_ms, image_url in entries:
                write_binary_string(stream, channel_id)
                stream.write(struct.pack(">q", start_ms))
                write_binary_string(stream, image_url)


def compact_schedules(schedules, channels):
    source = normalized_index(schedules)
    compact = {}
    missing = []
    for channel in channels:
        candidate_ids = channel.get("sourceIds", []) + [channel["epgId"]]
        schedule = next(
            (source.get(normalize_schedule_key(candidate)) for candidate in candidate_ids
             if source.get(normalize_schedule_key(candidate)) is not None),
            None,
        )
        if schedule is None or not schedule.programs:
            missing.append(channel["name"])
            continue
        keys = [channel["epgId"], normalize_schedule_key(channel["epgId"])]
        keys.extend(channel.get("aliases", []))
        for key in keys:
            if key:
                compact[key] = schedule
    if missing:
        raise RuntimeError("Missing generalist schedules: " + ", ".join(missing))
    return compact


def gzip_file(source, destination):
    with source.open("rb") as input_stream, destination.open("wb") as output_stream:
        with gzip.GzipFile(fileobj=output_stream, mode="wb", filename="", mtime=0) as gzip_stream:
            shutil.copyfileobj(input_stream, gzip_stream)


def xmltv_time(value):
    return value.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S %z")


def write_compact_xml_gzip(schedules, channels, destination):
    root = ET.Element("tv", {"generator-info-name": "TdTWorld compact EPG"})
    for channel in channels:
        epg_id = channel["epgId"]
        schedule = schedules.get(epg_id)
        if schedule is None:
            raise RuntimeError(f"Missing compact XML schedule: {channel['name']}")
        channel_element = ET.SubElement(root, "channel", {"id": epg_id})
        ET.SubElement(channel_element, "display-name").text = channel["name"]
        for program in schedule.programs:
            programme_element = ET.SubElement(root, "programme", {
                "start": xmltv_time(program[4]),
                "stop": xmltv_time(program[5]),
                "channel": epg_id,
            })
            ET.SubElement(programme_element, "title").text = program[0]
            if program[1]:
                ET.SubElement(programme_element, "desc").text = program[1]
            if program[2]:
                ET.SubElement(programme_element, "category").text = program[2]
            if program[3]:
                ET.SubElement(programme_element, "icon", {"src": program[3]})
    with destination.open("wb") as output_stream:
        with gzip.GzipFile(fileobj=output_stream, mode="wb", filename="", mtime=0) as gzip_stream:
            ET.ElementTree(root).write(gzip_stream, encoding="utf-8", xml_declaration=True)


def main():
    args = parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    if args.country not in config:
        raise RuntimeError(f"Unknown compact EPG country: {args.country}")
    if args.input_binary:
        schedules = read_binary_schedules(args.input_binary.resolve())
        source_description = "bundled binary"
    else:
        schedules = parse_xmltv_schedules(args.input.resolve().as_uri())
        source_description = "XMLTV source"
    compact = compact_schedules(schedules, config[args.country])
    fanart_schedules = schedules
    if args.fanart_input:
        fanart_schedules = parse_xmltv_schedules(args.fanart_input.resolve().as_uri())

    output_dir = args.output_dir.resolve() / "epg" / args.country
    output_dir.mkdir(parents=True, exist_ok=True)
    binary_path = output_dir / "epg.bin"
    compressed_path = output_dir / "epg.bin.gz"
    write_epg(binary_path, compact)
    gzip_file(binary_path, compressed_path)
    write_compact_xml_gzip(compact, config[args.country], output_dir / "guide.xml.gz")
    fanart_entries = build_fanart_entries(
        compact,
        config[args.country],
        fanart_schedules,
    )
    fanart_path = output_dir / "fanart.bin.gz"
    write_fanart_gzip(fanart_entries, fanart_path)

    version = hashlib.sha256(compressed_path.read_bytes()).hexdigest()
    unique_schedules = {id(schedule): schedule for schedule in compact.values()}
    marker = {
        "version": version,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "country": args.country,
        "channels": len(unique_schedules),
        "programs": sum(len(schedule.programs) for schedule in unique_schedules.values()),
        "compressedBytes": compressed_path.stat().st_size,
        "uncompressedBytes": binary_path.stat().st_size,
        "source": source_description,
        "fanartVersion": hashlib.sha256(fanart_path.read_bytes()).hexdigest(),
        "fanartPrograms": len(fanart_entries),
        "fanartCompressedBytes": fanart_path.stat().st_size,
    }
    (output_dir / "version.json").write_text(
        json.dumps(marker, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if args.asset_output:
        args.asset_output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(binary_path, args.asset_output)
    binary_path.unlink()
    print(json.dumps(marker, ensure_ascii=False))


if __name__ == "__main__":
    main()
