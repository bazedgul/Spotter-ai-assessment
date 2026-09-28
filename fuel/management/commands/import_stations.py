"""
Management command to import fuel stations from CSV.

Requirements from ASSESSMENT_PLAN.md:
- Filter out non-USA records.
- Deduplicate stations: When multiple distinct prices exist for the same station ID,
  select the LOWEST supplied price (cost-effective fuel planning assumption).
- Primary geocoding: US Census Geocoder batch for address/intersection matches.
- Fallback geocoding: US Census Gazetteer Places dataset for city/state centroids.
- Precision tagging: 'address', 'city_fallback', or 'unresolved'.
- Caching: Avoid repeated network calls on subsequent runs.
- Idempotent: Safe to run repeatedly without creating duplicates.
- Keep source CSV completely unchanged.
"""

import csv
import io
import json
import logging
import os
import re
import time
import urllib.request
import zipfile
from decimal import Decimal
from typing import Dict, List, Optional, Tuple, Any

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from fuel.models import FuelStation

logger = logging.getLogger(__name__)

# Valid US States + DC
US_STATES = {
    'AL', 'AK', 'AZ', 'AR', 'CA', 'CO', 'CT', 'DE', 'FL', 'GA',
    'HI', 'ID', 'IL', 'IN', 'IA', 'KS', 'KY', 'LA', 'ME', 'MD',
    'MA', 'MI', 'MN', 'MS', 'MO', 'MT', 'NE', 'NV', 'NH', 'NJ',
    'NM', 'NY', 'NC', 'ND', 'OH', 'OK', 'OR', 'PA', 'RI', 'SC',
    'SD', 'TN', 'TX', 'UT', 'VT', 'VA', 'WA', 'WV', 'WI', 'WY',
    'DC'
}


