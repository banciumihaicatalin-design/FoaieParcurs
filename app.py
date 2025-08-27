import io,os,sys,json,time,math,requests,pandas as pd
from datetime import date,datetime
from typing import List,Dict
try:
 import streamlit as st
 import pydeck as pdk
except Exception:
 st=None;pdk=None
if st is not None:
 try:
  st.set_page_config(page_title="Foaie de parcurs - calcul automat km",page_icon="🚗",layout="wide")
 except Exception:
  pass
APP_TITLE="Foaie de parcurs - calcul automat km";NOMINATIM_URL="https://nominatim.openstreetmap.org/search";OSRM_ROUTE_URL=("https://router.project-osrm.org/route/v1/driving/"+"{lon1},{lat1};{lon2},{lat2}?overview=full&alternatives=false&steps=false&geometries=geojson");USER_AGENT="FoaieParcursApp/2.0";CACHE_FILE=os.path.expanduser("~/.foaieparcurs_cache.json")

def _load_json(path:str)->dict:
 try:
  if os.path.exists(path):
   with open(path,"r",encoding="utf-8") as f:
    return json.load(f)
 except Exception:
  pass
 return {}

def _save_json(path:str,data:dict)->None:
 try:
  with open(path,"w",encoding="utf-8") as f:
   json.dump(data,f,ensure_ascii=False,indent=2)
 except Exception:
  pass

_GEOCODE_DISK=_load_json(CACHE_FILE)

def km_round(x:float,decimals:int=1)->float:
 pow10=10**decimals;return math.floor(x*pow10+0.5)/pow10

def geocode_osm_candidates(q:str,*,limit:int,implicit_place:str="")->List[Dict]:
 if implicit_place and (implicit_place.lower() not in q.lower()):
  q=f"{q}, {implicit_place}"
 key=f"{q}|{limit}"; 
 if key in _GEOCODE_DISK: return _GEOCODE_DISK[key]
 r=requests.get(NOMINATIM_URL,params={"q":q,"format":"json","limit":limit},headers={"User-Agent":USER_AGENT},timeout=20);r.raise_for_status();out=[{"lat":float(it["lat"]),"lon":float(it["lon"]),"display":it.get("display_name",q)} for it in r.json()];_GEOCODE_DISK[key]=out;_save_json(CACHE_FILE,_GEOCODE_DISK);return out

def route_osrm(lat1:float,lon1:float,lat2:float,lon2:float):
 try:
  url=OSRM_ROUTE_URL.format(lon1=lon1,lat1=lat1,lon2=lon2,lat2=lat2);r=requests.get(url,headers={"User-Agent":USER_AGENT},timeout=20);r.raise_for_status();data=r.json();routes=data.get("routes") or []; 
  if not routes: return None
  return {"km":routes[0]["distance"]/1000.0,"geometry":routes[0].get("geometry")}
 except Exception:
  return None

def _init_addr_state(key:str,default_text:str=""):
 if f"txt_{key}" not in st.session_state: st.session_state[f"txt_{key}"]=default_text
 st.session_state.setdefault(f"{key}_cands",[]);st.session_state.setdefault(f"{key}_sel",0);st.session_state.setdefault(f"{key}_lat",None);st.session_state.setdefault(f"{key}_lon",None);st.session_state.setdefault(f"{key}_display","");st.session_state.setdefault(f"{key}_last_fetch_ts",0.0)

def _refresh_candidates_if_due(key:str):
 q=(st.session_state.get(f"txt_{key}") or "").strip();last_q=(st.session_state.get(f"{key}_query") or "").strip();min_chars=int(st.session_state.get("cfg_geocode_min_chars",3));now=time.time();last_fetch=float(st.session_state.get(f"{key}_last_fetch_ts",0.0));debounce_s=float(st.session_state.get("cfg_debounce_ms",0))/1000.0
 if q and q!=last_q and len(q)>=min_chars and (now-last_fetch>=debounce_s):
  cands=geocode_osm_candidates(q,limit=int(st.session_state.get("cfg_geocode_candidates",6)),implicit_place=st.session_state.get("cfg_implicit_place","") );st.session_state[f"{key}_cands"]=cands;st.session_state[f"{key}_query"]=q;st.session_state[f"{key}_sel"]=0;st.session_state[f"{key}_last_fetch_ts"]=now

def _render_address_row(label:str,key:str):
 cont=st.container();cont.text_input(label,key=f"txt_{key}");_refresh_candidates_if_due(key);cands=st.session_state.get(f"{key}_cands",[])
 if cands:
  labels=[c["display"] for c in cands];idx=cont.radio("Alege varianta",options=list(range(len(labels))),format_func=lambda i:labels[i],index=st.session_state.get(f"{key}_sel",0),key=f"sel_{key}");st.session_state[f"{key}_lat"]=cands[idx]["lat"];st.session_state[f"{key}_lon"]=cands[idx]["lon"];st.session_state[f"{key}_display"]=cands[idx]["display"];st.session_state[key]=cands[idx]["display"]
 else:
  cont.caption("Tasteaza minim 3 caractere pentru sugestii.")

