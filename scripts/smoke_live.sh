#!/usr/bin/env bash
# Probe a running CLSA_EQD (Cloud Run URL or revision tag URL).
# Exit 0 only when Corr, Yield, Exotic, QIS, and regime2 calendar-vol all respond.
set -euo pipefail

BASE="${1:-${SMOKE_BASE:-https://exotic-option-architect-peter-xikaiwang-ueoo2e3ouq-uw.a.run.app}}"
BASE="${BASE%/}"

curl_ok() {
  local path="$1"
  local url="${BASE}${path}"
  local code body
  local attempt
  for attempt in 1 2 3 4 5 6; do
    code="$(curl -sS -o /tmp/clsa_smoke_body -w '%{http_code}' --max-time 120 "$url" || true)"
    if [[ "$code" == "200" ]]; then
      echo "OK  $path"
      return 0
    fi
    echo "WAIT $path -> ${code:-curl-fail} (attempt $attempt)"
    sleep $((attempt * 3))
  done
  echo "FAIL $path -> ${code:-curl-fail}"
  head -c 300 /tmp/clsa_smoke_body 2>/dev/null || true
  echo
  return 1
}

echo "==> smoke $BASE"
curl_ok "/api/v1/correlation/universe"
curl_ok "/api/v1/market/yield-curve?market=CN&history=1W"
curl_ok "/api/v1/exotic/presets"
curl_ok "/api/v1/qis/strategies"
curl_ok "/api/v1/qis/backtest/equity_3_6_calendar_vol_300?signal_mode=regime2"
python3 - "$BASE" <<'PY'
import json, sys
body = open("/tmp/clsa_smoke_body").read()
data = json.loads(body)
mode = (data.get("params") or {}).get("signal_mode")
if mode != "regime2":
    sys.exit(f"regime2 backtest params.signal_mode={mode!r}")
print("OK  regime2 signal_mode")
PY
# Cold start may be slow on first miss; accept cached or live payload.
curl_ok "/api/v1/qis/backtest/csi300_vol_rv?tilt=balanced&universe=300"
python3 - "$BASE" <<'PY'
import json, sys
body = open("/tmp/clsa_smoke_body").read()
data = json.loads(body)
if data.get("strategy_id") != "csi300_vol_rv":
    sys.exit(f"vol_rv strategy_id={data.get('strategy_id')!r}")
if "ablation" not in data:
    sys.exit("vol_rv missing ablation")
print("OK  csi300_vol_rv balanced u=300")
PY
curl_ok "/api/v1/qis/backtest/csi300_vol_rv?tilt=balanced&universe=AU"
python3 - "$BASE" <<'PY'
import json, sys
body = open("/tmp/clsa_smoke_body").read()
data = json.loads(body)
u = (data.get("params") or {}).get("universe")
if u != "AU":
    sys.exit(f"vol_rv AU universe={u!r}")
print("OK  csi300_vol_rv balanced u=AU")
PY
curl_ok "/api/v1/qis/backtest/csi300_vol_rv?tilt=balanced&universe=SC"
python3 - "$BASE" <<'PY'
import json, sys
body = open("/tmp/clsa_smoke_body").read()
data = json.loads(body)
u = (data.get("params") or {}).get("universe")
if u != "SC":
    sys.exit(f"vol_rv SC universe={u!r}")
print("OK  csi300_vol_rv balanced u=SC")
PY
echo "==> smoke passed"
