# app.py — StarPoint (SQLite) FULL (Excel-friendly CSV)
# - Mínimo para registrar: >= 2000
# - Puntos = monto * 0.0002 (cada 2.000 => 0.4)
# - Descarga: resta puntos; saldo puede ser negativo
# - Historial sin "None"; montos formateados "2.000"
# - Refresco automático tras registrar (st.rerun)
# - Exportar: Situación actual (CSV ; y ,) + Base completa (ZIP con CSV ; y , + backup .db)
# - Normaliza usuarios (TRIM) para resumen correcto

import os, io, zipfile, tempfile
import sqlite3
from pathlib import Path
from datetime import datetime, date
import pandas as pd
import streamlit as st

# --------------------------- Config & estilo ---------------------------

st.set_page_config(page_title="StarPoint", page_icon="⭐", layout="wide")

PRIMARY = "#6C5CE7"
st.markdown(f"""
<style>
:root {{ --primary: {PRIMARY}; }}
.block-container {{ padding-top: 1rem; }}
.kpi {{ font-size: 28px; font-weight: 800; margin: 0; color: #1f2937; }}
.kpi-sub {{ font-size: 12px; color: #6b7280; margin-top: 2px; }}
@media (prefers-color-scheme: dark) {{ .kpi {{ color:#fff !important; }} }}
.badge {{ display:inline-block; padding:2px 8px; border-radius:9999px; font-size:12px; font-weight:700; }}
.badge-ok {{ background:#E8F5E9; color:#1B5E20; }}
.badge-neg {{ background:#FFEBEE; color:#B71C1C; }}
.stButton>button {{ background: var(--primary) !important; border: 0 !important; }}
</style>
""", unsafe_allow_html=True)

# --------------------------- Helpers ----------------------------------

def parse_time_any(texto: str):
    """Acepta: '9 PM', '9:05 PM', '21:05', '09'. Devuelve time o None."""
    from datetime import datetime as _dt
    s = (texto or "").strip().upper().replace(".", "")
    formatos = ["%I:%M %p", "%I %p", "%H:%M", "%H"]
    for fmt in formatos:
        try:
            dt = _dt.strptime(s, fmt)
            return dt.time().replace(second=0, microsecond=0)
        except Exception:
            pass
    return None

def fmt_miles(n: float) -> str:
    """Devuelve 100000 -> '100.000' (puntos de miles, sin decimales)."""
    try:
        return f"{int(round(n)):,}".replace(",", ".")
    except Exception:
        return str(n)

# Puntos proporcionales: cada $2000 => 0.4
def puntos_por_monto(monto: float) -> float:
    return 0.0 if monto < 2000 else round(monto * 0.0002, 2)

# --------------------------- Base de datos ----------------------------

DB_PATH = Path(os.getenv("STARPOINT_DB", "StarPoint.db"))

def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    with conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
    return conn

