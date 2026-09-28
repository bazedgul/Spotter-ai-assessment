"""Geocoding service for user-provided USA start and finish locations.

Requirements from ASSESSMENT_PLAN.md:
- Resolves user-provided USA start and finish locations.
- Caches repeated identical geocoding requests via Django cache framework.
- Clearly distinguishes:
    * exact / address-level result (precision="exact", source="census_geocoder")
    * approximate / fallback result (precision="approximate", source="census_gazetteer")
    * unresolved result (raises UnresolvedLocationError)
- Does NOT blindly reuse city-centroid fallback for unresolvable user street addresses.
- Rejects non-USA inputs and addresses outside the USA.
- Centralized configuration via settings / environment variables.
- Strictly uses US Census Geocoder and Census Gazetteer (no Nominatim or unauthorized providers).
- Testable independently from API views.
"""

import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

import httpx
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

# Standard US States + DC
US_STATES = {
    'AL', 'AK', 'AZ', 'AR', 'CA', 'CO', 'CT', 'DE', 'FL', 'GA',
    'HI', 'ID', 'IL', 'IN', 'IA', 'KS', 'KY', 'LA', 'ME', 'MD',
    'MA', 'MI', 'MN', 'MS', 'MO', 'MT', 'NE', 'NV', 'NH', 'NJ',
    'NM', 'NY', 'NC', 'ND', 'OH', 'OK', 'OR', 'PA', 'RI', 'SC',
    'SD', 'TN', 'TX', 'UT', 'VT', 'VA', 'WA', 'WV', 'WI', 'WY',
    'DC'
}

# Full state name mapping to 2-letter postal abbreviation
STATE_NAMES_MAP = {
    'alabama': 'AL', 'alaska': 'AK', 'arizona': 'AZ', 'arkansas': 'AR',
    'california': 'CA', 'colorado': 'CO', 'connecticut': 'CT', 'delaware': 'DE',
    'florida': 'FL', 'georgia': 'GA', 'hawaii': 'HI', 'idaho': 'ID',
    'illinois': 'IL', 'indiana': 'IN', 'iowa': 'IA', 'kansas': 'KS',
    'kentucky': 'KY', 'louisiana': 'LA', 'maine': 'ME', 'maryland': 'MD',
    'massachusetts': 'MA', 'michigan': 'MI', 'minnesota': 'MN', 'mississippi': 'MS',
    'missouri': 'MO', 'montana': 'MT', 'nebraska': 'NE', 'nevada': 'NV',
    'new hampshire': 'NH', 'new jersey': 'NJ', 'new mexico': 'NM', 'new york': 'NY',
    'north carolina': 'NC', 'north dakota': 'ND', 'ohio': 'OH', 'oklahoma': 'OK',
    'oregon': 'OR', 'pennsylvania': 'PA', 'rhode island': 'RI', 'south carolina': 'SC',
    'south dakota': 'SD', 'tennessee': 'TN', 'texas': 'TX', 'utah': 'UT',
    'vermont': 'VT', 'virginia': 'VA', 'washington': 'WA', 'west virginia': 'WV',
    'wisconsin': 'WI', 'wyoming': 'WY', 'district of columbia': 'DC'
}

# Canadian provinces and territories to reject cleanly
CANADIAN_PROVINCES = {
    'AB', 'BC', 'MB', 'NB', 'NL', 'NS', 'NT', 'NU', 'ON', 'PE', 'QC', 'SK', 'YT'
}

# Approximate geographic bounding box for USA territory (CONUS + AK + HI + PR/territories)
USA_LAT_MIN = 17.0
USA_LAT_MAX = 72.0
USA_LON_MIN = -180.0
USA_LON_MAX = -65.0


class GeocodingError(Exception):
    """Base exception for geocoding failures."""
    pass


class InvalidLocationError(GeocodingError):
    """Raised when an input is empty, malformed, or explicitly outside the USA."""
    pass