def run_streamlit_app():
 st.title(APP_TITLE);st.caption("Completeaza adresele si selecteaza varianta corecta din lista.");st.session_state.setdefault("cfg_geocode_min_chars",3);st.session_state.setdefault("cfg_debounce_ms",0);st.session_state.setdefault("cfg_geocode_candidates",6);st.session_state.setdefault("cfg_implicit_place","")
 st.markdown("### Punct de plecare");_init_addr_state("start","Piata Unirii, Bucuresti");_render_address_row("Adresa de plecare","start")
 st.markdown("### Opriri");
 if "stops_keys" not in st.session_state:
  st.session_state.stops_keys=["stop_0"];_init_addr_state("stop_0","Aeroportul Otopeni")
 col_add,_=st.columns([1,9])
 if col_add.button("➕ Adauga oprire",key="add_stop"):
  new_key=f"stop_{len(st.session_state.stops_keys)}";st.session_state.stops_keys.append(new_key);_init_addr_state(new_key,"");st.rerun()
 remove_indices=[]
 for idx,key in enumerate(st.session_state.stops_keys):
  c0,c1,c2,c3=st.columns([0.5,0.5,8,2])
  with c0:
   if st.button("⬆️",key=f"up_{key}") and idx>0:
    sk=st.session_state.stops_keys;sk[idx-1],sk[idx]=sk[idx],sk[idx-1];st.rerun()
  with c1:
   if st.button("⬇️",key=f"down_{key}") and idx<len(st.session_state.stops_keys)-1:
    sk=st.session_state.stops_keys;sk[idx+1],sk[idx]=sk[idx],sk[idx+1];st.rerun()
  with c2:
   st.markdown(f"**Oprire #{idx+1}**")
  with c3:
   if st.button("Sterge",key=f"rm_{key}"):
    remove_indices.append(idx)
  _init_addr_state(key);_render_address_row("Adresa",key)
 for i in sorted(remove_indices,reverse=True):
  k=st.session_state.stops_keys.pop(i)
  for suf in ("_cands","_sel","_lat","_lon","_display","_last_fetch_ts","_query"):
   st.session_state.pop(f"{k}{suf}",None)
  st.session_state.pop(f"txt_{k}",None)
 if st.button("Calculeaza"):
  pts=[];start={"lat":st.session_state.get("start_lat"),"lon":st.session_state.get("start_lon"),"display":st.session_state.get("start")}
  if not start["lat"]:
   st.error("Selecteaza punctul de plecare.")
  else:
   pts.append(start)
   for key in st.session_state.stops_keys:
    lat,lon=st.session_state.get(f"{key}_lat"),st.session_state.get(f"{key}_lon")
    if lat and lon: pts.append({"lat":lat,"lon":lon,"display":st.session_state.get(key)})
   if len(pts)<2:
    st.error("Adauga minim o oprire.")
   else:
    segments=[]
    for i in range(len(pts)-1):
     a,b=pts[i],pts[i+1];res=route_osrm(a["lat"],a["lon"],b["lat"],b["lon"]) or {};km=km_round(float(res.get("km",0.0)),1);segments.append({"from":a["display"],"to":b["display"],"km_oneway":km})
    for k2 in list(st.session_state.keys()):
     if isinstance(k2,str) and k2.startswith("seg_rt_"): st.session_state.pop(k2,None)
    st.session_state["segments"]=segments;st.session_state["calc_date"]=date.today();st.session_state["calc_scop"]="Serviciu";st.success("Traseul a fost recalculat. Bifeaza dus-intors pe segmente si exporta.")
 if st.session_state.get("segments"):
  st.subheader("Segmente");segments=st.session_state["segments"];data_foaie=st.session_state.get("calc_date",date.today());scop_general=st.session_state.get("calc_scop","Serviciu");total=0.0;rows=[]
  for i,seg in enumerate(segments):
   cols=st.columns([6,2,2,2]);cols[0].markdown(f"**{i+1}.** {seg['from']} → {seg['to']}");cb_key=f"seg_rt_{i}";checked=cols[1].checkbox("dus-intors",key=cb_key,value=st.session_state.get(cb_key,False));effective=seg["km_oneway"]*(2 if checked else 1);total+=effective;cols[2].metric("km segment",f"{seg['km_oneway']}");cols[3].metric("km efectiv",f"{effective}");rows.append({"Data":data_foaie.strftime("%d.%m.%Y"),"Plecare":seg["from"],"Destinatie":seg["to"],"Scop":scop_general,"Dus-intors":"Da" if checked else "Nu","Km parcursi":effective})
  st.success(f"Total km: {total}");df=pd.DataFrame(rows);st.dataframe(df,use_container_width=True)
  csv_bytes=df.to_csv(index=False).encode("utf-8-sig");st.download_button("⬇️ Descarca CSV",csv_bytes,file_name=f"foaie_parcurs_{data_foaie.strftime('%Y%m%d')}.csv",mime="text/csv")
  bio=io.BytesIO()
  try:
   from openpyxl.styles import Font
   with pd.ExcelWriter(bio,engine="openpyxl") as writer:
    df.to_excel(writer,index=False,sheet_name="Foaie de parcurs");ws=writer.sheets["Foaie de parcurs"];last_row=ws.max_row+1;ws.cell(row=last_row,column=4,value="TOTAL km").font=Font(bold=True);ws.cell(row=last_row,column=5,value=total).font=Font(bold=True)
   bio.seek(0);st.download_button("⬇️ Descarca Excel",bio.getvalue(),file_name=f"foaie_parcurs_{data_foaie.strftime('%Y%m%d')}.xlsx",mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
  except Exception as ex:
   st.warning("Nu am putut genera Excel. Verifica instalarea `openpyxl`. Detalii mai jos.");st.exception(ex);st.info("CSV ramane disponibil pentru descarcare.")
# dep verificare
missing=[]
for lib in ["openpyxl","pandas","requests"]:
 try:
  __import__(lib)
 except ImportError:
  missing.append(lib)
if missing and st is not None:
 st.error(f"Lipsesc librarii necesare: {', '.join(missing)}. Instaleaza cu: pip install {' '.join(missing)}")
if st is not None:
 try:
  run_streamlit_app()
 except Exception as e:
  st.error("Eroare la randare.");st.exception(e)