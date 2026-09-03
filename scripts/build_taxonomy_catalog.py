#!/usr/bin/env python3
"""Build the compact offline location and education suggestion catalog.

Location data comes from GeoNames under CC BY 4.0. Education fields come
from the NCES Classification of Instructional Programs 2020 taxonomy.
This development script is not used by the installed runtime.
"""

import argparse
import csv
import io
import json
import zipfile
from pathlib import Path


def clean_code(value: str) -> str:
    return value.strip().removeprefix('="').removesuffix('"')


def build_catalog(
    cities_zip: Path,
    country_info: Path,
    admin1_codes: Path,
    cip_codes: Path,
) -> dict:
    countries = []
    country_names = {}
    with country_info.open(encoding="utf-8") as handle:
        for row in csv.reader(handle, dialect="excel-tab"):
            if not row or row[0].startswith("#") or len(row) < 18:
                continue
            code, iso3, name = row[0], row[1], row[4]
            countries.append([code, name, iso3])
            country_names[code] = name

    regions = []
    region_names = {}
    with admin1_codes.open(encoding="utf-8") as handle:
        for row in csv.reader(handle, dialect="excel-tab"):
            if len(row) < 4 or "." not in row[0]:
                continue
            country_code, region_code = row[0].split(".", 1)
            if country_code not in country_names:
                continue
            name = row[1].strip()
            ascii_name = row[2].strip()
            regions.append([country_code, region_code, name, ascii_name if ascii_name != name else ""])
            region_names[(country_code, region_code)] = name

    cities = []
    with zipfile.ZipFile(cities_zip) as archive:
        with archive.open("cities15000.txt") as raw:
            reader = csv.reader(io.TextIOWrapper(raw, encoding="utf-8"), dialect="excel-tab")
            for row in reader:
                if len(row) < 19 or row[8] not in country_names:
                    continue
                name, ascii_name = row[1].strip(), row[2].strip()
                cities.append(
                    [
                        name,
                        row[8],
                        row[10],
                        int(row[14] or 0),
                        ascii_name if ascii_name != name else "",
                    ]
                )

    fields = []
    with cip_codes.open(encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            code = clean_code(row.get("CIPCode", ""))
            if len(code.replace(".", "")) != 6:
                continue
            title = " ".join(row.get("CIPTitle", "").strip().rstrip(".").split())
            if code and title:
                fields.append([code, title])

    countries.sort(key=lambda entry: entry[1].casefold())
    regions.sort(key=lambda entry: (country_names[entry[0]].casefold(), entry[2].casefold()))
    cities.sort(key=lambda entry: (-entry[3], entry[0].casefold(), entry[1], entry[2]))
    fields.sort(key=lambda entry: entry[1].casefold())
    return {
        "version": 1,
        "sources": {
            "locations": "GeoNames cities15000, countryInfo, and admin1CodesASCII (CC BY 4.0), https://www.geonames.org/export/",
            "education": "NCES Classification of Instructional Programs 2020, https://nces.ed.gov/ipeds/cipcode/",
        },
        "countries": countries,
        "regions": regions,
        "cities": cities,
        "education_fields": fields,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cities", type=Path, required=True)
    parser.add_argument("--countries", type=Path, required=True)
    parser.add_argument("--regions", type=Path, required=True)
    parser.add_argument("--cip", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = build_catalog(args.cities, args.countries, args.regions, args.cip)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