class Command(BaseCommand):
    help = "Imports fuel stations from CSV, deduplicates, and resolves coordinates."

    def add_arguments(self, parser):
        parser.add_argument(
            '--csv',
            type=str,
            default=os.path.join(settings.BASE_DIR, 'data', 'fuel-prices-for-be-assessment.csv'),
            help='Path to source fuel prices CSV.'
        )
        parser.add_argument(
            '--cache-file',
            type=str,
            default=os.path.join(settings.BASE_DIR, 'data', 'geocoded_stations_cache.json'),
            help='Path to station geocoding cache file.'
        )
        parser.add_argument(
            '--gazetteer-file',
            type=str,
            default=os.path.join(settings.BASE_DIR, 'data', '2023_Gaz_place_national.txt'),
            help='Path to US Census Gazetteer Places file.'
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=1000,
            help='Batch size for US Census batch geocoder requests.'
        )
        parser.add_argument(
            '--no-network',
            action='store_true',
            help='Skip live network geocoding calls and rely only on cache and local Gazetteer.'
        )

    def handle(self, *args, **options):
        csv_path = options['csv']
        cache_path = options['cache_file']
        gazetteer_path = options['gazetteer_file']
        batch_size = options['batch_size']
        no_network = options['no_network']

        self.stdout.write(self.style.MIGRATE_HEADING("=== Starting Fuel Station Import ==="))
        self.stdout.write(f"Source CSV: {csv_path}")

        if not os.path.exists(csv_path):
            self.stderr.write(self.style.ERROR(f"Source CSV not found at: {csv_path}"))
            return

        # 1. Parse and validate CSV rows
        t0 = time.time()
        (
            grouped_stations,
            total_rows,
            usa_rows,
            non_usa_rows,
            non_usa_by_province,
            malformed_rows,
            identical_duplicate_rows,
            different_price_stations
        ) = self._parse_csv(csv_path)

        self.stdout.write(f"Parsed {total_rows} total rows in {time.time() - t0:.2f}s:")
        self.stdout.write(f"  - USA data rows: {usa_rows}")
        self.stdout.write(f"  - Non-USA rows excluded: {non_usa_rows}")
        self.stdout.write(f"  - Malformed rows skipped: {len(malformed_rows)}")
        self.stdout.write(f"  - Unique USA stations: {len(grouped_stations)}")
        self.stdout.write(f"  - Stations with multiple distinct prices: {len(different_price_stations)}")
        self.stdout.write(f"  - Identical duplicate rows collapsed: {identical_duplicate_rows}")

        # 2. Load Census Gazetteer for fallback
        gaz_lookup = self._load_or_fetch_gazetteer(gazetteer_path)

        # 3. Load or initialize geocoding cache
        geocode_cache = self._load_cache(cache_path)

        # 4. Resolve coordinates for all unique stations
        station_coords = self._resolve_station_coordinates(
            grouped_stations=grouped_stations,
            geocode_cache=geocode_cache,
            gaz_lookup=gaz_lookup,
            batch_size=batch_size,
            no_network=no_network,
            cache_path=cache_path
        )

        # 5. Persist to database idempotently
        created_count, updated_count = self._persist_stations(grouped_stations, station_coords)

        # 6. Report final summary statistics
        self._print_summary_report(
            total_rows=total_rows,
            usa_rows=usa_rows,
            non_usa_rows=non_usa_rows,
            non_usa_by_province=non_usa_by_province,
            malformed_rows=malformed_rows,
            unique_stations=len(grouped_stations),
            different_price_stations=different_price_stations,
            identical_duplicates=identical_duplicate_rows,
            created_count=created_count,
            updated_count=updated_count,
            station_coords=station_coords
        )

    def _parse_csv(self, csv_path: str):
        """Reads CSV, validates fields, filters non-USA records, and groups by station_id."""
        total_rows = 0
        usa_rows = 0
        non_usa_rows = 0
        non_usa_by_province = {}
        malformed_rows = []
        grouped_stations = {}
        identical_duplicate_rows = 0
        different_price_stations = set()

        with open(csv_path, 'r', encoding='utf-8', errors='replace') as f:
            reader = csv.DictReader(f)
            for row_idx, row in enumerate(reader, start=2):
                total_rows += 1
                raw_id = (row.get('OPIS Truckstop ID') or '').strip()
                raw_state = (row.get('State') or '').strip().upper()
                raw_price = (row.get('Retail Price') or '').strip()
                raw_name = (row.get('Truckstop Name') or '').strip()
                raw_addr = (row.get('Address') or '').strip()
                raw_city = (row.get('City') or '').strip()
                raw_rack = (row.get('Rack ID') or '').strip()

                # Validation checks
                if not raw_id:
                    malformed_rows.append((row_idx, "Missing OPIS Truckstop ID", row))
                    continue

                try:
                    station_id = int(raw_id)
                except ValueError:
                    malformed_rows.append((row_idx, f"Invalid station ID: '{raw_id}'", row))
                    continue

                if not raw_price:
                    malformed_rows.append((row_idx, "Missing Retail Price", row))
                    continue

                try:
                    price = Decimal(raw_price)
                    if price <= 0:
                        malformed_rows.append((row_idx, f"Non-positive price: {price}", row))
                        continue
                except Exception:
                    malformed_rows.append((row_idx, f"Invalid numeric price: '{raw_price}'", row))
                    continue

                if not raw_state:
                    malformed_rows.append((row_idx, "Missing State", row))
                    continue

                # Filter Non-USA records
                if raw_state not in US_STATES:
                    non_usa_rows += 1
                    non_usa_by_province[raw_state] = non_usa_by_province.get(raw_state, 0) + 1
                    continue

                usa_rows += 1
                rack_id_val = int(raw_rack) if raw_rack.isdigit() else None

                # Group by station_id
                if station_id not in grouped_stations:
                    grouped_stations[station_id] = {
                        'station_id': station_id,
                        'name': raw_name,
                        'address': raw_addr,
                        'city': raw_city,
                        'state': raw_state,
                        'rack_id': rack_id_val,
                        'prices': [price],
                        'rows': [(raw_name, raw_addr, raw_city, raw_state, price)]
                    }
                else:
                    existing = grouped_stations[station_id]
                    row_tuple = (raw_name, raw_addr, raw_city, raw_state, price)
                    if row_tuple in existing['rows']:
                        identical_duplicate_rows += 1
                    else:
                        existing['rows'].append(row_tuple)

                    if price not in existing['prices']:
                        different_price_stations.add(station_id)

                    existing['prices'].append(price)
                    # Keep shortest non-empty or cleanest attributes if previous was empty
                    if not existing['name'] and raw_name:
                        existing['name'] = raw_name
                    if not existing['address'] and raw_addr:
                        existing['address'] = raw_addr
                    if not existing['city'] and raw_city:
                        existing['city'] = raw_city

        return (
            grouped_stations,
            total_rows,
            usa_rows,
            non_usa_rows,
            non_usa_by_province,
            malformed_rows,
            identical_duplicate_rows,
            different_price_stations
        )

    def _load_or_fetch_gazetteer(self, gazetteer_path: str) -> Dict[Tuple[str, str], Dict[str, Any]]:
        """Loads Census Gazetteer Places file, downloading it if not present."""
        if not os.path.exists(gazetteer_path):
            self.stdout.write("Census Gazetteer Places file not found locally. Downloading official file...")
            url = 'https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2023_Gazetteer/2023_Gaz_place_national.zip'
            req = urllib.request.Request(url, headers={'User-Agent': 'SpotterAssessment/1.0'})
            try:
                with urllib.request.urlopen(req, timeout=45) as resp:
                    z = zipfile.ZipFile(io.BytesIO(resp.read()))
                    target_dir = os.path.dirname(gazetteer_path)
                    z.extractall(target_dir)
                    self.stdout.write(self.style.SUCCESS(f"Extracted Gazetteer to {target_dir}"))
            except Exception as e:
                self.stderr.write(self.style.WARNING(f"Failed to download Gazetteer: {e}. Fallback resolution may be limited."))
                return {}

        lookup = {}
        with open(gazetteer_path, 'r', encoding='utf-8', errors='replace') as f:
            f.readline()  # Skip TSV header
            for line in f:
                parts = line.strip().split('\t')
                if len(parts) >= 12:
                    st = parts[0].strip().upper()
                    full_name = parts[3].strip()
                    # Strip standard municipal suffix
                    clean_name = re.sub(
                        r'\s+(city|town|village|CDP|borough|municipality|\(balance\))$',
                        '',
                        full_name,
                        flags=re.IGNORECASE
                    ).strip().lower()
                    # Normalized alphanumeric name
                    norm_name = re.sub(r'[^a-z0-9]', '', clean_name)

                    try:
                        lat = float(parts[10].strip())
                        lon = float(parts[11].strip())
                        sqmi = float(parts[8].strip())
                        entry = {'lat': lat, 'lon': lon, 'sqmi': sqmi, 'full_name': full_name}

                        # Store under clean name and normalized name
                        key_clean = (st, clean_name)
                        if key_clean not in lookup or sqmi > lookup[key_clean]['sqmi']:
                            lookup[key_clean] = entry

                        key_norm = (st, norm_name)
                        if key_norm not in lookup or sqmi > lookup[key_norm]['sqmi']:
                            lookup[key_norm] = entry

                        # Compound names (e.g. Downieville-Lawson-Dumont -> Dumont, Butte-Silver Bow -> Butte)
                        if '-' in clean_name:
                            for part in clean_name.split('-'):
                                part = part.strip()
                                p_norm = re.sub(r'[^a-z0-9]', '', part)
                                if len(p_norm) >= 3:
                                    k = (st, p_norm)
                                    if k not in lookup or sqmi > lookup[k]['sqmi']:
                                        lookup[k] = entry
                    except ValueError:
                        continue

        self.stdout.write(f"Loaded {len(lookup)} municipal index entries from Census Gazetteer.")
        return lookup

    def _load_cache(self, cache_path: str) -> Dict[str, Dict[str, Any]]:
        """Loads cached station geocode coordinates if available."""
        if os.path.exists(cache_path):
            try:
                with open(cache_path, 'r', encoding='utf-8') as f:
                    cache = json.load(f)
                    self.stdout.write(f"Loaded {len(cache)} existing geocoded records from cache: {cache_path}")
                    return cache
            except Exception as e:
                self.stderr.write(self.style.WARNING(f"Could not read cache file ({e}). Starting fresh cache."))
        return {}

    def _save_cache(self, cache_path: str, cache: Dict[str, Dict[str, Any]]):
        """Saves station geocode coordinates to cache file."""
        try:
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            with open(cache_path, 'w', encoding='utf-8') as f:
                json.dump(cache, f, indent=2)
        except Exception as e:
            logger.warning(f"Could not write geocoding cache: {e}")

    def _resolve_station_coordinates(
        self,
        grouped_stations: Dict[int, Dict[str, Any]],
        geocode_cache: Dict[str, Dict[str, Any]],
        gaz_lookup: Dict[Tuple[str, str], Dict[str, Any]],
        batch_size: int,
        no_network: bool,
        cache_path: str
    ) -> Dict[int, Dict[str, Any]]:
        """
        Coordinates resolution pipeline:
        1. Check memory cache / existing DB stations.
        2. Unresolved stations sent via Census Batch Geocoder in chunks.
        3. Non-matches resolved via Census Gazetteer place centroids.
        4. Any remainder marked unresolved.
        """
        resolved = {}
        pending_census = []

        # 1. Check cache
        for tid, data in grouped_stations.items():
            s_tid = str(tid)
            if s_tid in geocode_cache:
                c = geocode_cache[s_tid]
                resolved[tid] = {
                    'latitude': c.get('latitude'),
                    'longitude': c.get('longitude'),
                    'precision': c.get('precision', FuelStation.GEOCODE_PRECISION_UNRESOLVED)
                }
            else:
                pending_census.append(data)

        self.stdout.write(f"Stations from cache: {len(resolved)} | Pending geocoding: {len(pending_census)}")

        # 2. Census Batch Geocoding for uncached stations
        if pending_census and not no_network:
            self.stdout.write(f"Submitting {len(pending_census)} stations to US Census Batch Geocoder (batch size: {batch_size})...")
            for i in range(0, len(pending_census), batch_size):
                chunk = pending_census[i:i + batch_size]
                chunk_num = (i // batch_size) + 1
                total_chunks = (len(pending_census) + batch_size - 1) // batch_size
                self.stdout.write(f"  Processing batch {chunk_num}/{total_chunks} ({len(chunk)} stations)...")

                batch_results = self._call_census_batch(chunk)
                for tid, res in batch_results.items():
                    if res['matched']:
                        resolved[tid] = {
                            'latitude': res['latitude'],
                            'longitude': res['longitude'],
                            'precision': FuelStation.GEOCODE_PRECISION_ADDRESS
                        }
                    # Non-matches will be resolved by Gazetteer fallback in step 3

        # 3. Fallback to Gazetteer for any station still without coordinates
        gaz_resolved_count = 0
        unresolved_count = 0

        for tid, data in grouped_stations.items():
            if tid in resolved and resolved[tid]['latitude'] is not None:
                continue

            city_clean = data['city'].strip().lower()
            city_norm = re.sub(r'[^a-z0-9]', '', city_clean)
            state = data['state'].strip().upper()

            # Gazetteer lookup
            gaz_match = (
                gaz_lookup.get((state, city_clean))
                or gaz_lookup.get((state, city_norm))
            )

            # Prefix / alias check if needed (e.g., "saint" -> "st")
            if not gaz_match:
                if city_clean.startswith('saint '):
                    alt_norm = 'st' + city_norm[5:]
                    gaz_match = gaz_lookup.get((state, alt_norm))
                elif city_clean.startswith('st. ') or city_clean.startswith('st '):
                    alt_norm = 'saint' + re.sub(r'^st\.?\s*', '', city_clean)
                    gaz_match = gaz_lookup.get((state, alt_norm))

            if gaz_match:
                resolved[tid] = {
                    'latitude': gaz_match['lat'],
                    'longitude': gaz_match['lon'],
                    'precision': FuelStation.GEOCODE_PRECISION_CITY_FALLBACK
                }
                gaz_resolved_count += 1
            else:
                resolved[tid] = {
                    'latitude': None,
                    'longitude': None,
                    'precision': FuelStation.GEOCODE_PRECISION_UNRESOLVED
                }
                unresolved_count += 1

        self.stdout.write(f"Fallback resolution: {gaz_resolved_count} matched via Census Gazetteer; {unresolved_count} unresolved.")

        # Update cache file with all resolved records
        for tid, res in resolved.items():
            geocode_cache[str(tid)] = res
        self._save_cache(cache_path, geocode_cache)

        return resolved

    def _call_census_batch(self, chunk: List[Dict[str, Any]]) -> Dict[int, Dict[str, Any]]:
        """Calls the live US Census Batch Geocoder with a list of stations."""
        url = "https://geocoding.geo.census.gov/geocoder/locations/addressbatch"
        lines = []
        for s in chunk:
            tid = s['station_id']
            # Clean commas to avoid disrupting CSV columns
            addr = s['address'].replace(',', ' ').replace('"', '').strip()
            city = s['city'].replace(',', ' ').replace('"', '').strip()
            state = s['state'].strip()
            lines.append(f"{tid},{addr},{city},{state},")

        csv_payload = "\n".join(lines) + "\n"
        boundary = "----SpotterBatchBoundary" + str(int(time.time()))
        body = []

        # addressFile part
        body.append(f"--{boundary}".encode('utf-8'))
        body.append(b'Content-Disposition: form-data; name="addressFile"; filename="batch.csv"')
        body.append(b'Content-Type: text/csv')
        body.append(b'')
        body.append(csv_payload.encode('utf-8'))

        # benchmark part
        body.append(f"--{boundary}".encode('utf-8'))
        body.append(b'Content-Disposition: form-data; name="benchmark"')
        body.append(b'')
        body.append(b'Public_AR_Current')

        body.append(f"--{boundary}--".encode('utf-8'))
        body.append(b'')
        payload_bytes = b"\r\n".join(body)

        req = urllib.request.Request(
            url,
            data=payload_bytes,
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "User-Agent": "SpotterAssessment/1.0"
            }
        )

        results = {}
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                content = resp.read().decode('utf-8', errors='replace')
                reader = csv.reader(io.StringIO(content))
                for row in reader:
                    if len(row) < 3:
                        continue
                    try:
                        tid = int(row[0].strip().replace('"', ''))
                    except ValueError:
                        continue

                    match_status = row[2].strip().replace('"', '')
                    if match_status == 'Match' and len(row) >= 6:
                        coords_str = row[5].strip().replace('"', '')
                        if ',' in coords_str:
                            lon_str, lat_str = coords_str.split(',', 1)
                            try:
                                lon = float(lon_str.strip())
                                lat = float(lat_str.strip())
                                results[tid] = {'matched': True, 'latitude': lat, 'longitude': lon}
                                continue
                            except ValueError:
                                pass
                    results[tid] = {'matched': False, 'latitude': None, 'longitude': None}
        except Exception as e:
            self.stderr.write(self.style.WARNING(f"Census batch request error: {e}. Falling back to Gazetteer."))

        return results

    def _persist_stations(
        self,
        grouped_stations: Dict[int, Dict[str, Any]],
        station_coords: Dict[int, Dict[str, Any]]
    ) -> Tuple[int, int]:
        """Persists all stations to the database using update_or_create to guarantee idempotency."""
        self.stdout.write("Persisting stations to database...")
        created_count = 0
        updated_count = 0

        existing_stations = {s.station_id: s for s in FuelStation.objects.all()}

        to_create = []
        to_update = []

        for tid, data in grouped_stations.items():
            # Business rule: lowest supplied price for duplicate station IDs
            lowest_price = min(data['prices'])
            coords = station_coords.get(tid, {})
            lat = coords.get('latitude')
            lon = coords.get('longitude')
            precision = coords.get('precision', FuelStation.GEOCODE_PRECISION_UNRESOLVED)

            if tid in existing_stations:
                st = existing_stations[tid]
                changed = False
                if st.price != lowest_price:
                    st.price = lowest_price
                    changed = True
                if st.name != data['name']:
                    st.name = data['name']
                    changed = True
                if st.address != data['address']:
                    st.address = data['address']
                    changed = True
                if st.city != data['city']:
                    st.city = data['city']
                    changed = True
                if st.state != data['state']:
                    st.state = data['state']
                    changed = True
                if st.latitude != lat or st.longitude != lon:
                    st.latitude = lat
                    st.longitude = lon
                    changed = True
                if st.geocode_precision != precision:
                    st.geocode_precision = precision
                    changed = True

                if changed:
                    to_update.append(st)
                updated_count += 1
            else:
                to_create.append(
                    FuelStation(
                        station_id=tid,
                        name=data['name'],
                        address=data['address'],
                        city=data['city'],
                        state=data['state'],
                        rack_id=data['rack_id'],
                        price=lowest_price,
                        latitude=lat,
                        longitude=lon,
                        geocode_precision=precision,
                        is_active=True,
                        source='opis_csv'
                    )
                )
                created_count += 1

        with transaction.atomic():
            if to_create:
                FuelStation.objects.bulk_create(to_create, batch_size=1000)
            if to_update:
                FuelStation.objects.bulk_update(
                    to_update,
                    fields=['name', 'address', 'city', 'state', 'price', 'latitude', 'longitude', 'geocode_precision'],
                    batch_size=1000
                )

        return created_count, updated_count

    def _print_summary_report(
        self,
        total_rows: int,
        usa_rows: int,
        non_usa_rows: int,
        non_usa_by_province: Dict[str, int],
        malformed_rows: List[Any],
        unique_stations: int,
        different_price_stations: set,
        identical_duplicates: int,
        created_count: int,
        updated_count: int,
        station_coords: Dict[int, Dict[str, Any]]
    ):
        """Prints a detailed tabular summary report matching assessment instructions."""
        address_count = sum(1 for c in station_coords.values() if c.get('precision') == FuelStation.GEOCODE_PRECISION_ADDRESS)
        fallback_count = sum(1 for c in station_coords.values() if c.get('precision') == FuelStation.GEOCODE_PRECISION_CITY_FALLBACK)
        unresolved_count = sum(1 for c in station_coords.values() if c.get('precision') == FuelStation.GEOCODE_PRECISION_UNRESOLVED)
        total_db_count = FuelStation.objects.count()

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("================================================================================"))
        self.stdout.write(self.style.SUCCESS("                      IMPORT_STATIONS SUMMARY REPORT                            "))
        self.stdout.write(self.style.SUCCESS("================================================================================"))
        self.stdout.write(f"Total Source CSV Rows Read:               {total_rows}")
        self.stdout.write(f"USA Rows Processed:                       {usa_rows}")
        self.stdout.write(f"Non-USA Rows Excluded:                    {non_usa_rows} ({non_usa_by_province})")
        self.stdout.write(f"Malformed / Invalid Rows Skipped:         {len(malformed_rows)}")
        self.stdout.write(f"Unique USA Stations Imported:             {unique_stations}")
        self.stdout.write("--------------------------------------------------------------------------------")
        self.stdout.write("DEDUPLICATION & PRICE NORMALIZATION:")
        self.stdout.write(f"  - Collapsed identical duplicate rows:   {identical_duplicates}")
        self.stdout.write(f"  - Duplicate stations with distinct prices: {len(different_price_stations)}")
        self.stdout.write("    (Rule: Lowest supplied price selected; CSV lacks timestamps per plan)")
        self.stdout.write("--------------------------------------------------------------------------------")
        self.stdout.write("GEOCODING RESOLUTION BREAKDOWN:")
        self.stdout.write(f"  - Exact Address / Intersection:         {address_count} ({address_count/unique_stations*100:.1f}%)")
        self.stdout.write(f"  - Census Gazetteer City Fallback:       {fallback_count} ({fallback_count/unique_stations*100:.1f}%)")
        self.stdout.write(f"  - Unresolved Coordinates:               {unresolved_count} ({unresolved_count/unique_stations*100:.1f}%)")
        self.stdout.write("--------------------------------------------------------------------------------")
        self.stdout.write("DATABASE PERSISTENCE (IDEMPOTENT):")
        self.stdout.write(f"  - Records Created:                      {created_count}")
        self.stdout.write(f"  - Records Updated / Verified:           {updated_count}")
        self.stdout.write(f"  - Total FuelStation Records in DB:      {total_db_count}")
        self.stdout.write(self.style.SUCCESS("================================================================================"))
