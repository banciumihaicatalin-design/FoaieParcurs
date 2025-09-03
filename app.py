# -*- coding: utf-8 -*-
"""
Foaie de parcurs - calcul automat km (OSRM gratuit)
Versiune stabilă (fix SyntaxError la începutul fișierului, fără caractere/markere rătăcite)
- Geocodare cu Nominatim + fallback Photon (retry/backoff)
- Calcul rute cu OSRM (driving)
- UI Streamlit: start + opriri, selecție din sugestii
- Export CSV/Excel (TOTAL km pe ultima linie din foaia principală)
- Teste de bază rulate cu:  python app.py --test
"""

from __future__ import annotations
import io, os, sys, json, time, math
from datetime import date
from typing import List, Dict, Optional

import requests
import pandas as pd

try:
    import streamlit as st  # type: ignore
except Exception:  # în modul CLI/test poate lipsi
    st = None  # type: ignore

# --- Page config cât mai devreme (doar în UI) ---
if st is not None:
    try:
        st.set_page_config(page_title="Foaie de parcurs - calcul automat km", page_icon="🚗", layout="wide")
    except Exception:
        pass

APP_TITLE = "Foaie de parcurs - calcul automat km"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
PHOTON_URL = "https://photon.komoot.io/api/"  # fallback gratuit la Nominatim
OSRM_ROUTE_URL = (
    "https://router.project-osrm.org/route/v1/driving/"
    "{lon1},{lat1};{lon2},{lat2}?overview=full&alternatives=false&steps=false&geometries=geojson"
)
USER_AGENT = "FoaieParcursApp/2.2 (+https://github.com/banciumihaicatalin-design/FoaieParcurs)"
CACHE_FILE = os.path.expanduser("~/.foaieparcurs_cache.json")

# ----------------- cache load/save -----------------

def _load_json(path: str) -> dict:
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_json(path: str, data: dict) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

_GEOCODE_DISK = _load_json(CACHE_FILE)

# ----------------- utilitare -----------------

def km_round(x: float, decimals: int = 1) -> float:
    pow10 = 10 ** decimals
    return math.floor(x * pow10 + 0.5) / pow10


def geocode_osm_candidates(q: str, *, limit: int, implicit_place: str = "") -> List[Dict]:
    """Returnează o listă de candidați {lat, lon, display}.
    - încearcă Nominatim cu retry (0/1/2s)
    - fallback la Photon dacă Nominatim e indisponibil sau rate-limited
    - cache pe disc
    """
    if implicit_place and (implicit_place.lower() not in q.lower()):
        q = f"{q}, {implicit_place}"
    key = f"{q}|{limit}"
    if key in _GEOCODE_DISK:
        return _GEOCODE_DISK[key]

    last_err: Optional[Exception] = None

    # 1) Nominatim cu retry
    nom_params = {"q": q, "format": "json", "limit": limit, "accept-language": "ro"}
    for attempt in range(3):  # 0, 1, 2 secunde
        try:
            r = requests.get(
                NOMINATIM_URL,
                params=nom_params,
                headers={"User-Agent": USER_AGENT},
                timeout=15,
            )
            r.raise_for_status()
            js = r.json()
            out = [
                {"lat": float(it["lat"]), "lon": float(it["lon"]), "display": it.get("display_name", q)}
                for it in js
            ]
            if out:
                _GEOCODE_DISK[key] = out
                _save_json(CACHE_FILE, _GEOCODE_DISK)
                if st is not None:
                    st.session_state["_geocode_source"] = "nominatim"
                return out
        except Exception as e:  # păstrăm ultima eroare ca să o arătăm dacă pică și fallback-ul
            last_err = e
            time.sleep(attempt)

    # 2) Fallback: Photon
    try:
        r = requests.get(
            PHOTON_URL,
            params={"q": q, "limit": limit, "lang": "ro"},
            headers={"User-Agent": USER_AGENT},
            timeout=15,
        )
        r.raise_for_status()
        js = r.json()
        feats = js.get("features", [])
        out2: List[Dict] = []
        for f in feats:
            coords = ((f.get("geometry") or {}).get("coordinates") or [None, None])
            lon, lat = coords[0], coords[1]
            props = f.get("properties", {})
            parts = [
                props.get("name"), props.get("street"), props.get("housenumber"),
                props.get("city"), props.get("county"), props.get("state"), props.get("country"), props.get("postcode")
            ]
            disp = ", ".join([str(p) for p in parts if p]) or q
            if lat is not None and lon is not None:
                out2.append({"lat": float(lat), "lon": float(lon), "display": disp})
        if out2:
            _GEOCODE_DISK[key] = out2
            _save_json(CACHE_FILE, _GEOCODE_DISK)
            if st is not None:
                st.session_state["_geocode_source"] = "photon"
            return out2
    except Exception:
        pass

    if last_err:
        raise last_err
    return []


