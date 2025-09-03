# -*- coding: utf-8 -*-
"""
Foaie de parcurs - calcul automat km (OSRM gratuit)
UI minimalistă, mobile-friendly, dark mode auto, ștergere individuală + „Șterge toate opririle”.
Geocodare robustă (Nominatim -> Photon -> geocode.maps.co) + Adresar local + Coordonate manuale (Lat/Lon).
"""

from __future__ import annotations
import io, os, sys, json, time, math
from datetime import date
from typing import List, Dict, Optional

import requests
import pandas as pd

try:
    import streamlit as st  # type: ignore
except Exception:
    st = None  # type: ignore

# --- Config pagină + CSS ---
if st is not None:
    try:
        st.set_page_config(
            page_title="Foaie de parcurs - calcul automat km",
            page_icon="🚗",
            layout="wide",
        )
    except Exception:
        pass

    st.markdown(
        """
        <style>
        #MainMenu, header, footer {visibility:hidden;}
        .block-container {padding-top: .75rem; padding-bottom: 5rem; max-width: 920px;}

        input, textarea, .stButton>button, .stSelectbox div[data-baseweb="select"] {min-height: 44px;}
        .stButton>button {border-radius: 10px;}

        .card {
          padding: .9rem 1rem;
          border: 1px solid var(--border, #e6e6e6);
          border-radius: 14px;
          background: var(--card, #ffffff);
          box-shadow: 0 1px 3px rgba(0,0,0,.04);
          margin-bottom: .8rem;
        }
        .card-title { font-weight: 700; margin: 0; }
        .muted {color:#666; font-size:.85rem}
        .tiny-btn button {
          padding: .35rem .6rem !important;
          line-height: 1 !important;
          min-height: 32px !important;
          border-radius: 8px !important;
        }

        @media (prefers-color-scheme: dark) {
          :root {
            --bg: #0e1117;
            --fg: #e6e6e6;
            --card: #161a23;
            --muted: #a3a3a3;
            --border: #2b3040;
          }
          body { color: var(--fg); background: var(--bg); }
          .block-container { background: var(--bg); }
          .card { border-color: var(--border); background: var(--card); box-shadow: none; }
          .muted { color: var(--muted); }

          .stTextInput input, .stTextArea textarea,
          .stSelectbox div[role="button"], .stSelectbox input {
            background-color: var(--card) !important;
            color: var(--fg) !important;
            border-radius: 10px;
          }
          .stTextInput>div>div, .stSelectbox>div>div {
            background-color: var(--card) !important;
            border: 1px solid var(--border) !important;
            border-radius: 10px !important;
          }
          ul[role="listbox"] {
            background-color: var(--card) !important;
            color: var(--fg) !important;
            border: 1px solid var(--border) !important;
          }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

# --- Constante ---
APP_TITLE = "Foaie de parcurs - calcul automat km"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
PHOTON_URL = "https://photon.komoot.io/api/"
MAPSCO_URL = "https://geocode.maps.co/search"
OSRM_ROUTE_URL = (
    "https://router.project-osrm.org/route/v1/driving/"
    "{lon1},{lat1};{lon2},{lat2}?overview=full&alternatives=false&steps=false&geometries=geojson"
)
USER_AGENT = "FoaieParcursApp/4.0 (+https://github.com/banciumihaicatalin-design/FoaieParcurs)"
CACHE_FILE = os.path.expanduser("~/.foaieparcurs_cache.json")

# --- Cache pe disc ---
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
# adresar local: chei "addr:<text exact>" -> {"lat":..,"lon":..,"display":..}

# --- Utilitare ---
def km_round(x: float, decimals: int = 1) -> float:
    pow10 = 10 ** decimals
    return math.floor(x * pow10 + 0.5) / pow10

def _addrbook_get(q: str) -> Optional[Dict]:
    return _GEOCODE_DISK.get(f"addr:{q}")

def _addrbook_put(q: str, lat: float, lon: float, display: str) -> None:
    _GEOCODE_DISK[f"addr:{q}"] = {"lat": float(lat), "lon": float(lon), "display": display}
    _save_json(CACHE_FILE, _GEOCODE_DISK)

# Geocodare robustă cu 3 furnizori + România implicit + adresar local
def geocode_osm_candidates(q: str, *, limit: int, implicit_place: str = "România") -> List[Dict]:
    """
    0) Caută întâi în adresarul local (exact match)
    1) Nominatim (OSM) - retry scurt
    2) Photon (Komoot) - fallback
    3) geocode.maps.co - fallback final (gratuit)
    Fără excepții în UI: la eșec salvează mesajul în st.session_state["_geocode_error"] și returnează [].
    """
    if implicit_place and (implicit_place.lower() not in q.lower()):
        q = f"{q}, {implicit_place}"

    # 0) Adresar local
    book_hit = _addrbook_get(q)
    if book_hit:
        if st is not None:
            st.session_state["_geocode_source"] = "adresar"
            st.session_state.pop("_geocode_error", None)
        return [book_hit]

    key = f"{q}|{limit}"
    if key in _GEOCODE_DISK:
        return _GEOCODE_DISK[key]

    last_err: Optional[Exception] = None

    # 1) Nominatim (retry 0s, apoi 0.5s)
    for attempt in range(2):
        try:
            r = requests.get(
                NOMINATIM_URL,
                params={"q": q, "format": "json", "limit": limit, "accept-language": "ro"},
                headers={"User-Agent": USER_AGENT},
                timeout=10,
            )
            r.raise_for_status()
            js = r.json()
            out = [{"lat": float(it["lat"]), "lon": float(it["lon"]), "display": it.get("display_name", q)} for it in js]
            if out:
                _GEOCODE_DISK[key] = out; _save_json(CACHE_FILE, _GEOCODE_DISK)
                if st is not None:
                    st.session_state["_geocode_source"] = "nominatim"
                    st.session_state.pop("_geocode_error", None)
                return out
        except Exception as e:
            last_err = e
            time.sleep(0.5 * attempt)

    # 2) Photon (Komoot)
    try:
        r = requests.get(
            PHOTON_URL,
            params={"q": q, "limit": limit, "lang": "ro"},
            headers={"User-Agent": USER_AGENT},
            timeout=10,
        )
        r.raise_for_status()
        js = r.json()
        feats = js.get("features", [])
        out2: List[Dict] = []
        for f in feats:
            coords = ((f.get("geometry") or {}).get("coordinates") or [None, None])
            lon, lat = coords[0], coords[1]
            props = f.get("properties", {})
            parts = [props.get("name"), props.get("street"), props.get("housenumber"),
                     props.get("city"), props.get("county"), props.get("state"),
                     props.get("country"), props.get("postcode")]
            disp = ", ".join([str(p) for p in parts if p]) or q
            if lat is not None and lon is not None:
                out2.append({"lat": float(lat), "lon": float(lon), "display": disp})
        if out2:
            _GEOCODE_DISK[key] = out2; _save_json(CACHE_FILE, _GEOCODE_DISK)
            if st is not None:
                st.session_state["_geocode_source"] = "photon"
                st.session_state.pop("_geocode_error", None)
            return out2
    except Exception as e2:
        last_err = last_err or e2

    # 3) geocode.maps.co (gratuit)
    try:
        r = requests.get(
            MAPSCO_URL,
            params={"q": q, "limit": str(limit)},
            headers={"User-Agent": USER_AGENT},
            timeout=10,
        )
        r.raise_for_status()
        js = r.json() if isinstance(r.json(), list) else []
        out3: List[Dict] = []
        for it in js:
            lat = it.get("lat"); lon = it.get("lon")
            disp = it.get("display_name") or it.get("name") or q
            if lat and lon:
                out3.append({"lat": float(lat), "lon": float(lon), "display": disp})
        if out3:
            _GEOCODE_DISK[key] = out3; _save_json(CACHE_FILE, _GEOCODE_DISK)
            if st is not None:
                st.session_state["_geocode_source"] = "maps.co"
                st.session_state.pop("_geocode_error", None)
            return out3
    except Exception as e3:
        last_err = last_err or e3

    if st is not None and last_err:
        st.session_state["_geocode_error"] = str(last_err)
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

# --- UI helpers ---
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
    # câmpuri pentru mod manual
    st.session_state.setdefault(f"{key}_man_lat", None)
    st.session_state.setdefault(f"{key}_man_lon", None)
    st.session_state.setdefault(f"{key}_save_to_book", False)

def _refresh_candidates_if_due(key: str) -> None:
    if st is None:
        return
    q = (st.session_state.get(f"txt_{key}") or "").strip()
    last_q = (st.session_state.get(f"{key}_query") or "").strip()
    if q and q != last_q and len(q) >= 3:
        st.session_state.pop("_geocode_error", None)  # curățăm eroarea anterioară
        cands = geocode_osm_candidates(q, limit=6, implicit_place="România")
        st.session_state[f"{key}_cands"] = cands
        st.session_state[f"{key}_query"] = q
        st.session_state[f"{key}_sel"] = 0
        st.session_state[f"{key}_last_fetch_ts"] = time.time()

def _render_manual_coord_controls(key: str, container) -> None:
    """Lat/Lon manuale; dacă ambele sunt completate corect, folosim coordonatele și (opțional) salvăm în adresar."""
    with container.expander("Alternativ: introdu coordonate manual (Lat, Lon)", expanded=False):
        colA, colB = st.columns(2)
        with colA:
            man_lat = st.number_input("Latitudine (ex: 44.4268)", value=st.session_state.get(f"{key}_man_lat") or 0.0, step=0.000001, format="%.6f", key=f"num_lat_{key}")
        with colB:
            man_lon = st.number_input("Longitudine (ex: 26.1025)", value=st.session_state.get(f"{key}_man_lon") or 0.0, step=0.000001, format="%.6f", key=f"num_lon_{key}")

        valid = (-90.0 <= man_lat <= 90.0) and (-180.0 <= man_lon <= 180.0) and (abs(man_lat) + abs(man_lon) > 0)
        st.session_state[f"{key}_man_lat"] = man_lat
        st.session_state[f"{key}_man_lon"] = man_lon

        if valid:
            # setăm coordonatele active pentru acest key
            st.session_state[f"{key}_lat"] = float(man_lat)
            st.session_state[f"{key}_lon"] = float(man_lon)
            # nu atingem textinput-ul (ca să nu dăm eroare Streamlit); afișăm display separat
            st.session_state[f"{key}_display"] = f"{man_lat:.6f}, {man_lon:.6f} (manual)"
            # opțiune: salvează în adresar sub textul exact introdus
            lbl = "Salvează aceste coordonate în adresarul local pentru textul introdus mai sus"
            save = st.checkbox(lbl, value=st.session_state.get(f"{key}_save_to_book", False), key=f"chk_book_{key}")
            st.session_state[f"{key}_save_to_book"] = save
            if save:
                qtext = (st.session_state.get(f"txt_{key}") or "").strip()
                if len(qtext) >= 3:
                    _addrbook_put(qtext + ", România", float(man_lat), float(man_lon), qtext + ", România")
                    st.info("Salvate în adresarul local. Data viitoare nu mai depinzi de internet pentru această adresă.")
        else:
            st.caption("<span class='muted'>Completează ambele câmpuri cu valori valide.</span>", unsafe_allow_html=True)

def _render_address_row(label: str, key: str) -> None:
    if st is None:
        return

    st.markdown("<div class='card'>", unsafe_allow_html=True)
    c1, c2 = st.columns([0.8, 0.2])
    with c1:
        st.markdown(f"<p class='card-title'>Adresă</p>", unsafe_allow_html=True)
    with c2:
        rm = st.button("✖ Șterge", key=f"rm_{key}", use_container_width=True)

    cont = st.container()
    cont.text_input(label, key=f"txt_{key}")

    # sursa curentă (doar informativ)
    src = st.session_state.get("_geocode_source")
    if src == "photon":
        cont.caption("Sugestii de la Photon (fallback Nominatim)")
    elif src == "nominatim":
        cont.caption("Sugestii de la Nominatim")
    elif src == "maps.co":
        cont.caption("Sugestii de la geocode.maps.co")
    elif src == "adresar":
        cont.caption("Adresar local (fără internet)")

    _refresh_candidates_if_due(key)
    cands = st.session_state.get(f"{key}_cands", [])
    if cands:
        labels = [c["display"] for c in cands]
        idx = cont.selectbox(
            "Alege adresa",
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
        err = st.session_state.get("_geocode_error")
        if err:
            cont.warning("Serviciul de geocodare este indisponibil momentan. Poți folosi modul cu coordonate manuale de mai jos sau reîncearcă.")
        else:
            cont.caption("<span class='muted'>Tastează minim 3 caractere pentru a vedea sugestii.</span>", unsafe_allow_html=True)

    # controale pentru coordonate manuale (bypass geocodare)
    _render_manual_coord_controls(key, cont)

    st.markdown("</div>", unsafe_allow_html=True)

    if rm:
        st.session_state.setdefault("_to_remove", []).append(key)

# --- APP ---
def run_streamlit_app() -> None:
    if st is None:
        print("Streamlit nu este disponibil în acest mediu.")
        return

    st.title("🚗 Foaie de parcurs")

    # Punct de plecare
    st.markdown("#### 📍 Punct de plecare")
    _init_addr_state("start", "Piata Unirii, Bucuresti")

    st.markdown("<div class='card'>", unsafe_allow_html=True)
    cont = st.container()
    cont.text_input("Adresa de plecare", key="txt_start")
    _refresh_candidates_if_due("start")
    start_cands = st.session_state.get("start_cands", [])
    if start_cands:
        labels = [c["display"] for c in start_cands]
        idx = cont.selectbox(
            "Alege adresa",
            options=list(range(len(labels))),
            format_func=lambda i: labels[i],
            index=st.session_state.get("start_sel", 0),
            key="sel_start",
        )
        st.session_state["start_lat"] = start_cands[idx]["lat"]
        st.session_state["start_lon"] = start_cands[idx]["lon"]
        st.session_state["start_display"] = start_cands[idx]["display"]
        st.session_state["start"] = start_cands[idx]["display"]
    else:
        err = st.session_state.get("_geocode_error")
        if err:
            cont.warning("Serviciul de geocodare este indisponibil momentan. Poți folosi modul cu coordonate manuale de mai jos sau reîncearcă.")
        else:
            cont.caption("<span class='muted'>Tastează minim 3 caractere pentru a vedea sugestii.</span>", unsafe_allow_html=True)

    # coordonate manuale pentru start
    _render_manual_coord_controls("start", cont)
    st.markdown("</div>", unsafe_allow_html=True)

    # Opriri
    st.markdown("#### 🛑 Opriri")
    if "stops_keys" not in st.session_state:
        st.session_state.stops_keys = ["stop_0"]
        _init_addr_state("stop_0", "")

    top_cols = st.columns([0.6, 0.4])
    with top_cols[0]:
        if st.button("➕ Adăugare oprire", key="add_stop_btn", use_container_width=True):
            new_key = f"stop_{len(st.session_state.stops_keys)}"
            st.session_state.stops_keys.append(new_key)
            _init_addr_state(new_key, "")
            st.rerun()
    with top_cols[1]:
        if st.button("🗑️ Șterge toate opririle", key="rm_all_btn", use_container_width=True):
            st.session_state["_to_remove"] = list(st.session_state.stops_keys)

    # Afișare opriri
    st.session_state.pop("_to_remove", None)
    for key in list(st.session_state.stops_keys):
        _init_addr_state(key)
        _render_address_row("Adresă", key)

    # Aplicăm ștergerile cerute
    remove_list = st.session_state.pop("_to_remove", [])
    if remove_list:
        for k in remove_list:
            if k in st.session_state.stops_keys:
                st.session_state.stops_keys.remove(k)
            for suf in ("_cands", "_sel", "_lat", "_lon", "_display", "_last_fetch_ts", "_query", "_man_lat", "_man_lon", "_save_to_book"):
                st.session_state.pop(f"{k}{suf}", None)
            st.session_state.pop(f"txt_{k}", None)
        st.rerun()

    # Calcul
    st.markdown("#### 📐 Calcul")
    if st.button("Calculează traseul", key="calc_btn", use_container_width=True):
        pts = []
        start = {"lat": st.session_state.get("start_lat"), "lon": st.session_state.get("start_lon"), "display": st.session_state.get("start") or st.session_state.get("start_display")}
        if not start["lat"] or not start["lon"]:
            st.error("Selectează punctul de plecare sau introdu coordonatele manual.")
        else:
            pts.append(start)
            for key in st.session_state.stops_keys:
                lat, lon = st.session_state.get(f"{key}_lat"), st.session_state.get(f"{key}_lon")
                disp = st.session_state.get(f"{key}_display") or st.session_state.get(key)
                if lat and lon:
                    pts.append({"lat": float(lat), "lon": float(lon), "display": disp or "Punct"})
            if len(pts) < 2:
                st.error("Adaugă minim o oprire (sau coordonatele ei).")
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

    # Segmente + export
    if st.session_state.get("segments"):
        st.markdown("#### 🧭 Segmente")
        segments = st.session_state["segments"]
        data_foaie = st.session_state.get("calc_date", date.today())
        total = 0.0
        rows = []
        for i, seg in enumerate(segments):
            col1, col2 = st.columns([0.7, 0.3])
            with col1:
                st.markdown(f"• <b>{seg['from']}</b> → <b>{seg['to']}</b>", unsafe_allow_html=True)
            with col2:
                checked = st.checkbox("dus-întors", key=f"seg_rt_{i}", value=st.session_state.get(f"seg_rt_{i}", False))
            effective = seg["km_oneway"] * (2 if checked else 1)
            total += effective
            st.markdown(f"<span class='muted'>Distanță: <b>{effective} km</b></span>", unsafe_allow_html=True)
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
            use_container_width=True,
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
                use_container_width=True,
            )
        except Exception as ex:
            st.warning("Nu am putut genera Excel. Verifică instalarea `openpyxl`. Detalii mai jos.")
            st.exception(ex)
            st.info("CSV rămâne disponibil pentru descărcare.")

# --- Teste minimale (nu se rulează în Streamlit Cloud) ---
def _run_basic_tests() -> None:
    assert km_round(12.34, 1) == 12.3
    assert km_round(12.35, 1) in (12.3, 12.4)
    key = "Test, RO|3"
    _GEOCODE_DISK[key] = [{"lat": 44.0, "lon": 26.0, "display": "Test, RO"}]
    _save_json(CACHE_FILE, _GEOCODE_DISK)
    reloaded = _load_json(CACHE_FILE)
    assert key in reloaded
    rows = [{"Data": "01.01.2025", "Plecare": "A", "Destinație": "B", "Dus-întors": "Nu", "Km parcurși": 12.3}]
    df = pd.DataFrame(rows)
    assert list(df.columns) == ["Data", "Plecare", "Destinație", "Dus-întors", "Km parcurși"]

# --- Rulare ---
if __name__ == "__main__":
    if "--test" in sys.argv:
        _run_basic_tests()
        print("OK: testele de bază au trecut.")
        sys.exit(0)
    if st is not None:
        run_streamlit_app()
    else:
        print("Rulat fără Streamlit (mod CLI). Folosește:  streamlit run app.py")