class UnresolvedLocationError(GeocodingError):
    """Raised when a location could not be reliably resolved by Census geocoding resources."""
    pass


class GeocodingConnectionError(GeocodingError):
    """Raised when the Census Geocoder service cannot be reached or times out."""
    pass


@dataclass
class GeocodingResult:
    """Normalized geocoding result with explicit precision level and metadata."""
    input_text: str
    latitude: float
    longitude: float
    precision: str  # 'exact' (address-level), 'approximate' (place/city centroid), or 'coordinates'
    matched_address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    source: str = "census"  # 'census_geocoder', 'census_gazetteer', or 'coordinates'

    def to_dict(self) -> Dict[str, Any]:
        """Serializes the result to a JSON-compatible dictionary."""
        return {
            "input": self.input_text,
            "latitude": round(self.latitude, 6),
            "longitude": round(self.longitude, 6),
            "precision": self.precision,
            "matched_address": self.matched_address,
            "city": self.city,
            "state": self.state,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GeocodingResult":
        """Reconstructs GeocodingResult from dictionary."""
        return cls(
            input_text=data["input"],
            latitude=float(data["latitude"]),
            longitude=float(data["longitude"]),
            precision=data.get("precision", "approximate"),
            matched_address=data.get("matched_address"),
            city=data.get("city"),
            state=data.get("state"),
            source=data.get("source", "census"),
        )


class GeocodingService:
    """Geocoding service for start and finish locations using US Census sources."""

    _gazetteer_index: Optional[Dict[Tuple[str, str], Dict[str, Any]]] = None

    def __init__(
        self,
        census_url: Optional[str] = None,
        gazetteer_path: Optional[str] = None,
        timeout: Optional[float] = None,
        client: Optional[httpx.Client] = None
    ):
        """Initializes geocoding service with configurable endpoint, gazetteer path, and timeout."""
        self.census_url = census_url or getattr(
            settings,
            "CENSUS_GEOCODER_URL",
            "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
        )
        self.gazetteer_path = gazetteer_path or getattr(
            settings,
            "CENSUS_GAZETTEER_PATH",
            os.path.join(settings.BASE_DIR, "data", "2023_Gaz_place_national.txt")
        )
        self.timeout = timeout if timeout is not None else getattr(settings, "GEOCODING_TIMEOUT_SECONDS", 15.0)
        self._external_client = client

    def _get_gazetteer_index(self) -> Dict[Tuple[str, str], Dict[str, Any]]:
        """Loads and indexes the US Census Gazetteer Places file (lazy-loaded singleton)."""
        if GeocodingService._gazetteer_index is not None:
            return GeocodingService._gazetteer_index

        index: Dict[Tuple[str, str], Dict[str, Any]] = {}
        if not os.path.exists(self.gazetteer_path):
            logger.warning(f"Census Gazetteer Places file not found at: {self.gazetteer_path}")
            GeocodingService._gazetteer_index = index
            return index

        logger.info(f"Loading Census Gazetteer from: {self.gazetteer_path}")
        with open(self.gazetteer_path, 'r', encoding='utf-8', errors='replace') as f:
            f.readline()  # Skip TSV header
            for line in f:
                parts = line.strip().split('\t')
                if len(parts) >= 12:
                    st = parts[0].strip().upper()
                    full_name = parts[3].strip()
                    # Strip municipal designations
                    clean_name = re.sub(
                        r'\s+(city|town|village|CDP|borough|municipality|\(balance\))$',
                        '',
                        full_name,
                        flags=re.IGNORECASE
                    ).strip().lower()
                    norm_name = re.sub(r'[^a-z0-9]', '', clean_name)

                    try:
                        lat = float(parts[10].strip())
                        lon = float(parts[11].strip())
                        sqmi = float(parts[8].strip())
                        entry = {'lat': lat, 'lon': lon, 'sqmi': sqmi, 'full_name': full_name}

                        # Store under clean name and normalized name
                        key_clean = (st, clean_name)
                        if key_clean not in index or sqmi > index[key_clean]['sqmi']:
                            index[key_clean] = entry

                        key_norm = (st, norm_name)
                        if key_norm not in index or sqmi > index[key_norm]['sqmi']:
                            index[key_norm] = entry

                        # Sub-names for compound places (e.g. Downieville-Lawson-Dumont)
                        if '-' in clean_name:
                            for part in clean_name.split('-'):
                                part = part.strip()
                                p_norm = re.sub(r'[^a-z0-9]', '', part)
                                if len(p_norm) >= 3:
                                    k = (st, p_norm)
                                    if k not in index or sqmi > index[k]['sqmi']:
                                        index[k] = entry
                    except ValueError:
                        continue

        logger.info(f"Successfully loaded {len(index)} municipal index entries from Census Gazetteer.")
        GeocodingService._gazetteer_index = index
        return index

    def _normalize_string(self, text: str) -> str:
        """Normalizes whitespace and capitalization in a location string."""
        return re.sub(r'\s+', ' ', text).strip()

    def _parse_coordinates(self, text: str) -> Optional[Tuple[float, float]]:
        """Checks if input string is directly formatted as numeric coordinates (lat, lon)."""
        pattern = r'^([-+]?\d{1,2}(?:\.\d+)?)[,\s]+([-+]?\d{1,3}(?:\.\d+)?)$'
        match = re.match(pattern, text.strip())
        if match:
            try:
                lat = float(match.group(1))
                lon = float(match.group(2))
                if -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0:
                    return lat, lon
            except ValueError:
                pass
        return None

    def _is_street_address(self, text: str) -> bool:
        """Determines whether a location string represents a specific street address with house numbers."""
        # Street addresses typically start with digits (e.g., '1600 Pennsylvania Ave') or have unit/box
        clean = text.strip()
        if re.match(r'^\d+[\w-]*\s+', clean):
            return True
        if re.search(r'\b(p\.?o\.?\s*box|suite|apt|ste|building|bldg)\b', clean, re.IGNORECASE):
            return True
        return False

    def _parse_city_state(self, text: str) -> Optional[Tuple[str, str]]:
        """Attempts to parse a clean City and State from a location string."""
        clean = self._normalize_string(text)

        # Remove optional trailing 5-digit ZIP code (e.g. 'Los Angeles, CA 90001' -> 'Los Angeles, CA')
        clean_no_zip = re.sub(r'\s+\d{5}(?:-\d{4})?$', '', clean).strip()

        # Match "City, State"
        pattern = r'^([A-Za-z\s\.\'-]+),\s*([A-Za-z\s\.\'-]+)$'
        match = re.match(pattern, clean_no_zip)
        if not match:
            return None

        raw_city = match.group(1).strip()
        raw_state = match.group(2).strip()

        # Check for Canadian province / foreign indicator
        state_upper = raw_state.upper()
        if state_upper in CANADIAN_PROVINCES:
            raise InvalidLocationError(
                f"Location '{text}' specifies Canadian province '{state_upper}'. Only USA locations are supported."
            )

        # Normalize state
        state_code = None
        if state_upper in US_STATES:
            state_code = state_upper
        else:
            state_lower = raw_state.lower()
            if state_lower in STATE_NAMES_MAP:
                state_code = STATE_NAMES_MAP[state_lower]

        if not state_code:
            return None

        return raw_city, state_code

    def _lookup_gazetteer(self, city: str, state: str) -> Optional[Dict[str, Any]]:
        """Searches the Census Gazetteer index for a city/state match with spelling variations."""
        index = self._get_gazetteer_index()
        st = state.upper()

        clean = re.sub(
            r'\s+(city|town|village|CDP|borough|municipality)$',
            '',
            city,
            flags=re.IGNORECASE
        ).strip().lower()

        # Direct clean match
        if (st, clean) in index:
            return index[(st, clean)]

        # Normalized alphanumeric match
        norm = re.sub(r'[^a-z0-9]', '', clean)
        if (st, norm) in index:
            return index[(st, norm)]

        # Saint / St alias
        if clean.startswith("st.") or clean.startswith("st "):
            alt = re.sub(r'^st\.?\s+', 'saint ', clean)
            alt_norm = re.sub(r'[^a-z0-9]', '', alt)
            if (st, alt) in index:
                return index[(st, alt)]
            if (st, alt_norm) in index:
                return index[(st, alt_norm)]
        elif clean.startswith("saint "):
            alt = re.sub(r'^saint\s+', 'st ', clean)
            alt_norm = re.sub(r'[^a-z0-9]', '', alt)
            if (st, alt) in index:
                return index[(st, alt)]
            if (st, alt_norm) in index:
                return index[(st, alt_norm)]

        # Fort / Ft alias
        if clean.startswith("ft.") or clean.startswith("ft "):
            alt = re.sub(r'^ft\.?\s+', 'fort ', clean)
            if (st, alt) in index:
                return index[(st, alt)]
        elif clean.startswith("fort "):
            alt = re.sub(r'^fort\s+', 'ft ', clean)
            if (st, alt) in index:
                return index[(st, alt)]

        # Mount / Mt alias
        if clean.startswith("mt.") or clean.startswith("mt "):
            alt = re.sub(r'^mt\.?\s+', 'mount ', clean)
            if (st, alt) in index:
                return index[(st, alt)]
        elif clean.startswith("mount "):
            alt = re.sub(r'^mount\s+', 'mt ', clean)
            if (st, alt) in index:
                return index[(st, alt)]

        return None

    def _query_census_geocoder(self, address_text: str) -> Optional[GeocodingResult]:
        """Queries the official US Census onelineaddress geocoder endpoint."""
        params = {
            "address": address_text,
            "benchmark": "Public_AR_Current",
            "format": "json"
        }

        logger.info(f"Querying US Census Geocoder: {address_text}")
        try:
            if self._external_client:
                response = self._external_client.get(self.census_url, params=params, timeout=self.timeout)
            else:
                with httpx.Client(timeout=self.timeout) as client:
                    response = client.get(self.census_url, params=params)
        except httpx.TimeoutException as exc:
            logger.error(f"US Census Geocoder timed out after {self.timeout}s: {exc}")
            raise GeocodingConnectionError(
                f"US Census Geocoder service timed out after {self.timeout}s."
            ) from exc
        except httpx.RequestError as exc:
            logger.error(f"US Census Geocoder network error: {exc}")
            raise GeocodingConnectionError(
                f"Failed to connect to US Census Geocoder service: {exc}"
            ) from exc

        if response.status_code != 200:
            logger.error(f"US Census Geocoder returned HTTP {response.status_code}: {response.text[:200]}")
            raise GeocodingConnectionError(
                f"US Census Geocoder returned HTTP {response.status_code}."
            )

        try:
            data = response.json()
        except Exception as exc:
            logger.error(f"Failed to parse US Census Geocoder JSON response: {exc}")
            raise GeocodingError("Invalid JSON returned by US Census Geocoder.") from exc

        matches = data.get("result", {}).get("addressMatches", [])
        if not matches:
            return None

        # Take primary address match
        top_match = matches[0]
        coords = top_match.get("coordinates", {})
        lon = float(coords.get("x"))
        lat = float(coords.get("y"))
        matched_addr = top_match.get("matchedAddress", address_text)
        addr_comps = top_match.get("addressComponents", {})
        state = addr_comps.get("state")
        city = addr_comps.get("city")

        # Verify within USA states
        if state and state.upper() not in US_STATES:
            raise InvalidLocationError(
                f"Matched address '{matched_addr}' is in region '{state}', which is outside the supported USA states."
            )

        return GeocodingResult(
            input_text=address_text,
            latitude=lat,
            longitude=lon,
            precision="exact",
            matched_address=matched_addr,
            city=city,
            state=state,
            source="census_geocoder",
        )

    def geocode(self, location: str, use_cache: bool = True) -> GeocodingResult:
        """Resolves a location string to geographic coordinates with precision metadata.

        Args:
            location: Address string, City/State, or coordinate string.
            use_cache: Whether to read/write results to Django cache.

        Returns:
            GeocodingResult with coordinates, precision ('exact', 'approximate', 'coordinates'), and source.

        Raises:
            InvalidLocationError: If empty, malformed, or outside USA.
            UnresolvedLocationError: If location cannot be resolved.
            GeocodingConnectionError: If Census geocoder network call fails.
        """
        if not location or not location.strip():
            raise InvalidLocationError("Location cannot be empty or blank.")

        norm_loc = self._normalize_string(location)
        clean_key = re.sub(r'[^a-zA-Z0-9_-]', '_', norm_loc.lower())
        cache_key = f"geocode:{clean_key}"

        if use_cache:
            cached_data = cache.get(cache_key)
            if cached_data is not None:
                logger.debug(f"Geocoding cache hit for key: {cache_key}")
                return GeocodingResult.from_dict(cached_data)

        # 1. Check direct coordinates input (lat, lon)
        coords = self._parse_coordinates(norm_loc)
        if coords:
            lat, lon = coords
            if not (USA_LAT_MIN <= lat <= USA_LAT_MAX and USA_LON_MIN <= lon <= USA_LON_MAX):
                raise InvalidLocationError(
                    f"Coordinates ({lat}, {lon}) are outside the United States territorial boundary."
                )
            result = GeocodingResult(
                input_text=location,
                latitude=lat,
                longitude=lon,
                precision="coordinates",
                matched_address=f"{lat:.6f}, {lon:.6f}",
                source="coordinates",
            )
            if use_cache:
                cache.set(cache_key, result.to_dict(), timeout=getattr(settings, "CACHES", {}).get("default", {}).get("TIMEOUT", 86400))
            return result

        # 2. Check if input is a City, State without a specific street address
        is_street = self._is_street_address(norm_loc)
        city_state = self._parse_city_state(norm_loc)

        if not is_street and city_state:
            city, state = city_state
            gaz_entry = self._lookup_gazetteer(city, state)
            if gaz_entry:
                result = GeocodingResult(
                    input_text=location,
                    latitude=gaz_entry["lat"],
                    longitude=gaz_entry["lon"],
                    precision="approximate",
                    matched_address=f"{gaz_entry['full_name']}, {state}",
                    city=city,
                    state=state,
                    source="census_gazetteer",
                )
                if use_cache:
                    cache.set(cache_key, result.to_dict(), timeout=getattr(settings, "CACHES", {}).get("default", {}).get("TIMEOUT", 86400))
                return result

        # 3. For street addresses (or city/states not found in gazetteer), query US Census Geocoder
        census_result = self._query_census_geocoder(norm_loc)
        if census_result:
            if use_cache:
                cache.set(cache_key, census_result.to_dict(), timeout=getattr(settings, "CACHES", {}).get("default", {}).get("TIMEOUT", 86400))
            return census_result

        # 4. Strict handling: If user provided a specific street address and Census failed,
        # DO NOT blindly fall back to city center (violates rule 4).
        if is_street:
            raise UnresolvedLocationError(
                f"Could not resolve street address: '{location}'. Please check the street name and number."
            )

        # 5. If city/state was not found in Gazetteer nor Census Geocoder
        raise UnresolvedLocationError(
            f"Unable to resolve location '{location}'. Please provide a valid USA city and state (e.g. 'Los Angeles, CA') or full street address."
        )