def init_db(conn: sqlite3.Connection):
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cargas(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              usuario TEXT NOT NULL,
              monto REAL NOT NULL,
              ts DATETIME NOT NULL,
              puntos REAL NOT NULL
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS retiros(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              usuario TEXT NOT NULL,
              notificado_en DATETIME NOT NULL,
              monto REAL,
              puntos REAL
            );
        """)
        # Reparación de nulos para que no aparezca "None" en historial
        conn.execute("UPDATE cargas  SET ts            = COALESCE(ts,            CURRENT_TIMESTAMP) WHERE ts            IS NULL;")
        conn.execute("UPDATE retiros SET notificado_en = COALESCE(notificado_en, CURRENT_TIMESTAMP) WHERE notificado_en IS NULL;")

def normalize_users(conn):
    """Normaliza usuarios (TRIM) para evitar duplicados por espacios en el resumen."""
    with conn:
        conn.execute("UPDATE cargas  SET usuario = TRIM(usuario) WHERE usuario != TRIM(usuario);")
        conn.execute("UPDATE retiros SET usuario = TRIM(usuario) WHERE usuario != TRIM(usuario);")

def list_usuarios(conn: sqlite3.Connection) -> list[str]:
    q = """
    SELECT usuario
    FROM (SELECT usuario FROM cargas UNION SELECT usuario FROM retiros) t
    ORDER BY usuario COLLATE NOCASE;
    """
    return [r["usuario"] for r in conn.execute(q).fetchall()]

def insertar_carga(conn: sqlite3.Connection, usuario: str, monto: float, ts: datetime, puntos: float):
    with conn:
        conn.execute(
            "INSERT INTO cargas(usuario, monto, ts, puntos) VALUES (?, ?, ?, ?);",
            (usuario.strip(), float(monto), ts, float(puntos))
        )

def insertar_retiro(conn: sqlite3.Connection, usuario: str, ts: datetime, monto: float, puntos: float):
    with conn:
        conn.execute(
            "INSERT INTO retiros(usuario, notificado_en, monto, puntos) VALUES (?, ?, ?, ?);",
            (usuario.strip(), ts, float(monto), float(puntos))
        )

def total_puntos_usuario(conn: sqlite3.Connection, usuario: str) -> float:
    row_c = conn.execute("SELECT COALESCE(SUM(puntos),0) AS t FROM cargas  WHERE usuario=?;", (usuario,)).fetchone()
    row_r = conn.execute("SELECT COALESCE(SUM(puntos),0) AS t FROM retiros WHERE usuario=?;", (usuario,)).fetchone()
    return float((row_c["t"] or 0.0) - (row_r["t"] or 0.0))

def historial_usuario(conn: sqlite3.Connection, usuario: str, limit: int = 200) -> pd.DataFrame:
    # Formateo y orden desde SQL; siempre hay fecha válida (ver reparación en init)
    q = """
    SELECT dt,
           fecha_fmt AS "Fecha/Hora",
           tipo       AS "Tipo",
           monto      AS "Monto",
           puntos_vis AS "Puntos"
    FROM (
        SELECT
          COALESCE(ts, CURRENT_TIMESTAMP)                                        AS dt,
          strftime('%d/%m/%Y %H:%M', COALESCE(ts, CURRENT_TIMESTAMP))            AS fecha_fmt,
          'Carga'                                                                AS tipo,
          monto,
          puntos                                                                  AS puntos_vis
        FROM cargas WHERE usuario = ?
        UNION ALL
        SELECT
          COALESCE(notificado_en, CURRENT_TIMESTAMP)                              AS dt,
          strftime('%d/%m/%Y %H:%M', COALESCE(notificado_en, CURRENT_TIMESTAMP))  AS fecha_fmt,
          'Descarga'                                                              AS tipo,
          monto,
          -COALESCE(puntos,0)                                                     AS puntos_vis
        FROM retiros WHERE usuario = ?
    ) u
    ORDER BY dt DESC
    LIMIT ?;
    """
    return pd.read_sql_query(q, conn, params=(usuario, usuario, limit))

# -------- Resumen general (todos los usuarios) --------
def resumen_general(conn):
    """
    Resumen por usuario con montos, puntos y último movimiento.
    Agrupa por TRIM(usuario) para evitar duplicados por espacios.
    """
    q = """
    WITH
    u AS (
      SELECT TRIM(usuario) AS usuario FROM cargas
      UNION
      SELECT TRIM(usuario) AS usuario FROM retiros
    ),
    c AS (
      SELECT TRIM(usuario) AS usuario,
             SUM(monto) AS monto_c,
             SUM(puntos) AS pts_c,
             MAX(ts)    AS last_c
      FROM cargas
      GROUP BY TRIM(usuario)
    ),
    r AS (
      SELECT TRIM(usuario) AS usuario,
             SUM(monto) AS monto_r,
             SUM(puntos) AS pts_r,
             MAX(notificado_en) AS last_r
      FROM retiros
      GROUP BY TRIM(usuario)
    )
    SELECT
      u.usuario AS Usuario,
      COALESCE(c.monto_c, 0) AS Monto_cargas,
      COALESCE(r.monto_r, 0) AS Monto_descargas,
      COALESCE(c.pts_c, 0)   AS Puntos_cargas,
      COALESCE(r.pts_r, 0)   AS Puntos_descargas,
      COALESCE(c.pts_c, 0) - COALESCE(r.pts_r, 0) AS Puntos_actuales,
      strftime('%d/%m/%Y %H:%M', MAX(COALESCE(c.last_c, r.last_r))) AS Ultimo_movimiento
    FROM u
    LEFT JOIN c ON c.usuario = u.usuario
    LEFT JOIN r ON r.usuario = u.usuario
    GROUP BY u.usuario
    ORDER BY u.usuario COLLATE NOCASE;
    """
    return pd.read_sql_query(q, conn)

# -------- Export helpers --------
def sqlite_backup_bytes(conn: sqlite3.Connection) -> bytes:
    """Backup binario del SQLite aunque esté abierto."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        tmp_name = tmp.name
    try:
        dst = sqlite3.connect(tmp_name)
        with dst:
            conn.backup(dst)
        dst.close()
        with open(tmp_name, "rb") as f:
            data = f.read()
    finally:
        try:
            os.remove(tmp_name)
        except Exception:
            pass
    return data

def make_full_export_zip(conn: sqlite3.Connection) -> bytes:
    """
    ZIP con:
    - cargas.csv
    - descargas.csv (retiros)
    - resumen_actual.csv
    - StarPoint_backup.db (backup)
    (CSV amigables para Excel ES: sep=';' y decimal=',')
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        df_c = pd.read_sql_query(
            "SELECT id, usuario, monto, strftime('%d/%m/%Y %H:%M', ts) AS fecha, puntos FROM cargas ORDER BY ts DESC;",
            conn
        )
        df_r = pd.read_sql_query(
            "SELECT id, usuario, monto, strftime('%d/%m/%Y %H:%M', notificado_en) AS fecha, puntos FROM retiros ORDER BY notificado_en DESC;",
            conn
        )
        df_s = resumen_general(conn)

        zf.writestr(
            "cargas.csv",
            df_c.round(2).to_csv(index=False, sep=';', decimal=',', float_format='%.2f').encode("utf-8-sig")
        )
        zf.writestr(
            "descargas.csv",
            df_r.round(2).to_csv(index=False, sep=';', decimal=',', float_format='%.2f').encode("utf-8-sig")
        )
        zf.writestr(
            "resumen_actual.csv",
            df_s.round(2).to_csv(index=False, sep=';', decimal=',', float_format='%.2f').encode("utf-8-sig")
        )
        zf.writestr("StarPoint_backup.db", sqlite_backup_bytes(conn))
    buffer.seek(0)
    return buffer.getvalue()

# --------------------------- Estado app -------------------------------

conn = get_conn()
init_db(conn)
normalize_users(conn)  # normaliza usuarios (TRIM) para resumen correcto

if "selected_user" not in st.session_state:
    st.session_state.selected_user = ""

# --------------------------- UI --------------------------------------

st.title("⭐ StarPoint (SQLite) — Cargas y Descargas")

left, right = st.columns([2, 1], gap="large")

# -------- Panel izquierdo: entrada --------
with left:
    with st.form("form_carga", clear_on_submit=True):
        st.subheader("Registrar carga")
        c1, c2 = st.columns([2, 1])
        with c1:
            usuario = st.text_input("Usuario", value=st.session_state.get("selected_user", ""), placeholder="Ej.: maru5040")
        with c2:
            monto = st.number_input("Monto (≥ 2.000)", min_value=0.0, step=100.0, value=0.0)
        submit_carga = st.form_submit_button("➕ Registrar", use_container_width=True)

    if submit_carga:
        now = datetime.now()
        if not usuario.strip():
            st.error("Ingresá un usuario válido.", icon="🚫")
        elif float(monto) < 2000:
            st.error("La carga mínima para sumar puntos es **2000**.", icon="🚫")
        else:
            pts = puntos_por_monto(float(monto))
            insertar_carga(conn, usuario.strip(), float(monto), now, pts)
            st.session_state.selected_user = usuario.strip()
            st.success(f"✔ Carga **{fmt_miles(monto)}** registrada para **{usuario}** • +**{pts}** puntos", icon="✅")
            st.rerun()  # refresca historial/estado al instante

    st.divider()

    # -------- Historial --------
    st.subheader("Historial (Cargas y Descargas)")
    sel = st.text_input("Usuario para ver historial", value=st.session_state.get("selected_user", ""), placeholder="Escribí un usuario…")
    if sel and sel.strip():
        df = historial_usuario(conn, sel.strip(), limit=200)
        if df.empty:
            st.info("Sin movimientos para este usuario aún.")
        else:
            df_disp = df.drop(columns=["dt"]).copy()
            df_disp["Monto"]  = df_disp["Monto"].apply(fmt_miles)  # mostrar 2.000 / 10.000
            df_disp["Puntos"] = df_disp["Puntos"].round(2)        # redondeo visual a 2 decimales
            st.dataframe(df_disp, use_container_width=True, height=360, hide_index=True)
    else:
        st.caption("Tip: al registrar una carga, el usuario queda seleccionado automáticamente.")

# -------- Panel derecho: estado + descarga --------
with right:
    st.subheader("Estado del usuario")
    u_estado = st.text_input("Usuario", value=st.session_state.get("selected_user", ""), placeholder="Ej.: maru5040", key="estado_user")

    if u_estado and u_estado.strip():
        total_actual = total_puntos_usuario(conn, u_estado.strip())
        # KPI y badge (sin barra)
        st.markdown(f"<div class='kpi'>{total_actual:.2f}</div><div class='kpi-sub'>Puntos actuales</div>", unsafe_allow_html=True)
        if total_actual < 0:
            st.markdown("<span class='badge badge-neg'>Saldo negativo</span>", unsafe_allow_html=True)
        else:
            st.markdown("<span class='badge badge-ok'>Saldo positivo</span>", unsafe_allow_html=True)

    st.divider()
    st.subheader("Registrar descarga (retiro) — resta puntos")

    with st.form("form_retiro", clear_on_submit=True):
        ur = st.text_input("Usuario", value=st.session_state.get("selected_user", ""), placeholder="Ej.: maru5040")
        c3, c4 = st.columns([1, 1])
        with c3:
            monto_r = st.number_input("Monto del retiro (≥ 2.000)", min_value=0.0, step=100.0, value=0.0)
        with c4:
            default_text = datetime.now().strftime("%I:%M %p")
            hhmm_text = st.text_input("Hora (AM/PM o 24 h)", value=default_text, help="Ej.: 9 PM · 9:05 PM · 21:05 · 09")
        submit_retiro = st.form_submit_button("⬇️ Registrar descarga", use_container_width=True)

    if submit_retiro:
        if not ur.strip():
            st.error("Ingresá un usuario válido para registrar la descarga.", icon="🚫")
        elif float(monto_r) < 2000:
            st.error("El monto mínimo de descarga es **2000**.", icon="🚫")
        else:
            t = parse_time_any(hhmm_text)
            if not t:
                st.error("Hora inválida. Usá: 9 PM, 9:05 PM o 21:05.", icon="🚫")
            else:
                ts = datetime.combine(date.today(), t)
                pts_r = puntos_por_monto(float(monto_r))
                insertar_retiro(conn, ur.strip(), ts, float(monto_r), float(pts_r))
                st.session_state.selected_user = ur.strip()
                st.warning(f"Descarga registrada para **{ur}** • −**{pts_r}** puntos (monto {fmt_miles(monto_r)})", icon="⚠️")
                st.rerun()  # refresco automático tras registrar

# -------- Export / Descargas --------
st.divider()
st.subheader("Descargas / Exportar")

colA, colB = st.columns(2)

with colA:
    df_resumen = resumen_general(conn)
    # CSV amigable para Excel ES: ; como separador de columnas, , como decimal
    csv_text = df_resumen.round(2).to_csv(
        index=False,
        sep=';',
        decimal=',',
        float_format='%.2f'
    )
    csv_bytes = csv_text.encode("utf-8-sig")
    st.download_button(
        "⬇️ Descargar situación actual (CSV)",
        data=csv_bytes,
        file_name=f"situacion_{datetime.now():%Y%m%d_%H%M}.csv",
        mime="text/csv",
        use_container_width=True
    )

with colB:
    zip_bytes = make_full_export_zip(conn)
    st.download_button(
        "⬇️ Descargar base de datos completa (ZIP)",
        data=zip_bytes,
        file_name=f"starpoint_backup_{datetime.now():%Y%m%d_%H%M}.zip",
        mime="application/zip",
        use_container_width=True
    )

# --------------------------- Footer -----------------------------------
st.caption("StarPoint • SQLite • Mínimo 2000 • Puntos = monto × 0.0002 • Descargas restan puntos • Export CSV/ZIP (Excel ES).")