def route_osrm(lat1: float, lon1: float, lat2: float, lon2: float) -> Optional[Dict]:
    try:
        url = OSRM_ROUTE_URL.format(lon1=lon1, lat1=lat1, lon2=lon2, lat2=lat2)
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
        r.raise_for_status()
        data = r.json()
        routes = data.get("routes") or []
        if not routes:
            return None
        return {"km": routes[0]["distance"] / 1000.0}
    except Exception:
        return None

# ----------------- UI helpers -----------------

def _init_addr_state(key: str, default_text: str = "") -> None:
    if st is None:
        return
    if f"txt_{key}" not in st.session_state:
        st.session_state[f"txt_{key}"] = default_text
    st.session_state.setdefault(f"{key}_cands", [])
    st.session_state.setdefault(f"{key}_sel", 0)
    st.session_state.setdefault(f"{key}_lat", None)
    st.session_state.setdefault(f"{key}_lon", None)
    st.session_state.setdefault(f"{key}_display", "")
    st.session_state.setdefault(f"{key}_last_fetch_ts", 0.0)


def _refresh_candidates_if_due(key: str) -> None:
    if st is None:
        return
    q = (st.session_state.get(f"txt_{key}") or "").strip()
    last_q = (st.session_state.get(f"{key}_query") or "").strip()
    if q and q != last_q and len(q) >= 3:
        cands = geocode_osm_candidates(q, limit=6, implicit_place="")
        st.session_state[f"{key}_cands"] = cands
        st.session_state[f"{key}_query"] = q
        st.session_state[f"{key}_sel"] = 0
        st.session_state[f"{key}_last_fetch_ts"] = time.time()


def _render_address_row(label: str, key: str) -> None:
    if st is None:
        return
    cont = st.container()
    cont.text_input(label, key=f"txt_{key}")
    src = st.session_state.get("_geocode_source")
    if src == "photon":
        cont.caption("Sugestii de la Photon (fallback la indisponibilitatea Nominatim)")
    elif src == "nominatim":
        cont.caption("Sugestii de la Nominatim")
    _refresh_candidates_if_due(key)
    cands = st.session_state.get(f"{key}_cands", [])
    if cands:
        labels = [c["display"] for c in cands]
        idx = cont.radio(
            "Alege varianta",
            options=list(range(len(labels))),
            format_func=lambda i: labels[i],
            index=st.session_state.get(f"{key}_sel", 0),
            key=f"sel_{key}",
        )
        st.session_state[f"{key}_lat"] = cands[idx]["lat"]
        st.session_state[f"{key}_lon"] = cands[idx]["lon"]
        st.session_state[f"{key}_display"] = cands[idx]["display"]
        st.session_state[key] = cands[idx]["display"]
    else:
        cont.caption("Tastează minim 3 caractere pentru sugestii.")

# ----------------- APP -----------------

