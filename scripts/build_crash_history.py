"""
Build data/crash_history.csv.gz from NHTSA FARS national CSV downloads.

FARS (Fatality Analysis Reporting System) records every fatal crash on US
public roads with its coordinates. Download the yearly national CSV zips from
https://static.nhtsa.gov/nhtsa/downloads/FARS/<YEAR>/National/FARS<YEAR>NationalCSV.zip
and pass them to this script:

    python scripts/build_crash_history.py FARS2019.zip FARS2020.zip ...

Output columns: lat, lng, year, fatals, road
"""

import csv
import gzip
import io
import os
import sys
import zipfile

OUTPUT = os.path.join(os.path.dirname(__file__), '..', 'data', 'crash_history.csv.gz')


def read_crashes(zip_path: str):
    """Yield (lat, lng, year, fatals, road) for each crash with valid coordinates."""
    with zipfile.ZipFile(zip_path) as z:
        name = next(n for n in z.namelist() if os.path.basename(n).lower() == 'accident.csv')
        with z.open(name) as f:
            for row in csv.DictReader(io.TextIOWrapper(f, encoding='latin-1')):
                try:
                    lat, lng = float(row['LATITUDE']), float(row['LONGITUD'])
                except ValueError:
                    continue
                # FARS codes unknown coordinates as 77.7777, 88.8888, 99.9999 (and 777.7777...)
                if not (-90 < lat < 77 and -180 < lng < 0):
                    continue
                yield (round(lat, 5), round(lng, 5), int(row['YEAR']),
                       int(row['FATALS']), row['TWAY_ID'].strip())


def main(zip_paths: list):
    os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)
    count = 0
    with gzip.open(OUTPUT, 'wt', newline='') as out:
        writer = csv.writer(out)
        writer.writerow(['lat', 'lng', 'year', 'fatals', 'road'])
        for path in zip_paths:
            for crash in read_crashes(path):
                writer.writerow(crash)
                count += 1
    print(f"Wrote {count} crashes to {os.path.normpath(OUTPUT)}")


if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    main(sys.argv[1:])
