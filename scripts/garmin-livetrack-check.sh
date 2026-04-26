#!/usr/bin/env bash
# garmin-livetrack-check.sh — sanity check that Garmin LiveTrack endpoints still work
# Usage:  ./garmin-livetrack-check.sh "https://livetrack.garmin.com/session/<id>/token/<token>"

set -e

URL="${1:-}"
if [[ -z "$URL" ]]; then
  echo "Usage: $0 <livetrack-url>"
  exit 1
fi

python3 - "$URL" <<'PY'
import sys, re, json, time
try:
    import cloudscraper
except ImportError:
    print("Install cloudscraper:  pip install cloudscraper --break-system-packages")
    sys.exit(1)

url = sys.argv[1]
m = re.match(r'https://livetrack\.garmin\.com/session/([0-9a-f-]+)/token/([0-9A-Za-z]+)', url)
if not m:
    print(f"✗ Invalid LiveTrack URL: {url}")
    sys.exit(1)
sid, tok = m.group(1), m.group(2)
BASE = 'https://livetrack.garmin.com'

s = cloudscraper.create_scraper()
ok = lambda b: '✓' if b else '✗'

# 1. CSRF from /
t0 = time.time()
r = s.get(f'{BASE}/', timeout=15)
m = re.search(rb'name="csrf-token"[^>]*content="([0-9a-f-]+)"', r.content)
csrf = m.group(1).decode() if m else None
print(f'{ok(csrf)} CSRF      HTTP {r.status_code}  {len(r.content)//1024} KB  {(time.time()-t0)*1000:.0f} ms  {csrf or "NOT FOUND"}')
if not csrf: sys.exit(1)
hdrs = {'accept':'application/json','referer':f'{BASE}/','livetrack-csrf-token':csrf}

# 2. Session
t0 = time.time()
r = s.get(f'{BASE}/api/sessions/{sid}?token={tok}', headers=hdrs, timeout=10)
ms = (time.time()-t0)*1000
if r.status_code == 200:
    d = r.json()
    print(f'✓ Session   HTTP 200  {len(r.content)} B  {ms:.0f} ms  user={d.get("userDisplayName")!r} freq={d.get("postTrackPointFrequency")}s')
    print(f'            start={d.get("start")}  end={d.get("end")}')
else:
    print(f'✗ Session   HTTP {r.status_code}  {ms:.0f} ms  {r.text[:120]}')
    sys.exit(1)

# 3. Track-points
t0 = time.time()
r = s.get(f'{BASE}/api/sessions/{sid}/track-points/common?token={tok}', headers=hdrs, timeout=10)
ms = (time.time()-t0)*1000
if r.status_code == 200:
    pts = r.json().get('trackPoints', [])
    last = pts[-1] if pts else None
    print(f'✓ Points    HTTP 200  {len(r.content)} B  {ms:.0f} ms  count={len(pts)}')
    if last:
        pos = last.get('position') or {}
        print(f'            last={last.get("dateTime")} lat={pos.get("lat")} lon={pos.get("lon")} act={last.get("activityType")}')
else:
    print(f'✗ Points    HTTP {r.status_code}  {ms:.0f} ms')
    sys.exit(1)

print('\nAll endpoints OK ✓')
PY