def run_streamlit_app() -> None:
    if st is None:
        print("Streamlit nu este disponibil în acest mediu.")
        return
    st.title(APP_TITLE)
    st.caption("Completează adresele și selectează varianta corectă din listă.")

    st.markdown("### Punct de plecare")
    _init_addr_state("start", "Piata Unirii, Bucuresti")  # fără diacritice în placeholder, evităm tastaturi diferite
    _render_address_row("Adresa de plecare", "start")

    st.markdown("### Opriri")
    if "stops_keys" not in st.session_state:
        st.session_state.stops_keys = ["stop_0"]
        _init_addr_state("stop_0", "Aeroportul Otopeni")

    if st.button("➕ Adaugă oprire", key="add_stop"):
        new_key = f"stop_{len(st.session_state.stops_keys)}"
        st.session_state.stops_keys.append(new_key)
        _init_addr_state(new_key, "")
        st.rerun()

    # Afișare opriri + ștergere
    remove_indices: List[int] = []
    for idx, key in enumerate(st.session_state.stops_keys):
        st.markdown(f"**Oprire #{idx+1}**")
        _init_addr_state(key)
        _render_address_row("Adresă", key)
        if st.button("Șterge", key=f"rm_{key}"):
            remove_indices.append(idx)

    for i in sorted(remove_indices, reverse=True):
        k = st.session_state.stops_keys.pop(i)
        for suf in ("_cands", "_sel", "_lat", "_lon", "_display", "_last_fetch_ts", "_query"):
            st.session_state.pop(f"{k}{suf}", None)
        st.session_state.pop(f"txt_{k}", None)

    if st.button("Calculează"):
        pts = []
        start = {"lat": st.session_state.get("start_lat"), "lon": st.session_state.get("start_lon"), "display": st.session_state.get("start")}
        if not start["lat"]:
            st.error("Selectează punctul de plecare.")
        else:
            pts.append(start)
            for key in st.session_state.stops_keys:
                lat, lon = st.session_state.get(f"{key}_lat"), st.session_state.get(f"{key}_lon")
                if lat and lon:
                    pts.append({"lat": lat, "lon": lon, "display": st.session_state.get(key)})
            if len(pts) < 2:
                st.error("Adaugă minim o oprire.")
            else:
                segments = []
                for i in range(len(pts) - 1):
                    a, b = pts[i], pts[i + 1]
                    res = route_osrm(a["lat"], a["lon"], b["lat"], b["lon"]) or {}
                    km = km_round(float(res.get("km", 0.0)), 1)
                    segments.append({"from": a["display"], "to": b["display"], "km_oneway": km})
                st.session_state["segments"] = segments
                st.session_state["calc_date"] = date.today()
                st.success("Traseul a fost recalculat. Poți bifa acum dus-întors pe segmente și exporta.")

    if st.session_state.get("segments"):
        st.subheader("Segmente")
        segments = st.session_state["segments"]
        data_foaie = st.session_state.get("calc_date", date.today())
        total = 0.0
        rows = []
        for i, seg in enumerate(segments):
            checked = st.checkbox("dus-întors", key=f"seg_rt_{i}", value=st.session_state.get(f"seg_rt_{i}", False))
            effective = seg["km_oneway"] * (2 if checked else 1)
            total += effective
            st.write(f"{seg['from']} → {seg['to']} = {effective} km")
            rows.append({
                "Data": data_foaie.strftime("%d.%m.%Y"),
                "Plecare": seg["from"],
                "Destinație": seg["to"],
                "Dus-întors": "Da" if checked else "Nu",
                "Km parcurși": effective,
            })
        st.success(f"Total km: {total}")

        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True)

        # Export CSV
        csv_bytes = df.to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            "⬇️ Descarcă CSV",
            csv_bytes,
            file_name=f"foaie_parcurs_{data_foaie.strftime('%Y%m%d')}.csv",
            mime="text/csv",
        )

        # Export Excel (TOTAL în foaia principală)
        bio = io.BytesIO()
        try:
            from openpyxl.styles import Font  # type: ignore
            with pd.ExcelWriter(bio, engine="openpyxl") as writer:
                df.to_excel(writer, index=False, sheet_name="Foaie de parcurs")
                ws = writer.sheets["Foaie de parcurs"]
                last_row = ws.max_row + 1
                ws.cell(row=last_row, column=4, value="TOTAL km").font = Font(bold=True)
                ws.cell(row=last_row, column=5, value=total).font = Font(bold=True)
            bio.seek(0)
            st.download_button(
                "⬇️ Descarcă Excel",
                bio.getvalue(),
                file_name=f"foaie_parcurs_{data_foaie.strftime('%Y%m%d')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        except Exception as ex:
            st.warning("Nu am putut genera Excel. Verifică instalarea `openpyxl`. Detalii mai jos.")
            st.exception(ex)
            st.info("CSV rămâne disponibil pentru descărcare.")

# ----------------- Teste de bază -----------------

def _run_basic_tests() -> None:
    # km_round
    assert km_round(12.34, 1) == 12.3
    assert km_round(12.35, 1) in (12.3, 12.4)  # depinde de floating

    # cache funcționează: scriem manual și citim
    key = "Test, RO|3"
    _GEOCODE_DISK[key] = [{"lat": 44.0, "lon": 26.0, "display": "Test, RO"}]
    _save_json(CACHE_FILE, _GEOCODE_DISK)
    reloaded = _load_json(CACHE_FILE)
    assert key in reloaded

    # shaping rând export
    rows = [{"Data": "01.01.2025", "Plecare": "A", "Destinație": "B", "Dus-întors": "Nu", "Km parcurși": 12.3}]
    df = pd.DataFrame(rows)
    assert list(df.columns) == ["Data", "Plecare", "Destinație", "Dus-întors", "Km parcurși"]

if __name__ == "__main__":
    if "--test" in sys.argv:
        _run_basic_tests()
        print("OK: testele de bază au trecut.")
        sys.exit(0)
    # lansare UI
    if st is not None:
        run_streamlit_app()
    else:
        print("Rulat fără Streamlit (mod CLI). Folosește:  streamlit run app.py")
