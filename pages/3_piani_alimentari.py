import logging
import inspect
import importlib.metadata
from datetime import datetime
from io import BytesIO
import html
import re
import streamlit as st
import json
import pandas as pd
from decimal import Decimal
from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode, JsCode
try:
    from st_aggrid import DataReturnMode
except ImportError:
    DataReturnMode = None
from Backend.services.patient_service import get_all_patients
from Backend.services.diet_service import (
    get_diet_plans,
    add_diet_plan,
    update_diet_plan,
    delete_diet_plan,
    diet_name_exists,
    calculate_nutrients_proportional,
    calculate_diet_micronutrients_overview,
)
from Backend.services.food_service import get_foods_for_diet_editor

# Configurazione del logger
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("DietApp")

# ------------------------------------------------------------------
# DIAGNOSTICA TEMPORANEA AG Grid -> Python
# ------------------------------------------------------------------
# I log vengono scritti sia sulla console Python sia in session_state,
# cosi possono essere copiati/scaricati direttamente dalla UI Streamlit.
DEBUG_LOG_KEY = "diet_grid_debug_log"
DEBUG_LOG_MAX_EVENTS = 500

def _debug_json_safe(value):
    """Rende serializzabili i valori usati nei log diagnostici."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {str(k): _debug_json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_debug_json_safe(v) for v in value]
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    return repr(value)

def _debug_df_payload(data, max_rows=10):
    """Snapshot compatta di un DataFrame/lista per capire cosa vede Python."""
    if data is None:
        return {"is_none": True}
    try:
        df = pd.DataFrame(data).copy()
    except Exception as exc:
        return {
            "conversion_error": repr(exc),
            "python_type": type(data).__name__,
            "repr": repr(data)[:1000],
        }

    interesting = [
        c for c in [
            "__row_id", "__action_touch", "__sync_request",
            "Alimento", "Grammi (g)", "Kcal", "Fats", "Carbs", "Prots"
        ] if c in df.columns
    ]
    sample_df = df[interesting].head(max_rows) if interesting else df.head(max_rows)
    rows = []
    for row in sample_df.to_dict(orient="records"):
        rows.append({str(k): _debug_json_safe(v) for k, v in row.items()})

    return {
        "shape": [int(df.shape[0]), int(df.shape[1])],
        "columns": [str(c) for c in df.columns],
        "sample_rows": rows,
    }

def _debug_response_payload(response):
    """Descrive l'oggetto restituito da AgGrid senza assumere una versione specifica."""
    if response is None:
        return {"python_type": "NoneType", "is_none": True}

    payload = {"python_type": type(response).__name__}
    if isinstance(response, dict):
        payload["dict_keys"] = [str(k) for k in response.keys()]
        payload["data"] = _debug_df_payload(response.get("data"))
        for key in ("selected_rows", "event_data", "grid_state", "columns_state"):
            if key in response:
                payload[key] = _debug_json_safe(response.get(key))
    else:
        attrs = {}
        for attr in ("data", "selected_rows", "event_data", "grid_state", "columns_state"):
            try:
                value = getattr(response, attr, None)
            except Exception as exc:
                attrs[attr] = {"read_error": repr(exc)}
                continue
            if attr == "data":
                attrs[attr] = _debug_df_payload(value)
            elif value is not None:
                attrs[attr] = _debug_json_safe(value)
        payload["attrs"] = attrs
        try:
            payload["repr"] = repr(response)[:1500]
        except Exception:
            pass
    return payload

def _diag_log(event, **payload):
    """Registra un evento diagnostico numerato e timestampato."""
    try:
        seq = int(st.session_state.get("diet_grid_debug_seq", 0) or 0) + 1
        st.session_state["diet_grid_debug_seq"] = seq
    except Exception:
        seq = -1

    record = {
        "seq": seq,
        "ts": datetime.now().isoformat(timespec="milliseconds"),
        "event": event,
        **{k: _debug_json_safe(v) for k, v in payload.items()},
    }
    try:
        line = json.dumps(record, ensure_ascii=False, default=str)
    except Exception:
        line = repr(record)

    logger.warning("[GRID-DEBUG] %s", line)
    try:
        log_lines = list(st.session_state.get(DEBUG_LOG_KEY, []))
        log_lines.append(line)
        if len(log_lines) > DEBUG_LOG_MAX_EVENTS:
            log_lines = log_lines[-DEBUG_LOG_MAX_EVENTS:]
        st.session_state[DEBUG_LOG_KEY] = log_lines
    except Exception as exc:
        logger.warning("[GRID-DEBUG] impossibile salvare log in session_state: %r", exc)

def _diag_text():
    return "\n".join(st.session_state.get(DEBUG_LOG_KEY, []))

def _diag_clear():
    st.session_state[DEBUG_LOG_KEY] = []
    st.session_state["diet_grid_debug_seq"] = 0

def _basic_valid_rows(df):
    """Conta righe non vuote senza dipendere dal catalogo alimenti."""
    if df is None:
        return 0
    try:
        frame = pd.DataFrame(df)
    except Exception:
        return 0
    if "Alimento" not in frame.columns or "Grammi (g)" not in frame.columns:
        return 0
    count = 0
    for food, grams in frame[["Alimento", "Grammi (g)"]].itertuples(index=False, name=None):
        name = "" if pd.isna(food) else str(food).strip()
        try:
            g = float(grams or 0)
        except Exception:
            g = 0.0
        if name and g > 0:
            count += 1
    return count

tec_conf = st.session_state.get("tec_conf", {}) 

GIORNI_MAP = {
    1: "Lunedì", 2: "Martedì", 3: "Mercoledì",
    4: "Giovedì", 5: "Venerdì", 6: "Sabato", 7: "Domenica"
}

MEAL_ORDER = {
    "Colazione": 1,
    "Spuntino": 2,
    "Pranzo": 3,
    "Merenda": 4,
    "Cena": 5,
}


def _pdf_number(value) -> float:
    """Converte Decimal/None/stringhe numeriche in float per aggregazioni e PDF."""
    if value is None or value == "":
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _pdf_text(value) -> str:
    """Testo sicuro per i Paragraph ReportLab."""
    return html.escape(str(value if value not in (None, "") else "N/D"))


def _micronutrient_overview_dataframe(result: dict) -> pd.DataFrame:
    """Converte il risultato BE nel modello visuale dell'overview micronutrienti."""
    rows = []

    for item in (result or {}).get("rows", []):
        unit = str(item.get("unit") or "").strip()
        label = str(item.get("micronutrient") or "N/D")
        if unit:
            label = f"{label} ({unit})"

        # Usa lo stato semantico calcolato dal backend EFSA quando disponibile.
        # Il fallback mantiene compatibilita con versioni precedenti del service.
        status = str(item.get("comparison_status") or "").strip().upper()
        if not status:
            current_value = float(item.get("current_daily_value") or 0)
            minimum_value = item.get("minimum_rda")
            maximum_value = item.get("maximum_rda")
            reference_type = str(item.get("reference_type") or "").strip().upper()
            maximum_type = str(item.get("maximum_type") or "").strip().upper()

            if minimum_value is not None and current_value < float(minimum_value):
                status = "LOW_AI" if reference_type in {"AI", "SAFE_ADEQUATE"} else "LOW_PRI"
            elif maximum_value is not None and current_value > float(maximum_value):
                status = (
                    "HIGH_WARNING"
                    if maximum_type in {"SAFE_LEVEL", "SAFE_ADEQUATE"}
                    else "HIGH_UL"
                )
            else:
                status = "OK"

        rows.append({
            "Micronutriente": label,
            "Valore attuale": round(float(item.get("current_daily_value") or 0), 2),
            "Min consigliato": (
                round(float(item["minimum_rda"]), 2)
                if item.get("minimum_rda") is not None else None
            ),
            "Max consigliato": (
                round(float(item["maximum_rda"]), 2)
                if item.get("maximum_rda") is not None else None
            ),
            "__status": status,
            "__note": str(item.get("comparison_note") or ""),
        })

    return pd.DataFrame(rows)


def _render_micronutrient_overview_table(df: pd.DataFrame, key: str) -> None:
    """Renderizza l'overview con colorazione dell'intera riga direttamente in AG Grid."""
    if df.empty:
        return

    grid_df = df.copy()
    gb = GridOptionsBuilder.from_dataframe(grid_df)

    gb.configure_default_column(
        editable=False,
        sortable=True,
        filter=False,
        resizable=True,
    )
    gb.configure_column("Micronutriente", flex=2.0, minWidth=240, pinned="left")
    gb.configure_column("Valore attuale", type="numericColumn", flex=1.0, minWidth=140)
    gb.configure_column("Min consigliato", type="numericColumn", flex=1.0, minWidth=140)
    gb.configure_column("Max consigliato", type="numericColumn", flex=1.0, minWidth=140)
    gb.configure_column("__status", hide=True, suppressColumnsToolPanel=True)
    gb.configure_column("__note", hide=True, suppressColumnsToolPanel=True)

    # Semantica grafica:
    # - arancione: apporto sotto PRI/AI -> attenzione sull'adeguatezza, non diagnosi;
    # - rosso: superamento di un vero UL;
    # - giallo: superamento di un safe level / livello prudenziale;
    # - grigio: confronto non valido/non configurato;
    # - verde tenue: riferimento rispettato.
    row_style = JsCode(r"""
    function(params) {
        const status = String((params.data && params.data.__status) || 'OK').toUpperCase();

        if (status === 'HIGH_UL') {
            return {
                backgroundColor: '#FDE2E2',
                color: '#8B1E1E',
                fontWeight: '600',
                borderLeft: '5px solid #DC2626'
            };
        }

        if (status === 'LOW_PRI' || status === 'LOW_AI') {
            return {
                backgroundColor: '#FFF0DF',
                color: '#7C3E00',
                fontWeight: '600',
                borderLeft: '5px solid #F97316'
            };
        }

        if (status === 'HIGH_WARNING') {
            return {
                backgroundColor: '#FFF7CC',
                color: '#6B5200',
                fontWeight: '600',
                borderLeft: '5px solid #EAB308'
            };
        }

        if (status === 'NOT_COMPARABLE' || status === 'NO_REFERENCE') {
            return {
                backgroundColor: '#F1F3F5',
                color: '#667085',
                borderLeft: '5px solid #98A2B3'
            };
        }

        return {
            backgroundColor: '#EAF7EE',
            color: '#205C37',
            borderLeft: '5px solid #22C55E'
        };
    }
    """)

    gb.configure_grid_options(
        getRowStyle=row_style,
        rowHeight=36,
        headerHeight=40,
        domLayout="normal",
        suppressRowHoverHighlight=False,
        tooltipShowDelay=250,
    )

    grid_options = gb.build()
    height = min(520, 48 + max(1, len(grid_df)) * 36)

    AgGrid(
        grid_df,
        gridOptions=grid_options,
        allow_unsafe_jscode=True,
        fit_columns_on_grid_load=False,
        height=height,
        theme="streamlit",
        key=key,
    )


def _render_micronutrient_reference_messages(result: dict) -> None:
    """Legenda e avvisi compatti comuni alle due overview."""
    result_rows = list((result or {}).get("rows", []))
    not_comparable = [r for r in result_rows if r.get("comparison_status") == "NOT_COMPARABLE"]
    no_reference = [r for r in result_rows if r.get("comparison_status") == "NO_REFERENCE"]

    st.caption(
        "🟠 sotto PRI/AI = apporto sotto il riferimento · "
        "🔴 sopra UL = superamento del limite tollerabile · "
        "🟡 sopra safe level = attenzione prudenziale · "
        "🩶 grigio = non confrontabile · "
        "🟢 verde = riferimento rispettato."
    )

    if not_comparable:
        names = ", ".join(str(r.get("micronutrient")) for r in not_comparable)
        st.info(
            "Confronto prudenzialmente disabilitato per basi nutrizionali non equivalenti: "
            f"{names}. I valori attuali restano visibili, ma min/max non generano alert."
        )

    if no_reference:
        names = ", ".join(str(r.get("micronutrient")) for r in no_reference)
        st.warning(f"Riferimento tipologico non configurato per: {names}.")

def _diet_pdf_filename(diet_name: str) -> str:
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", str(diet_name or "piano_alimentare")).strip("_.")
    return f"{safe_name or 'piano_alimentare'}.pdf"


def _build_diet_pdf(diet: dict, patient_name: str) -> bytes:
    """Genera il documento PDF del piano alimentare interamente in memoria."""
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER, TA_LEFT
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import (
            PageBreak,
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )
    except ImportError as exc:
        raise RuntimeError(
            "Per generare il PDF è necessario installare il package 'reportlab'."
        ) from exc

    items = list(diet.get("items") or [])
    daily_totals = {
        day_code: {"kcal": 0.0, "carbs": 0.0, "fats": 0.0, "prot": 0.0}
        for day_code in GIORNI_MAP
    }
    items_by_day = {day_code: [] for day_code in GIORNI_MAP}
    shopping = {}

    for item in items:
        day_code = item.get("giorno_settimana")
        if day_code in items_by_day:
            items_by_day[day_code].append(item)
            daily_totals[day_code]["kcal"] += _pdf_number(item.get("kcal_calculated"))
            daily_totals[day_code]["carbs"] += _pdf_number(item.get("carbs_calculated"))
            daily_totals[day_code]["fats"] += _pdf_number(item.get("fats_calculated"))
            daily_totals[day_code]["prot"] += _pdf_number(item.get("prot_calculated"))

        item_name = str(item.get("item_name") or item.get("food_name") or "N/D").strip()
        normalized_name = item_name.casefold()
        if normalized_name not in shopping:
            shopping[normalized_name] = {"label": item_name, "grams": 0.0}
        shopping[normalized_name]["grams"] += _pdf_number(item.get("grams"))

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=str(diet.get("diet_name") or "Piano alimentare"),
        author="Diet App",
    )

    palette = {
        "primary": colors.HexColor("#2F6B4F"),
        "primary_light": colors.HexColor("#EAF4EE"),
        "header": colors.HexColor("#F3F5F4"),
        "border": colors.HexColor("#D8DEDA"),
        "text": colors.HexColor("#1F2933"),
        "muted": colors.HexColor("#667085"),
    }

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "DietTitle",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=22,
        leading=27,
        textColor=palette["primary"],
        alignment=TA_CENTER,
        spaceAfter=8,
    )
    subtitle_style = ParagraphStyle(
        "DietSubtitle",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=10.5,
        leading=14,
        textColor=palette["muted"],
        alignment=TA_CENTER,
        spaceAfter=14,
    )
    h1_style = ParagraphStyle(
        "DietH1",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=15,
        leading=19,
        textColor=palette["primary"],
        spaceBefore=4,
        spaceAfter=8,
    )
    h2_style = ParagraphStyle(
        "DietH2",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=11.5,
        leading=15,
        textColor=palette["text"],
        spaceBefore=7,
        spaceAfter=5,
    )
    body_style = ParagraphStyle(
        "DietBody",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=9.5,
        leading=14,
        textColor=palette["text"],
        alignment=TA_LEFT,
        spaceAfter=6,
    )
    small_style = ParagraphStyle(
        "DietSmall",
        parent=body_style,
        fontSize=8,
        leading=10,
        spaceAfter=0,
    )
    table_header_style = ParagraphStyle(
        "DietTableHeader",
        parent=small_style,
        fontName="Helvetica-Bold",
        textColor=palette["text"],
        alignment=TA_CENTER,
    )

    def _styled_table(data, col_widths, repeat_rows=1, font_size=8.2, alignments=None):
        table = Table(data, colWidths=col_widths, repeatRows=repeat_rows, hAlign="LEFT")
        commands = [
            ("BACKGROUND", (0, 0), (-1, 0), palette["header"]),
            ("TEXTCOLOR", (0, 0), (-1, 0), palette["text"]),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
            ("FONTSIZE", (0, 0), (-1, -1), font_size),
            ("LEADING", (0, 0), (-1, -1), font_size + 2.2),
            ("GRID", (0, 0), (-1, -1), 0.35, palette["border"]),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]
        if alignments:
            for col_idx, alignment in alignments.items():
                commands.append(("ALIGN", (col_idx, 1), (col_idx, -1), alignment))
                commands.append(("ALIGN", (col_idx, 0), (col_idx, 0), "CENTER"))
        table.setStyle(TableStyle(commands))
        return table

    def _footer(canvas, document):
        canvas.saveState()
        canvas.setStrokeColor(palette["border"])
        canvas.setLineWidth(0.4)
        canvas.line(18 * mm, 12 * mm, A4[0] - 18 * mm, 12 * mm)
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(palette["muted"])
        canvas.drawString(18 * mm, 7.5 * mm, str(diet.get("diet_name") or "Piano alimentare"))
        canvas.drawRightString(A4[0] - 18 * mm, 7.5 * mm, f"Pagina {document.page}")
        canvas.restoreState()

    story = []

    # Pagina 1 - descrizione e macro aggregate per giorno.
    story.append(Spacer(1, 8 * mm))
    story.append(Paragraph("Piano alimentare", title_style))
    story.append(Paragraph(_pdf_text(diet.get("diet_name")), title_style))
    story.append(Paragraph(f"Assistito: {_pdf_text(patient_name)}", subtitle_style))

    story.append(Paragraph("Descrizione del piano", h1_style))
    story.append(Paragraph(_pdf_text(diet.get("descrizione")), body_style))
    if diet.get("warnings"):
        story.append(Paragraph("Avvertenze / note", h2_style))
        story.append(Paragraph(_pdf_text(diet.get("warnings")), body_style))

    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph("Macro per giorno della settimana", h1_style))
    macro_data = [[
        Paragraph("Giorno", table_header_style),
        Paragraph("Kcal", table_header_style),
        Paragraph("Carboidrati (g)", table_header_style),
        Paragraph("Grassi (g)", table_header_style),
        Paragraph("Proteine (g)", table_header_style),
    ]]
    for day_code, day_name in GIORNI_MAP.items():
        totals = daily_totals[day_code]
        macro_data.append([
            day_name,
            f"{totals['kcal']:.1f}",
            f"{totals['carbs']:.1f}",
            f"{totals['fats']:.1f}",
            f"{totals['prot']:.1f}",
        ])
    story.append(_styled_table(
        macro_data,
        [35 * mm, 25 * mm, 34 * mm, 30 * mm, 31 * mm],
        alignments={0: "LEFT", 1: "RIGHT", 2: "RIGHT", 3: "RIGHT", 4: "RIGHT"},
    ))

    # Pagina 2 - lista della spesa aggregata per item_name/food_name.
    story.append(PageBreak())
    story.append(Paragraph("Lista della spesa settimanale", h1_style))
    story.append(Paragraph(
        "Le quantità sono aggregate per alimento sull'intera settimana.",
        body_style,
    ))

    shopping_data = [[
        Paragraph("Alimento", table_header_style),
        Paragraph("Quantità totale (g)", table_header_style),
    ]]
    if shopping:
        for entry in sorted(shopping.values(), key=lambda x: x["label"].casefold()):
            shopping_data.append([
                Paragraph(_pdf_text(entry["label"]), small_style),
                f"{entry['grams']:.1f}",
            ])
    else:
        shopping_data.append(["Nessun alimento presente", "0.0"])

    story.append(_styled_table(
        shopping_data,
        [122 * mm, 43 * mm],
        alignments={0: "LEFT", 1: "RIGHT"},
    ))

    # Pagine successive - un giorno per pagina.
    for day_code, day_name in GIORNI_MAP.items():
        story.append(PageBreak())
        story.append(Paragraph(day_name, h1_style))

        day_items = sorted(
            items_by_day[day_code],
            key=lambda item: (
                MEAL_ORDER.get(str(item.get("meal_type") or ""), 99),
                str(item.get("food_name") or "").casefold(),
            ),
        )

        day_totals = daily_totals[day_code]
        story.append(Paragraph(
            (
                f"Totale giorno: {day_totals['kcal']:.1f} kcal - "
                f"Carboidrati {day_totals['carbs']:.1f} g - "
                f"Grassi {day_totals['fats']:.1f} g - "
                f"Proteine {day_totals['prot']:.1f} g"
            ),
            body_style,
        ))

        detail_data = [[
            Paragraph("Pasto", table_header_style),
            Paragraph("Alimento", table_header_style),
            Paragraph("g", table_header_style),
            Paragraph("Kcal", table_header_style),
            Paragraph("Carb", table_header_style),
            Paragraph("Grassi", table_header_style),
            Paragraph("Prot", table_header_style),
        ]]
        if day_items:
            for item in day_items:
                detail_data.append([
                    Paragraph(_pdf_text(item.get("meal_type")), small_style),
                    Paragraph(_pdf_text(item.get("item_name") or item.get("food_name")), small_style),
                    f"{_pdf_number(item.get('grams')):.1f}",
                    f"{_pdf_number(item.get('kcal_calculated')):.1f}",
                    f"{_pdf_number(item.get('carbs_calculated')):.1f}",
                    f"{_pdf_number(item.get('fats_calculated')):.1f}",
                    f"{_pdf_number(item.get('prot_calculated')):.1f}",
                ])
        else:
            detail_data.append(["-", "Nessun alimento previsto", "-", "-", "-", "-", "-"])

        story.append(_styled_table(
            detail_data,
            [25 * mm, 57 * mm, 14 * mm, 17 * mm, 18 * mm, 18 * mm, 18 * mm],
            font_size=7.6,
            alignments={0: "LEFT", 1: "LEFT", 2: "RIGHT", 3: "RIGHT", 4: "RIGHT", 5: "RIGHT", 6: "RIGHT"},
        ))

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes


st.title("🥗 Gestione Piani Alimentari")

if "user_id" not in st.session_state:
    st.session_state.user_id = "00000000-0000-0000-0000-000000000000"

user_id = st.session_state.user_id

# Recupero pazienti tramite il servizio standard
try:
    pazienti = get_all_patients(tec_conf, user_id)
except Exception as e:
    logger.error(f"Errore nel recupero pazienti: {e}", exc_info=True)
    st.error(f"Errore nel recupero pazienti: {e}")
    pazienti = []

if not pazienti:
    st.warning("Nessun paziente presente nel database.")
    st.stop()

pazienti_dict = {f"{p['cognome']} {p['nome']} ({p.get('data_nascita', '')})": p['id'] for p in pazienti}
paziente_selezionato_label = st.selectbox("Seleziona il paziente", options=list(pazienti_dict.keys()))
current_patient_id = pazienti_dict[paziente_selezionato_label]
paziente_obj = next((p for p in pazienti if p['id'] == current_patient_id), None)
logger.info(f"Paziente selezionato: {paziente_selezionato_label} [ID: {current_patient_id}]")
selected_patient_name = paziente_obj.get('nome')
selected_patient_full_name = (
    f"{paziente_obj.get('cognome', '')} {paziente_obj.get('nome', '')}".strip()
    or selected_patient_name
    or "Assistito"
)


def _clear_editor_state_if_deleted(diet_id):
    """Evita che l'editor mantenga in sessione un piano appena eliminato."""
    st.session_state.pop(f"diet_micronutrient_overview_{diet_id}", None)
    if str(st.session_state.get("diet_loaded_plan_id")) != str(diet_id):
        return

    for key in list(st.session_state.keys()):
        key_str = str(key)
        if key_str.startswith("diet_slot_") or key_str.startswith("ag_diet_slot_"):
            st.session_state.pop(key, None)

    for key in (
        "diet_loaded_plan_id",
        "diet_loaded_plan_name",
        "diet_name_create",
        "diet_description_create",
        "diet_warnings_create",
        "diet_plan_search",
        "diet_plan_import_select",
        "diet_import_needs_recalc",
        "diet_aggregations_dirty",
    ):
        st.session_state.pop(key, None)

    st.session_state["diet_editor_revision"] = int(
        st.session_state.get("diet_editor_revision", 0) or 0
    ) + 1


def _delete_diet_from_ui(diet_id, diet_name):
    delete_diet_plan(tec_conf, current_patient_id, diet_id)
    _clear_editor_state_if_deleted(diet_id)
    st.session_state["diet_list_flash_message"] = (
        f"Piano '{diet_name}' eliminato con successo."
    )
    st.rerun()


_dialog_decorator = getattr(st, "dialog", None) or getattr(st, "experimental_dialog", None)
if _dialog_decorator is not None:
    @_dialog_decorator("Conferma eliminazione")
    def _confirm_delete_diet_dialog(diet_id, diet_name):
        st.warning(
            f"Stai per eliminare definitivamente il piano **{diet_name}** e tutti i suoi alimenti."
        )
        st.caption("L'operazione non può essere annullata.")
        cancel_col, confirm_col = st.columns(2)
        with cancel_col:
            if st.button(
                "Annulla",
                key=f"cancel_delete_diet_{diet_id}",
                use_container_width=True,
            ):
                st.rerun()
        with confirm_col:
            if st.button(
                "Elimina definitivamente",
                key=f"confirm_delete_diet_{diet_id}",
                type="primary",
                use_container_width=True,
            ):
                try:
                    _delete_diet_from_ui(diet_id, diet_name)
                except Exception as exc:
                    logger.error("Errore durante l'eliminazione del piano", exc_info=True)
                    st.error(f"Errore durante l'eliminazione del piano: {exc}")
else:
    _confirm_delete_diet_dialog = None

tab_list, tab_create = st.tabs([
    "📋 Visualizza Diete Esistenti",
    "➕ Crea nuovo piano alimentare o modifica esistente",
])

patient_diets = []

with tab_list:
    st.subheader(f"Piani Alimentari per {selected_patient_name}")

    list_flash_message = st.session_state.pop("diet_list_flash_message", None)
    if list_flash_message:
        st.success(list_flash_message)

    # Esempio di utilizzo dei servizi BE analogamente al modulo biometria che mi hai inviato
    try:
        patient_diets = get_diet_plans(tec_conf, current_patient_id)
        if patient_diets:
            tot = len(patient_diets)
            st.info(f" Sono stati trovati {tot} piani alimentari per questo assistito")
        else:
            st.info("Nessun piano alimentare registrato per questo paziente.")
    except Exception as e:
        st.error(f"Errore nel caricamento delle diete: {e}")
    
    if not patient_diets:
        st.info("Nessun piano alimentare associato a questo assistito.")
    else:
        for diet in patient_diets:
            with st.expander(f"📁 {diet['diet_name']} (ID: {diet['id']})"):
                action_download_col, action_micro_col, action_delete_col = st.columns(3)

                with action_download_col:
                    try:
                        pdf_bytes = _build_diet_pdf(diet, selected_patient_full_name)
                        st.download_button(
                            "📄 Scarica documento PDF",
                            data=pdf_bytes,
                            file_name=_diet_pdf_filename(diet.get("diet_name")),
                            mime="application/pdf",
                            key=f"download_diet_pdf_{diet['id']}",
                            use_container_width=True,
                        )
                    except Exception as exc:
                        logger.error("Errore durante la generazione del PDF", exc_info=True)
                        st.error(f"Impossibile generare il PDF: {exc}")

                with action_micro_col:
                    micro_state_key = f"diet_micronutrient_overview_{diet['id']}"
                    if st.button(
                        "🧬 Micronutrienti",
                        key=f"calculate_diet_micronutrients_{diet['id']}",
                        help="Calcola ora l'apporto medio giornaliero dei micronutrienti.",
                        use_container_width=True,
                    ):
                        try:
                            st.session_state[micro_state_key] = calculate_diet_micronutrients_overview(
                                tec_conf,
                                diet.get("items", []),
                                days_in_plan=7,
                            )
                        except Exception as exc:
                            logger.error("Errore nel calcolo micronutrienti della dieta", exc_info=True)
                            st.error(f"Impossibile calcolare i micronutrienti: {exc}")

                with action_delete_col:
                    delete_clicked = st.button(
                        "🗑️ Elimina dieta",
                        key=f"delete_diet_{diet['id']}",
                        use_container_width=True,
                    )
                    if delete_clicked:
                        if _confirm_delete_diet_dialog is not None:
                            _confirm_delete_diet_dialog(diet["id"], diet.get("diet_name", "Piano alimentare"))
                        else:
                            st.session_state["diet_delete_pending_id"] = str(diet["id"])

                # Fallback per versioni Streamlit prive di st.dialog/experimental_dialog.
                if (
                    _confirm_delete_diet_dialog is None
                    and st.session_state.get("diet_delete_pending_id") == str(diet["id"])
                ):
                    st.warning(
                        f"Confermi l'eliminazione definitiva del piano **{diet.get('diet_name', 'Piano alimentare')}**?"
                    )
                    fallback_cancel_col, fallback_confirm_col = st.columns(2)
                    with fallback_cancel_col:
                        if st.button(
                            "Annulla eliminazione",
                            key=f"fallback_cancel_delete_{diet['id']}",
                            use_container_width=True,
                        ):
                            st.session_state.pop("diet_delete_pending_id", None)
                            st.rerun()
                    with fallback_confirm_col:
                        if st.button(
                            "Conferma eliminazione",
                            key=f"fallback_confirm_delete_{diet['id']}",
                            type="primary",
                            use_container_width=True,
                        ):
                            try:
                                st.session_state.pop("diet_delete_pending_id", None)
                                _delete_diet_from_ui(
                                    diet["id"],
                                    diet.get("diet_name", "Piano alimentare"),
                                )
                            except Exception as exc:
                                logger.error("Errore durante l'eliminazione del piano", exc_info=True)
                                st.error(f"Errore durante l'eliminazione del piano: {exc}")

                micro_result = st.session_state.get(f"diet_micronutrient_overview_{diet['id']}")
                if micro_result is not None:
                    st.markdown("#### 🧬 Overview micronutrienti")
                    st.caption(
                        "Valore attuale = apporto medio giornaliero della dieta "
                        "(totale dei 7 giorni / 7), confrontato con i riferimenti giornalieri configurati."
                    )
                    micro_df = _micronutrient_overview_dataframe(micro_result)
                    if micro_df.empty:
                        st.warning("Nessun alimento valido disponibile per il calcolo.")
                    else:
                        _render_micronutrient_overview_table(
                            micro_df,
                            key=f"micro_overview_saved_{diet['id']}"
                        )
                        _render_micronutrient_reference_messages(micro_result)

                    missing_rda = micro_result.get("missing_rda_names", [])
                    if missing_rda:
                        st.warning(
                            f"Riferimento non configurato per {len(missing_rda)} micronutrienti: "
                            "i relativi min/max sono mostrati come N/D."
                        )

                st.markdown(f"**Descrizione:** {diet.get('descrizione', 'N/D')}")
                st.markdown(f"**Avvertenze:** {diet.get('warnings', 'N/D')}")
                st.markdown("---")
                st.markdown("#### Tabella Dettaglio Dieta")
                
                items = diet.get('items', [])
                if items:
                    formatted_items = []
                    for item in items:
                        formatted_items.append({
                            "Giorno": GIORNI_MAP.get(item['giorno_settimana'], "N/D"),
                            "Pasto": item['meal_type'],
                            "Item": item['food_name'],
                            "Grammi (g)": item['grams'],
                            "Kcal": item['kcal_calculated'],
                            "Carbs (g)": item['carbs_calculated'],
                            "Fats (g)": item['fats_calculated'],
                            "Prot (g)": item['prot_calculated']
                        })
                    df_items = pd.DataFrame(formatted_items)
                    st.dataframe(df_items, use_container_width=True)
                else:
                    st.warning("Nessun alimento registrato in questo piano.")

# Drop-in replacement del blocco originale.
# Richiede gli stessi import/oggetti gia presenti nel file originale:
# st, pd, json, AgGrid, GridOptionsBuilder, GridUpdateMode, JsCode,
# tab_create, get_foods_for_diet_editor, tec_conf, GIORNI_MAP,
# current_patient_id.


@st.cache_data(ttl=3600, show_spinner=False)
def _cached_get_all_foods(conf_cache_key, _tec_conf):
    """Evita una query al DB a ogni full rerun.

    conf_cache_key entra nella chiave della cache; _tec_conf e escluso
    dall'hashing di Streamlit per tollerare configurazioni contenenti
    oggetti non hashabili.
    """
    return get_foods_for_diet_editor(_tec_conf)


def _empty_diet_slot(rows=2):
    return pd.DataFrame({
        "option": [""] * rows,
        "__action_touch": [0] * rows,
        "__sync_request": [0] * rows,
        "Alimento": [None] * rows,
        "Grammi (g)": [0.0] * rows,
    })


def _normalize_slot_df(data):
    """Mantiene nello stato solo i dati realmente editabili.

    Le colonne nutrizionali sono valueGetter JS: non ha senso salvarle
    nel DataFrame di sessione e rispedirle al browser a ogni rerun.
    """
    df = pd.DataFrame(data).copy()

    if "option" not in df.columns:
        df["option"] = ""
    if "__action_touch" not in df.columns:
        df["__action_touch"] = 0
    if "__sync_request" not in df.columns:
        df["__sync_request"] = 0
    if "Alimento" not in df.columns:
        df["Alimento"] = None
    if "Grammi (g)" not in df.columns:
        df["Grammi (g)"] = 0.0

    # ID tecnico stabile della riga. Rimane nello stato ma viene nascosto nella grid.
    if "__row_id" not in df.columns:
        df["__row_id"] = [str(i) for i in range(len(df))]

    # Colonne tecniche:
    # - __action_touch forza un cellValueChanged dopo add/remove;
    # - __sync_request trasporta il comando Ctrl/Cmd+Enter dalla grid a Python.
    df = df[[
        "option", "__row_id", "__action_touch", "__sync_request",
        "Alimento", "Grammi (g)"
    ]]
    df["__row_id"] = df["__row_id"].astype(str)
    df["Grammi (g)"] = pd.to_numeric(df["Grammi (g)"], errors="coerce").fillna(0.0)
    return df


def _slot_signature(df):
    """Firma minimale usata per capire se lo slot e davvero cambiato."""
    return tuple(
        (
            "" if pd.isna(food_name) else str(food_name),
            round(float(grams or 0.0), 4),
        )
        for food_name, grams in df[["Alimento", "Grammi (g)"]].itertuples(index=False, name=None)
    )


def _slot_keys(day, meal):
    safe_base = f"diet_slot_{day}_{meal}"
    return {
        "grid": safe_base,
        "sig": f"{safe_base}__sig",
        "items": f"{safe_base}__items",
        "totals": f"{safe_base}__totals",
    }


def _zero_totals():
    # L'editor mantiene solo i macro. I micronutrienti vengono calcolati on-demand.
    return {
        "kcal": 0.0,
        "carbs": 0.0,
        "fats": 0.0,
        "prot": 0.0,
    }


def _process_slot(df, day, meal, food_dict, food_js_db, giorno_code):
    """Calcola un singolo slot solo quando Alimento/Grammi cambiano."""
    processed_items = []
    totals = _zero_totals()

    for food_name, grams in df[["Alimento", "Grammi (g)"]].itertuples(index=False, name=None):
        if pd.isna(food_name):
            continue

        food_name = str(food_name)
        grams = float(grams or 0.0)

        if grams <= 0 or food_name not in food_dict or food_name not in food_js_db:
            continue

        nutrition = food_js_db[food_name]
        ratio = grams / 100.0

        # Stesso arrotondamento visuale del valueGetter JS originale.
        kcal = round(nutrition["kcal"] * ratio, 1)
        carbs = round(nutrition["carbs"] * ratio, 1)
        fats = round(nutrition["fats"] * ratio, 1)
        prot = round(nutrition["prot"] * ratio, 1)
        processed_items.append({
            "giorno_settimana": giorno_code,
            "giorno_label": day,
            "meal_type": meal,
            "food_id": food_dict[food_name]["id"],
            "food_name": food_name,
            "grams": grams,
            "kcal": kcal,
            "carbs": carbs,
            "fats": fats,
            "prot": prot,
        })

        totals["kcal"] += kcal
        totals["carbs"] += carbs
        totals["fats"] += fats
        totals["prot"] += prot

    return processed_items, totals


def _aggregate_cached_totals(days, meals):
    """Aggrega solo 35 piccoli dizionari gia calcolati, senza riprocessare le righe."""
    weekly = _zero_totals()
    daily = {day: _zero_totals() for day in days}

    for day in days:
        for meal in meals:
            totals_key = _slot_keys(day, meal)["totals"]
            slot_totals = st.session_state.get(totals_key, _zero_totals())

            for key, value in slot_totals.items():
                weekly[key] += value
                daily[day][key] += value

    return weekly, daily


def _collect_cached_items(days, meals):
    """Viene usata solo al salvataggio: nessun master DataFrame a ogni rerun."""
    items = []
    for day in days:
        for meal in meals:
            items.extend(st.session_state.get(_slot_keys(day, meal)["items"], []))
    return items


def _reset_diet_editor_state(clear_search=True):
    """Azzera esclusivamente lo stato dell'editor dieta, senza toccare il resto della pagina."""
    for key in list(st.session_state.keys()):
        key_str = str(key)
        if key_str.startswith("diet_slot_") or key_str.startswith("ag_diet_slot_"):
            del st.session_state[key]

    for key in (
        "diet_name_create",
        "diet_description_create",
        "diet_warnings_create",
        "diet_loaded_plan_id",
        "diet_loaded_plan_name",
        "diet_aggregations_dirty",
        "diet_last_grid_sync_request",
        "diet_grid_rx_revision",
        "diet_last_consolidation_revision",
        "diet_micronutrient_overview_editor",
    ):
        st.session_state.pop(key, None)

    if clear_search:
        st.session_state.pop("diet_plan_search", None)
        st.session_state.pop("diet_plan_import_select", None)

    st.session_state["diet_editor_revision"] = int(
        st.session_state.get("diet_editor_revision", 0) or 0
    ) + 1


def _load_diet_into_editor(diet):
    """Carica testata e tutti gli item di una dieta precedente nei 35 slot dell'editor."""
    # Non cancelliamo i widget di ricerca: la selezione corrente deve restare visibile.
    _reset_diet_editor_state(clear_search=False)

    st.session_state["diet_name_create"] = diet.get("diet_name") or ""
    st.session_state["diet_description_create"] = diet.get("descrizione") or ""
    st.session_state["diet_warnings_create"] = diet.get("warnings") or ""
    st.session_state["diet_loaded_plan_id"] = diet.get("id")
    st.session_state["diet_loaded_plan_name"] = diet.get("diet_name") or ""

    items_by_slot = {}
    for item in diet.get("items", []):
        day = GIORNI_MAP.get(item.get("giorno_settimana"))
        meal = item.get("meal_type")
        if not day or not meal:
            continue
        items_by_slot.setdefault((day, meal), []).append(item)

    meals = ["Colazione", "Spuntino", "Pranzo", "Merenda", "Cena"]
    revision = int(st.session_state.get("diet_editor_revision", 0) or 0)

    for day in GIORNI_MAP.values():
        for meal in meals:
            imported_items = items_by_slot.get((day, meal), [])
            rows = max(2, len(imported_items))
            slot_df = _empty_diet_slot(rows=rows)
            slot_df["__row_id"] = [
                f"import_{revision}_{day}_{meal}_{idx}" for idx in range(rows)
            ]

            for idx, item in enumerate(imported_items):
                slot_df.at[idx, "Alimento"] = item.get("food_name")
                slot_df.at[idx, "Grammi (g)"] = _safe_float(item.get("grams"))

            keys = _slot_keys(day, meal)
            normalized = _normalize_slot_df(slot_df)
            st.session_state[keys["grid"]] = normalized
            st.session_state[keys["sig"]] = _slot_signature(normalized)
            st.session_state[keys["items"]] = []
            st.session_state[keys["totals"]] = _zero_totals()

    # Dopo che il catalogo alimenti sara disponibile, faremo un consolidamento completo.
    st.session_state["diet_import_needs_recalc"] = True
    st.session_state["diet_aggregations_dirty"] = True


def _recalculate_all_slots(days, meals, food_dict, food_js_db, trigger="unknown"):
    """Consolida in Python TUTTI gli slot usando l'ultimo draft ricevuto dalle grid."""
    day_code_map = {label: code for code, label in GIORNI_MAP.items()}
    _diag_log(
        "recalculate_all_slots_start",
        trigger=trigger,
        active_day=st.session_state.get("diet_active_day"),
        rx_revision=st.session_state.get("diet_grid_rx_revision", 0),
    )

    total_basic_rows = 0
    total_processed = 0
    slot_summaries = []

    for day in days:
        giorno_code = day_code_map.get(day, "lun")
        for meal in meals:
            keys = _slot_keys(day, meal)
            raw_df = st.session_state.get(keys["grid"])
            basic_rows = _basic_valid_rows(raw_df)
            total_basic_rows += basic_rows

            if raw_df is None:
                st.session_state[keys["items"]] = []
                st.session_state[keys["totals"]] = _zero_totals()
                if day == st.session_state.get("diet_active_day"):
                    slot_summaries.append({
                        "day": day, "meal": meal, "state": "missing", "basic_rows": 0
                    })
                continue

            slot_df = _normalize_slot_df(raw_df)
            st.session_state[keys["grid"]] = slot_df

            processed_items, totals = _process_slot(
                slot_df,
                day,
                meal,
                food_dict,
                food_js_db,
                giorno_code,
            )
            st.session_state[keys["items"]] = processed_items
            st.session_state[keys["totals"]] = totals
            total_processed += len(processed_items)

            if basic_rows > 0 or processed_items or day == st.session_state.get("diet_active_day"):
                slot_summaries.append({
                    "day": day,
                    "meal": meal,
                    "basic_rows": basic_rows,
                    "processed_items": len(processed_items),
                    "df": _debug_df_payload(slot_df),
                    "totals": totals,
                })

    st.session_state["diet_aggregations_dirty"] = False
    st.session_state["diet_last_consolidation_revision"] = int(
        st.session_state.get("diet_last_consolidation_revision", 0) or 0
    ) + 1

    _diag_log(
        "recalculate_all_slots_end",
        trigger=trigger,
        total_basic_rows=total_basic_rows,
        total_processed_items=total_processed,
        slot_summaries=slot_summaries,
        consolidation_revision=st.session_state.get("diet_last_consolidation_revision", 0),
    )


def _button_with_optional_shortcut(label, shortcut=None, **kwargs):
    """Usa il parametro shortcut sulle versioni Streamlit che lo supportano."""
    try:
        if shortcut and "shortcut" in inspect.signature(st.button).parameters:
            kwargs["shortcut"] = shortcut
    except (TypeError, ValueError):
        pass
    return st.button(label, **kwargs)


def _aggrid_supports_parameter(name):
    """Feature detection per mantenere compatibilita con piu versioni di st-aggrid."""
    try:
        return name in inspect.signature(AgGrid).parameters
    except (TypeError, ValueError):
        return False


def _extract_aggrid_dataframe(grid_response, debug_context=None):
    """Estrae in modo tollerante il DataFrame restituito da st-aggrid."""
    debug_context = debug_context or {}
    _diag_log(
        "extract_aggrid_dataframe_enter",
        **debug_context,
        response=_debug_response_payload(grid_response),
    )

    if grid_response is None:
        _diag_log("extract_aggrid_dataframe_none_response", **debug_context)
        return None

    if isinstance(grid_response, dict):
        data = grid_response.get("data")
    else:
        data = getattr(grid_response, "data", None)

    if data is None:
        _diag_log("extract_aggrid_dataframe_no_data", **debug_context)
        return None

    try:
        normalized = _normalize_slot_df(data)
        _diag_log(
            "extract_aggrid_dataframe_ok",
            **debug_context,
            normalized=_debug_df_payload(normalized),
            basic_valid_rows=_basic_valid_rows(normalized),
        )
        return normalized
    except Exception as exc:
        logger.warning("Impossibile normalizzare la response AG Grid: %s", exc)
        _diag_log(
            "extract_aggrid_dataframe_error",
            **debug_context,
            error=repr(exc),
            raw_data=_debug_df_payload(data),
        )
        return None


def _make_grid_capture_callback(keys, day, meal):
    """Salva il draft RAW appena st-aggrid notifica un cambio cella.

    Il callback NON calcola macro aggregati: serve esclusivamente come ponte
    affidabile browser -> Python. In questa versione registra anche ogni step
    del passaggio per individuare con precisione dove si perde il dato.
    """
    def _capture(grid_response):
        _diag_log(
            "aggrid_callback_enter",
            day=day,
            meal=meal,
            grid_key=keys["grid"],
            response=_debug_response_payload(grid_response),
            session_before=_debug_df_payload(st.session_state.get(keys["grid"])),
        )

        edited_df = _extract_aggrid_dataframe(
            grid_response,
            {"day": day, "meal": meal, "grid_key": keys["grid"], "source": "callback"},
        )
        if edited_df is None:
            _diag_log(
                "aggrid_callback_no_dataframe",
                day=day,
                meal=meal,
                grid_key=keys["grid"],
            )
            return

        old_df = st.session_state.get(keys["grid"])
        old_sig = None
        if old_df is not None:
            try:
                old_sig = _slot_signature(_normalize_slot_df(old_df))
            except Exception as exc:
                _diag_log(
                    "aggrid_callback_old_signature_error",
                    day=day, meal=meal, error=repr(exc),
                )
                old_sig = None

        new_sig = _slot_signature(edited_df)
        st.session_state[keys["grid"]] = edited_df
        st.session_state[keys["sig"]] = new_sig

        changed = old_sig != new_sig
        if changed:
            st.session_state["diet_aggregations_dirty"] = True
            st.session_state.pop("diet_micronutrient_overview_editor", None)

        st.session_state["diet_grid_rx_revision"] = int(
            st.session_state.get("diet_grid_rx_revision", 0) or 0
        ) + 1

        _diag_log(
            "aggrid_callback_saved_session",
            day=day,
            meal=meal,
            grid_key=keys["grid"],
            changed=changed,
            old_signature=old_sig,
            new_signature=new_sig,
            basic_valid_rows=_basic_valid_rows(edited_df),
            session_after=_debug_df_payload(st.session_state.get(keys["grid"])),
            rx_revision=st.session_state.get("diet_grid_rx_revision", 0),
        )

    return _capture


def _count_valid_rows(df, food_dict=None):
    """Conta le righe che Python ha realmente ricevuto dalla grid."""
    if df is None:
        return 0
    df = _normalize_slot_df(df)
    count = 0
    for food_name, grams in df[["Alimento", "Grammi (g)"]].itertuples(index=False, name=None):
        if pd.isna(food_name):
            continue
        name = str(food_name).strip()
        try:
            grams_value = float(grams or 0)
        except (TypeError, ValueError):
            grams_value = 0.0
        if not name or grams_value <= 0:
            continue
        if food_dict is not None and name not in food_dict:
            continue
        count += 1
    return count


def _safe_float(value):
    """Converte valori numerici DB/API in float senza propagare None/stringhe vuote."""
    if value is None or value == "":
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _normalize_nutrition_field_name(field_name):
    """Normalizza i nomi dei nutrienti provenienti da DB/API.

    Esempi equivalenti: ``Protein (g)``, ``protein-g``, ``protein_g``.
    """
    import re

    normalized = str(field_name or "").strip().lower()
    normalized = normalized.replace("%", "pct")
    normalized = re.sub(r"[^a-z0-9]+", "_", normalized)
    return normalized.strip("_")


def _iter_nutrition_containers(food):
    """Visita ricorsivamente i dizionari nutrizionali senza dipendere dal nesting."""
    if not isinstance(food, dict):
        return

    stack = [food]
    visited = set()
    while stack:
        current = stack.pop()
        current_id = id(current)
        if current_id in visited:
            continue
        visited.add(current_id)
        yield current

        for value in current.values():
            if isinstance(value, dict):
                stack.append(value)


def _first_numeric_value(food, aliases, prefixes=()):
    """Recupera un nutriente tollerando nomenclature diverse del backend.

    Prima prova gli alias esatti normalizzati. Se non trova nulla, puo usare
    dei prefissi controllati (es. ``protein_*``) per campi come
    ``protein_per_100g`` o ``proteins_100g``.
    """
    normalized_aliases = {_normalize_nutrition_field_name(alias) for alias in aliases}
    normalized_prefixes = tuple(_normalize_nutrition_field_name(p) for p in prefixes)

    containers = list(_iter_nutrition_containers(food) or [])

    # 1) Match esatto: e sempre la scelta preferita.
    for container in containers:
        normalized = {_normalize_nutrition_field_name(k): v for k, v in container.items()}
        for alias in normalized_aliases:
            if alias in normalized:
                return _safe_float(normalized[alias])

    # 2) Fallback controllato per varianti del tipo protein_per_100g.
    if normalized_prefixes:
        for container in containers:
            for key, value in container.items():
                normalized_field = _normalize_nutrition_field_name(key)
                if any(
                    normalized_field == prefix or normalized_field.startswith(prefix + "_")
                    for prefix in normalized_prefixes
                ):
                    numeric = _safe_float(value)
                    if numeric != 0.0 or value in (0, 0.0, "0", "0.0"):
                        return numeric

    return 0.0


def _normalize_food_nutrition(food):
    """Crea il modello nutrizionale canonico usato sia da JS sia da Python."""
    return {
        "kcal": _first_numeric_value(food, (
            "kcal", "calories", "calorie", "energy_kcal", "energia_kcal",
        )),
        "fats": _first_numeric_value(food, (
            "fats", "fat", "fat_g", "fats_g", "total_fat",
            "lipids", "lipidi", "grassi", "grassi_g",
        )),
        "carbs": _first_numeric_value(food, (
            "carbs", "carb", "carbs_g", "carbohydrates",
            "carbohydrates_g", "carboidrati", "carboidrati_g",
        )),
        "prot": _first_numeric_value(
            food,
            (
                "prot", "prots", "prot_g", "prots_g",
                "protein", "proteins", "protein_g", "proteins_g",
                "protein (g)", "proteins (g)",
                "protein_100g", "proteins_100g",
                "protein_per_100g", "proteins_per_100g",
                "proteine", "proteine_g", "proteine (g)",
                "proteine_100g", "proteine_per_100g",
            ),
            prefixes=("prot", "prots", "protein", "proteins", "proteine"),
        ),
    }


with tab_create:
    st.subheader("Crea nuovo piano alimentare o modifica esistente")

    # Evita che un piano importato per un assistito rimanga nell'editor
    # quando viene selezionato un assistito diverso.
    if st.session_state.get("diet_editor_patient_id") != current_patient_id:
        _reset_diet_editor_state(clear_search=True)
        st.session_state["diet_editor_patient_id"] = current_patient_id

    flash_message = st.session_state.pop("diet_flash_message", None)
    if flash_message:
        st.success(flash_message)

    # ------------------------------------------------------------------
    # 1. Import facoltativo di un piano gia assegnato all'assistito
    # ------------------------------------------------------------------
    st.markdown("#### 🔎 Importa un piano alimentare esistente")
    search_text = st.text_input(
        "Cerca tra i piani dell'assistito",
        placeholder="Digita il nome del piano alimentare...",
        key="diet_plan_search",
    )

    normalized_search = search_text.strip().lower()
    filtered_diets = [
        diet for diet in patient_diets
        if not normalized_search
        or normalized_search in str(diet.get("diet_name", "")).lower()
    ]
    diet_by_id = {diet["id"]: diet for diet in filtered_diets}

    selected_import_id = st.selectbox(
        "Piano da importare",
        options=[None] + list(diet_by_id.keys()),
        format_func=lambda diet_id: (
            "Seleziona un piano..."
            if diet_id is None
            else str(diet_by_id[diet_id].get("diet_name", diet_id))
        ),
        key="diet_plan_import_select",
    )

    # La selezione importa automaticamente il piano una sola volta.
    # I rerun successivi non sovrascrivono le modifiche fatte dall'utente.
    if (
        selected_import_id is not None
        and selected_import_id != st.session_state.get("diet_loaded_plan_id")
    ):
        _load_diet_into_editor(diet_by_id[selected_import_id])
        st.rerun()

    loaded_plan_id = st.session_state.get("diet_loaded_plan_id")
    if loaded_plan_id is not None:
        st.info(
            f"Piano importato: **{st.session_state.get('diet_loaded_plan_name', loaded_plan_id)}**. "
            "Puoi modificarlo e scegliere se aggiornare l'esistente o salvarlo come nuovo."
        )

    if st.button(
        "🧹 Inizia un nuovo piano vuoto",
        key="reset_diet_editor",
        use_container_width=False,
    ):
        _reset_diet_editor_state(clear_search=True)
        st.session_state["diet_editor_patient_id"] = current_patient_id
        st.rerun()

    st.markdown("---")

    # ------------------------------------------------------------------
    # 2. Metadati piano
    # ------------------------------------------------------------------
    col_n1, col_n2 = st.columns(2)
    with col_n1:
        diet_name = st.text_input(
            "Nome Piano Alimentare",
            placeholder="es. Massa Pulita Autunno",
            key="diet_name_create",
        )
    with col_n2:
        descrizione = st.text_input(
            "Descrizione / Obiettivi",
            key="diet_description_create",
        )

    warnings = st.text_area(
        "Avvertenze o Note cliniche",
        key="diet_warnings_create",
    )
    st.markdown("---")

    # ------------------------------------------------------------------
    # 3. Alimenti: query DB cached + lookup costruiti una sola volta
    #    durante il full rerun. I fragment rerun non rieseguono questo blocco.
    # ------------------------------------------------------------------
    conf_cache_key = json.dumps(tec_conf, sort_keys=True, default=str)
    raw_foods = _cached_get_all_foods(conf_cache_key, tec_conf)

    food_dict = {f["item_name"]: f for f in raw_foods}

    # Un solo modello canonico condiviso da JavaScript (macro immediati in grid)
    # e Python (aggregazioni). Il resolver tollera i nomi campo piu comuni del BE.
    food_js_db = {
        f["item_name"]: _normalize_food_nutrition(f)
        for f in raw_foods
    }
    food_catalog_version = str(hash(tuple(sorted(food_js_db.keys()))))

    # Registra una volta per sessione le capability reali dell'ambiente.
    if not st.session_state.get("diet_debug_environment_logged", False):
        try:
            aggrid_version = importlib.metadata.version("streamlit-aggrid")
        except Exception:
            aggrid_version = "unknown"
        _diag_log(
            "environment",
            streamlit_version=getattr(st, "__version__", "unknown"),
            streamlit_aggrid_version=aggrid_version,
            aggrid_supports_callback=_aggrid_supports_parameter("callback"),
            aggrid_supports_update_on=_aggrid_supports_parameter("update_on"),
            aggrid_supports_server_sync_strategy=_aggrid_supports_parameter("server_sync_strategy"),
            data_return_mode_available=DataReturnMode is not None,
            food_count=len(food_dict),
        )
        st.session_state["diet_debug_environment_logged"] = True

    # Il DB alimenti non viene piu incorporato 4 volte nei valueGetter.
    # Viene passato UNA SOLA VOLTA per griglia tramite params.context.foodDb.
    # Autocomplete compatibile con streamlit-aggrid:
    # - l'input resta un normale editor della cella (nessun popup AG Grid)
    # - il menu viene appeso al document dell'iframe e ancorato all'input corrente
    # Questo evita sia il bug del <datalist> nativo sia i problemi di mount
    # dei custom popup editor su alcune versioni di streamlit-aggrid.
    food_autocomplete_editor = JsCode(r"""
class FoodAutocompleteEditor {
    init(params) {
        this.params = params;
        this.foodDb = (params.context && params.context.foodDb) || {};
        this.foods = Object.keys(this.foodDb);
        this.maxVisibleOptions = 40;
        this.filteredFoods = [];
        this.highlightedIndex = -1;
        this.menuOpen = false;

        this.eGui = document.createElement('div');
        this.eGui.style.width = '100%';
        this.eGui.style.height = '100%';

        this.eInput = document.createElement('input');
        this.eInput.type = 'text';
        this.eInput.value = params.value || '';
        this.eInput.autocomplete = 'off';
        this.eInput.spellcheck = false;
        this.eInput.style.width = '100%';
        this.eInput.style.height = '100%';
        this.eInput.style.boxSizing = 'border-box';
        this.eInput.style.padding = '4px 8px';
        this.eInput.style.border = '1px solid #6c8ebf';
        this.eInput.style.outline = 'none';
        this.eInput.style.font = 'inherit';
        this.eInput.style.background = 'white';

        this.eGui.appendChild(this.eInput);

        // Il menu vive fuori dalla cella, cosi non viene tagliato dall'overflow
        // di AG Grid e resta ancorato all'input della riga effettivamente editata.
        this.eMenu = document.createElement('div');
        this.eMenu.style.position = 'fixed';
        this.eMenu.style.display = 'none';
        this.eMenu.style.maxHeight = '220px';
        this.eMenu.style.overflowY = 'auto';
        this.eMenu.style.background = 'white';
        this.eMenu.style.border = '1px solid #c9c9c9';
        this.eMenu.style.borderRadius = '4px';
        this.eMenu.style.boxShadow = '0 4px 14px rgba(0,0,0,0.16)';
        this.eMenu.style.zIndex = '2147483647';
        this.eMenu.style.boxSizing = 'border-box';
        document.body.appendChild(this.eMenu);

        this.boundReposition = () => this.repositionMenu();
        window.addEventListener('resize', this.boundReposition);
        document.addEventListener('scroll', this.boundReposition, true);

        this.eInput.addEventListener('input', () => {
            this.highlightedIndex = -1;
            this.renderOptions(this.eInput.value);
            this.openMenu();
        });

        this.eInput.addEventListener('mousedown', (event) => {
            event.stopPropagation();
        });

        this.eInput.addEventListener('click', (event) => {
            event.stopPropagation();
            this.renderOptions(this.eInput.value);
            this.openMenu();
        });

        this.eInput.addEventListener('keydown', (event) => {
            if (event.key === 'ArrowDown') {
                event.preventDefault();
                event.stopPropagation();
                if (!this.menuOpen) {
                    this.renderOptions(this.eInput.value);
                    this.openMenu();
                }
                if (this.filteredFoods.length) {
                    this.highlightedIndex = Math.min(
                        this.highlightedIndex + 1,
                        this.filteredFoods.length - 1
                    );
                    this.refreshHighlight();
                }
                return;
            }

            if (event.key === 'ArrowUp') {
                event.preventDefault();
                event.stopPropagation();
                if (this.filteredFoods.length) {
                    this.highlightedIndex = Math.max(this.highlightedIndex - 1, 0);
                    this.refreshHighlight();
                }
                return;
            }

            if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
                event.preventDefault();
                event.stopPropagation();
                if (
                    this.highlightedIndex >= 0 &&
                    this.highlightedIndex < this.filteredFoods.length
                ) {
                    this.eInput.value = this.filteredFoods[this.highlightedIndex];
                }
                this.closeMenu();
                params.stopEditing();

                // Il custom editor intercetta Enter e quindi lo shortcut non
                // arriverebbe a onCellKeyDown. Generiamo direttamente la richiesta.
                setTimeout(() => {
                    if (params.node) {
                        params.node.setDataValue('__sync_request', Date.now());
                    }
                }, 0);
                return;
            }

            if (event.key === 'Enter') {
                event.preventDefault();
                event.stopPropagation();
                if (
                    this.highlightedIndex >= 0 &&
                    this.highlightedIndex < this.filteredFoods.length
                ) {
                    this.eInput.value = this.filteredFoods[this.highlightedIndex];
                }
                this.closeMenu();
                params.stopEditing();
                return;
            }

            if (event.key === 'Escape') {
                event.preventDefault();
                event.stopPropagation();
                this.eInput.value = params.value || '';
                this.closeMenu();
                params.stopEditing(true);
                return;
            }

            if (
                event.key === 'ArrowLeft' ||
                event.key === 'ArrowRight' ||
                event.key === 'Home' ||
                event.key === 'End'
            ) {
                event.stopPropagation();
            }
        });
    }

    renderOptions(searchText) {
        const needle = String(searchText || '').trim().toLowerCase();
        const startsWith = [];
        const contains = [];

        for (const food of this.foods) {
            const normalized = String(food).toLowerCase();
            if (!needle || normalized.startsWith(needle)) {
                startsWith.push(food);
            } else if (normalized.includes(needle)) {
                contains.push(food);
            }
        }

        this.filteredFoods = startsWith
            .concat(contains)
            .slice(0, this.maxVisibleOptions);

        this.eMenu.innerHTML = '';

        if (!this.filteredFoods.length) {
            const empty = document.createElement('div');
            empty.textContent = 'Nessun alimento trovato';
            empty.style.padding = '7px 9px';
            empty.style.opacity = '0.65';
            empty.style.fontSize = '12px';
            this.eMenu.appendChild(empty);
            return;
        }

        this.filteredFoods.forEach((food, index) => {
            const option = document.createElement('div');
            option.textContent = food;
            option.style.padding = '7px 9px';
            option.style.cursor = 'pointer';
            option.style.whiteSpace = 'nowrap';
            option.style.overflow = 'hidden';
            option.style.textOverflow = 'ellipsis';

            option.addEventListener('mousedown', (event) => {
                // mousedown conserva il focus sull'editor della riga corrente
                // fino a quando il valore viene acquisito.
                event.preventDefault();
                event.stopPropagation();
                this.eInput.value = food;
                this.closeMenu();
                this.params.stopEditing();
            });

            option.addEventListener('mouseenter', () => {
                this.highlightedIndex = index;
                this.refreshHighlight();
            });

            this.eMenu.appendChild(option);
        });

        this.refreshHighlight();
    }

    repositionMenu() {
        if (!this.menuOpen || !this.eInput || !this.eMenu) {
            return;
        }

        const rect = this.eInput.getBoundingClientRect();
        const viewportHeight = window.innerHeight || document.documentElement.clientHeight;
        const estimatedHeight = Math.min(220, Math.max(36, this.eMenu.scrollHeight));
        const spaceBelow = viewportHeight - rect.bottom;
        const openAbove = spaceBelow < estimatedHeight && rect.top > spaceBelow;

        this.eMenu.style.left = rect.left + 'px';
        this.eMenu.style.width = Math.max(rect.width, 240) + 'px';

        if (openAbove) {
            this.eMenu.style.top = Math.max(0, rect.top - estimatedHeight) + 'px';
        } else {
            this.eMenu.style.top = rect.bottom + 'px';
        }
    }

    openMenu() {
        if (!this.eMenu) {
            return;
        }
        this.menuOpen = true;
        this.eMenu.style.display = 'block';
        this.repositionMenu();
    }

    closeMenu() {
        this.menuOpen = false;
        if (this.eMenu) {
            this.eMenu.style.display = 'none';
        }
    }

    refreshHighlight() {
        const children = Array.from(this.eMenu.children);
        children.forEach((child, index) => {
            child.style.background = index === this.highlightedIndex
                ? '#f0f2f6'
                : 'white';
        });

        if (
            this.highlightedIndex >= 0 &&
            this.highlightedIndex < children.length
        ) {
            children[this.highlightedIndex].scrollIntoView({ block: 'nearest' });
        }
    }

    getGui() {
        return this.eGui;
    }

    afterGuiAttached() {
        // Usa l'istanza appena montata nella cella corrente; non esiste alcun
        // datalist condiviso con la prima riga.
        requestAnimationFrame(() => {
            this.eInput.focus({ preventScroll: true });
            this.eInput.select();
            this.renderOptions(this.eInput.value);
            this.openMenu();
        });
    }

    getValue() {
        return this.eInput.value;
    }

    destroy() {
        window.removeEventListener('resize', this.boundReposition);
        document.removeEventListener('scroll', this.boundReposition, true);
        if (this.eMenu && this.eMenu.parentNode) {
            this.eMenu.parentNode.removeChild(this.eMenu);
        }
    }
}
""")

    def _js_value_getter(macro_key):
        return JsCode(f"""
function(params) {{
    const foodDb = (params.context && params.context.foodDb) || {{}};
    const row = params.data || {{}};
    const food = foodDb[row.Alimento];
    const grams = Number(row['Grammi (g)']);

    if (!food || !Number.isFinite(grams) || grams <= 0) {{
        return 0.0;
    }}

    const value = (Number(food['{macro_key}'] || 0) * grams) / 100.0;
    return Math.round(value * 10) / 10;
}}
""")

    js_kcal = _js_value_getter("kcal")
    js_fats = _js_value_getter("fats")
    js_carbs = _js_value_getter("carbs")
    js_prot = _js_value_getter("prot")

    # BUGFIX 1: i valueGetter sono client-side, ma AG Grid non garantisce
    # il refresh delle colonne dipendenti quando cambia una cella editabile.
    # Forziamo quindi il repaint dei soli macro della riga modificata.
    js_refresh_macro_columns = JsCode(r"""
function(params) {
    if (!params || !params.api || !params.node) {
        return;
    }

    const changedCol = params.column && params.column.getColId
        ? params.column.getColId()
        : null;

    if (changedCol === 'Alimento' || changedCol === 'Grammi (g)') {
        try {
            console.warn('[DIET-GRID-DEBUG] cellValueChanged', {
                column: changedCol,
                rowIndex: params.node ? params.node.rowIndex : null,
                rowData: params.data
            });
        } catch (e) {}
        params.api.refreshCells({
            rowNodes: [params.node],
            columns: ['Kcal', 'Fats', 'Carbs', 'Prots'],
            force: true
        });
    }
}
""")

    # Ctrl/Cmd+Enter dentro l'iframe AG Grid non sempre raggiunge il bottone
    # Streamlit esterno. Trasformiamo quindi lo shortcut in un evento tecnico
    # della grid, che verra interpretato da Python come richiesta di sync.
    js_grid_keyboard_shortcuts = JsCode(r"""
function(params) {
    const event = params && params.event;
    if (!event) {
        return;
    }

    if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') {
        event.preventDefault();
        event.stopPropagation();
        params.api.stopEditing();

        const node = params.node || params.api.getDisplayedRowAtIndex(0);
        if (node) {
            node.setDataValue('__sync_request', Date.now());
        }
    }
}
""")

    # Prima colonna della grid: X rimuove la riga, + ne inserisce una subito sotto.
    row_options_renderer = JsCode(r"""
class RowOptionsRenderer {
    init(params) {
        this.params = params;

        this.eGui = document.createElement('div');
        this.eGui.style.display = 'flex';
        this.eGui.style.alignItems = 'center';
        this.eGui.style.justifyContent = 'center';
        this.eGui.style.gap = '8px';
        this.eGui.style.height = '100%';

        const makeButton = (label, title) => {
            const button = document.createElement('button');
            button.type = 'button';
            button.textContent = label;
            button.title = title;
            button.style.border = 'none';
            button.style.background = 'transparent';
            button.style.padding = '0 4px';
            button.style.fontSize = '20px';
            button.style.lineHeight = '1';
            button.style.fontWeight = '700';
            button.style.cursor = 'pointer';
            button.style.userSelect = 'none';
            return button;
        };

        const deleteButton = makeButton('×', 'Rimuovi riga');
        const addButton = makeButton('+', 'Aggiungi riga sotto');

        const stopGridEvent = (event) => {
            event.preventDefault();
            event.stopPropagation();
        };
        deleteButton.addEventListener('mousedown', stopGridEvent);
        addButton.addEventListener('mousedown', stopGridEvent);

        deleteButton.addEventListener('click', (event) => {
            stopGridEvent(event);

            // Mantiene sempre almeno una riga, cosi resta disponibile il pulsante +.
            if (params.api.getDisplayedRowCount() <= 1) {
                return;
            }

            params.api.stopEditing();
            const oldIndex = params.node.rowIndex == null ? 0 : params.node.rowIndex;
            params.api.applyTransaction({ remove: [params.data] });

            // Genera un evento nativo cellValueChanged DOPO la rimozione.
            const remaining = params.api.getDisplayedRowCount();
            const targetIndex = Math.min(oldIndex, remaining - 1);
            const targetNode = params.api.getDisplayedRowAtIndex(targetIndex);
            if (targetNode) {
                targetNode.setDataValue('__action_touch', Date.now());
            }
        });

        addButton.addEventListener('click', (event) => {
            stopGridEvent(event);
            params.api.stopEditing();

            const id = (typeof crypto !== 'undefined' && crypto.randomUUID)
                ? crypto.randomUUID()
                : `row_${Date.now()}_${Math.random().toString(36).slice(2)}`;

            const newRow = {
                option: '',
                __row_id: id,
                __action_touch: 0,
                __sync_request: 0,
                Alimento: null,
                'Grammi (g)': 0.0
            };

            const currentIndex = params.node.rowIndex == null
                ? params.api.getDisplayedRowCount() - 1
                : params.node.rowIndex;

            const tx = params.api.applyTransaction({
                add: [newRow],
                addIndex: currentIndex + 1
            });

            // Forza la sincronizzazione con Streamlit solo dopo che la nuova riga esiste.
            const addedNode = tx && tx.add && tx.add.length ? tx.add[0] : null;
            if (addedNode) {
                addedNode.setDataValue('__action_touch', Date.now());
            }
        });

        this.eGui.appendChild(deleteButton);
        this.eGui.appendChild(addButton);

        // Se e l'unica riga, la X resta visibile ma disabilitata.
        if (params.api.getDisplayedRowCount() <= 1) {
            deleteButton.disabled = true;
            deleteButton.style.opacity = '0.35';
            deleteButton.style.cursor = 'default';
        }
    }

    getGui() {
        return this.eGui;
    }

    refresh() {
        return false;
    }
}
""")

    day_options_list = list(GIORNI_MAP.values())
    pasti_options = ["Colazione", "Spuntino", "Pranzo", "Merenda", "Cena"]

    # Un piano appena importato viene subito consolidato, cosi totali e cache Python
    # sono coerenti gia dal primo rendering delle grid precompilate.
    if st.session_state.pop("diet_import_needs_recalc", False):
        _recalculate_all_slots(
            day_options_list,
            pasti_options,
            food_dict,
            food_js_db,
            trigger="import_existing_diet",
        )

    # ------------------------------------------------------------------
    # 4. Lazy rendering: un solo giorno alla volta -> 5 grid invece di 35.
    # ------------------------------------------------------------------
    selected_day = st.radio(
        "Giorno da modificare",
        day_options_list,
        horizontal=True,
        key="diet_active_day",
    )

    # Compatibilita con Streamlit < 1.37: senza fragment continua a
    # funzionare, ma la modifica della grid provochera un full rerun.
    fragment_decorator = getattr(st, "fragment", lambda func: func)

    @fragment_decorator
    def _render_active_day(day):
        st.markdown(f"### 📅 {day}")

        # Riserva due blocchi stabili nell'albero Streamlit.
        # Il riepilogo viene popolato dopo aver letto le risposte AG Grid,
        # ma il container esiste gia sopra le grid: evita il replace() di
        # st.empty(), che causava reflow/flicker degli iframe AG Grid.
        daily_overview_container = st.container()
        sync_container = st.container()
        sync_status_container = st.container()
        grids_container = st.container()

        # Il bottone viene materializzato nel container SOLO dopo aver eseguito
        # le grid. Visivamente resta sopra, ma lato Python le response AG Grid
        # vengono acquisite prima di valutare il comando di consolidamento.
        sync_clicked = False
        grid_sync_requested = False
        meal_caption_slots = []

        with grids_container:
            for pasto in pasti_options:
                with st.expander(f"🍽️ {pasto}", expanded=True):
                    keys = _slot_keys(day, pasto)

                    if keys["grid"] not in st.session_state:
                        initial_df = _empty_diet_slot()
                        initial_df["__row_id"] = [str(i) for i in range(len(initial_df))]
                        st.session_state[keys["grid"]] = initial_df

                    # Normalizza anche eventuali slot rimasti in sessione da versioni precedenti.
                    current_df = _normalize_slot_df(st.session_state[keys["grid"]])
                    st.session_state[keys["grid"]] = current_df
                    grid_df = current_df

                    gb = GridOptionsBuilder.from_dataframe(grid_df)

                    # Colonna tecnica aggiunta internamente da streamlit-aggrid:
                    # serve come row ID ma non deve essere mostrata all'utente.
                    gb.configure_column(
                        "::auto_unique_id::",
                        hide=True,
                        suppressColumnsToolPanel=True,
                    )
                    gb.configure_column(
                        "__row_id",
                        hide=True,
                        suppressColumnsToolPanel=True,
                    )
                    gb.configure_column(
                        "__action_touch",
                        hide=True,
                        suppressColumnsToolPanel=True,
                    )
                    gb.configure_column(
                        "__sync_request",
                        hide=True,
                        suppressColumnsToolPanel=True,
                    )
                    gb.configure_column(
                        "option",
                        headerName="option",
                        editable=False,
                        sortable=False,
                        filter=False,
                        resizable=False,
                        pinned="left",
                        width=92,
                        minWidth=92,
                        maxWidth=92,
                        suppressColumnsToolPanel=True,
                        cellRenderer=row_options_renderer,
                    )

                    gb.configure_column(
                        "Alimento",
                        editable=True,
                        singleClickEdit=True,
                        cellEditor=food_autocomplete_editor,
                        flex=2,
                    )
                    gb.configure_column(
                        "Grammi (g)",
                        editable=True,
                        type="numericColumn",
                        flex=1,
                    )
                    gb.configure_column(
                        "Kcal",
                        valueGetter=js_kcal,
                        type="numericColumn",
                        editable=False,
                        flex=1,
                    )
                    gb.configure_column(
                        "Fats",
                        valueGetter=js_fats,
                        type="numericColumn",
                        editable=False,
                        flex=1,
                    )
                    gb.configure_column(
                        "Carbs",
                        valueGetter=js_carbs,
                        type="numericColumn",
                        editable=False,
                        flex=1,
                    )
                    gb.configure_column(
                        "Prots",
                        valueGetter=js_prot,
                        type="numericColumn",
                        editable=False,
                        flex=1,
                    )

                    gb.configure_grid_options(
                        domLayout="normal",
                        editable=True,
                        context={"foodDb": food_js_db, "foodVersion": food_catalog_version},
                        suppressColumnVirtualisation=False,
                        getRowId=JsCode("function(params) { return String(params.data.__row_id); }"),
                        onCellValueChanged=js_refresh_macro_columns,
                        onCellKeyDown=js_grid_keyboard_shortcuts,
                    )
                    grid_options = gb.build()

                    # La modifica della grid viene rimandata a Python come semplice DRAFT.
                    # Il fragment limita il rerun alla sola giornata attiva; nessuna
                    # aggregazione viene eseguita finche l'utente non preme Sync.
                    # Altezza dinamica: compatta con poche righe, scroll oltre 7 righe.
                    grid_height = min(295, 42 + max(1, len(grid_df)) * 35)

                    # Ponte browser -> Python. Usiamo VALUE_CHANGED come modalita
                    # di compatibilita piu ampia: nelle versioni recenti viene tradotta
                    # nello stesso evento cellValueChanged; nelle versioni meno recenti
                    # evita il caso osservato con NO_UPDATE, in cui la grid rimaneva
                    # aggiornata nel browser ma Python continuava a ricevere il DF iniziale.
                    aggrid_kwargs = dict(
                        gridOptions=grid_options,
                        update_mode=GridUpdateMode.VALUE_CHANGED,
                        allow_unsafe_jscode=True,
                        fit_columns_on_grid_load=False,
                        height=grid_height,
                        theme="streamlit",
                        key=f"ag_{keys['grid']}_{int(st.session_state.get('diet_editor_revision', 0) or 0)}",
                    )

                    # Se update_on e disponibile, azzeriamo gli eventi di default:
                    # VALUE_CHANGED aggiungera esplicitamente cellValueChanged evitando
                    # doppie sottoscrizioni e rerun superflui.
                    if _aggrid_supports_parameter("update_on"):
                        aggrid_kwargs["update_on"] = []

                    # AS_INPUT forza il collector a restituire le righe editate della
                    # grid, nell'ordine di input, senza dipendere da sort/filter.
                    if DataReturnMode is not None:
                        aggrid_kwargs["data_return_mode"] = DataReturnMode.AS_INPUT

                    # Sulle versioni che espongono callback, il draft viene catturato
                    # direttamente nell'on_change del componente PRIMA del rerun.
                    # E il canale primario di sincronizzazione.
                    callback_supported = _aggrid_supports_parameter("callback")
                    if callback_supported:
                        aggrid_kwargs["callback"] = _make_grid_capture_callback(keys, day, pasto)

                    if _aggrid_supports_parameter("server_sync_strategy"):
                        aggrid_kwargs["server_sync_strategy"] = "client_wins"

                    _diag_log(
                        "before_aggrid_call",
                        day=day,
                        meal=pasto,
                        grid_key=keys["grid"],
                        callback_supported=callback_supported,
                        kwargs_flags={
                            "update_mode": str(aggrid_kwargs.get("update_mode")),
                            "has_update_on": "update_on" in aggrid_kwargs,
                            "update_on": aggrid_kwargs.get("update_on"),
                            "has_data_return_mode": "data_return_mode" in aggrid_kwargs,
                            "has_callback": "callback" in aggrid_kwargs,
                            "server_sync_strategy": aggrid_kwargs.get("server_sync_strategy"),
                        },
                        grid_input=_debug_df_payload(grid_df),
                        basic_valid_rows=_basic_valid_rows(grid_df),
                    )

                    response = AgGrid(grid_df, **aggrid_kwargs)

                    _diag_log(
                        "after_aggrid_call",
                        day=day,
                        meal=pasto,
                        grid_key=keys["grid"],
                        response=_debug_response_payload(response),
                        session_snapshot=_debug_df_payload(st.session_state.get(keys["grid"])),
                    )

                    if callback_supported:
                        # Il callback ha gia salvato l'ultima snapshot ricevuta.
                        # Non sovrascriviamo il draft con una response iniziale/stale
                        # provocata da un rerun esterno (es. pressione del bottone).
                        edited_df = _normalize_slot_df(st.session_state[keys["grid"]])
                    else:
                        # Fallback per versioni st-aggrid senza callback.
                        returned_df = _extract_aggrid_dataframe(response, {"day": day, "meal": pasto, "grid_key": keys["grid"], "source": "fallback_after_call"})
                        if returned_df is not None:
                            previous_df = _normalize_slot_df(st.session_state[keys["grid"]])
                            previous_sig = _slot_signature(previous_df)
                            returned_sig = _slot_signature(returned_df)
                            st.session_state[keys["grid"]] = returned_df
                            st.session_state[keys["sig"]] = returned_sig
                            if returned_sig != previous_sig:
                                st.session_state["diet_aggregations_dirty"] = True
                                st.session_state.pop("diet_micronutrient_overview_editor", None)
                                st.session_state["diet_grid_rx_revision"] = int(
                                    st.session_state.get("diet_grid_rx_revision", 0) or 0
                                ) + 1
                            edited_df = returned_df
                        else:
                            edited_df = _normalize_slot_df(st.session_state[keys["grid"]])

                    _diag_log(
                        "grid_python_state_after_capture",
                        day=day,
                        meal=pasto,
                        grid_key=keys["grid"],
                        edited_df=_debug_df_payload(edited_df),
                        basic_valid_rows=_basic_valid_rows(edited_df),
                        session_df=_debug_df_payload(st.session_state.get(keys["grid"])),
                    )

                    # Richiesta di sync proveniente da Ctrl/Cmd+Enter dentro AG Grid.
                    sync_values = pd.to_numeric(
                        edited_df["__sync_request"],
                        errors="coerce",
                    ).fillna(0)
                    latest_sync_request = int(sync_values.max()) if len(sync_values) else 0
                    last_sync_request = int(
                        st.session_state.get("diet_last_grid_sync_request", 0) or 0
                    )
                    if latest_sync_request > last_sync_request:
                        st.session_state["diet_last_grid_sync_request"] = latest_sync_request
                        grid_sync_requested = True

                    # Riserviamo la posizione della caption ma la valorizziamo solo
                    # dopo aver letto tutte le grid. In questo modo, se viene richiesto
                    # un Sync, tutte le caption mostrano la stessa snapshot Python.
                    meal_caption_container = st.container()
                    meal_caption_slots.append((meal_caption_container, pasto, keys))

        # Il bottone e scritto ora nel container creato sopra: visivamente resta
        # prima delle grid, ma il codice ha gia acquisito tutti i draft disponibili.
        with sync_container:
            sync_clicked = _button_with_optional_shortcut(
                "🔄 Aggiorna totali / aggregazioni",
                shortcut="Ctrl+Enter",
                key="sync_diet_aggregations",
                help=(
                    "Consolida in Python i dati RAW gia ricevuti dalle grid e aggiorna "
                    "insieme totali per pasto, giorno e settimana."
                ),
                use_container_width=True,
            )

        # Il consolidamento avviene DOPO aver acquisito le response di tutte le grid:
        # cosi anche l'ultima modifica committata entra nello stesso snapshot.
        if sync_clicked or grid_sync_requested:
            _diag_log(
                "sync_requested",
                active_day=day,
                source="button" if sync_clicked else "grid_shortcut",
                active_day_snapshot={
                    meal: _debug_df_payload(st.session_state.get(_slot_keys(day, meal)["grid"]))
                    for meal in pasti_options
                },
                active_day_basic_valid={
                    meal: _basic_valid_rows(st.session_state.get(_slot_keys(day, meal)["grid"]))
                    for meal in pasti_options
                },
            )
            _recalculate_all_slots(
                day_options_list,
                pasti_options,
                food_dict,
                food_js_db,
                trigger="button" if sync_clicked else "grid_shortcut",
            )

        for meal_caption_container, pasto, keys in meal_caption_slots:
            meal_totals = st.session_state.get(keys["totals"], _zero_totals())
            with meal_caption_container:
                st.caption(
                    f"\u03a3 Macro {pasto}  |  "
                    f"Kcal **{meal_totals['kcal']:.1f}**  |  "
                    f"Carbs **{meal_totals['carbs']:.1f} g**  |  "
                    f"Grassi **{meal_totals['fats']:.1f} g**  |  "
                    f"Proteine **{meal_totals['prot']:.1f} g**"
                )

        # Lo stato viene scritto solo dopo aver letto tutte le grid del rerun,
        # cosi il messaggio riflette davvero la presenza di modifiche pending.
        with sync_status_container:
            if st.session_state.get("diet_aggregations_dirty", False):
                st.caption(
                    "⚠️ Le grid contengono modifiche non ancora consolidate nei totali. "
                    "Premi il pulsante oppure Ctrl+Enter."
                )
            else:
                st.caption("✅ Totali Python allineati all'ultimo consolidamento.")

        # Diagnostica estesa: copia/scarica questo blocco dopo aver riprodotto il bug.
        with st.expander("🧪 Log diagnostico AG Grid → Python", expanded=False):
            active_valid_rows = 0
            diagnostic_rows = []
            for meal_name in pasti_options:
                meal_keys = _slot_keys(day, meal_name)
                meal_df = st.session_state.get(meal_keys["grid"])
                valid_count = _count_valid_rows(meal_df, food_dict)
                basic_count = _basic_valid_rows(meal_df)
                active_valid_rows += valid_count
                diagnostic_rows.append({
                    "Pasto": meal_name,
                    "Righe raw non vuote": basic_count,
                    "Righe valide catalogo": valid_count,
                })

            st.caption(
                f"Revisioni ricevute dalle grid: "
                f"{int(st.session_state.get('diet_grid_rx_revision', 0) or 0)} · "
                f"Righe valide per {day}: {active_valid_rows}"
            )
            st.dataframe(
                pd.DataFrame(diagnostic_rows),
                use_container_width=True,
                hide_index=True,
            )

            log_text = _diag_text()
            st.caption("Log completo (puoi copiarlo oppure scaricarlo come .txt)")
            st.code(log_text or "Nessun log disponibile.", language=None)

            dc1, dc2 = st.columns(2)
            with dc1:
                st.download_button(
                    "⬇️ Scarica log .txt",
                    data=log_text or "Nessun log disponibile.",
                    file_name="diet_grid_debug_log.txt",
                    mime="text/plain",
                    use_container_width=True,
                    key="download_diet_grid_debug_log",
                )
            with dc2:
                if st.button(
                    "🧹 Azzera log",
                    use_container_width=True,
                    key="clear_diet_grid_debug_log",
                ):
                    _diag_clear()
                    st.rerun()

            st.caption(
                "Per un test pulito: azzera il log → modifica un alimento → modifica i grammi "
                "→ premi Aggiorna totali → premi Salva → scarica il log e inoltramelo. "
                "I messaggi [DIET-GRID-DEBUG] sono visibili anche nella console del browser."
            )

        # --------------------------------------------------------------
        # Totali aggregati: usa esclusivamente i piccoli dizionari cached
        # dei 35 slot; nessun concat/groupby sui DataFrame di dettaglio.
        # --------------------------------------------------------------
        weekly_totals, daily_totals = _aggregate_cached_totals(
            day_options_list,
            pasti_options,
        )

        # Il riepilogo giornaliero resta VISIVAMENTE sopra le grid ma viene
        # scritto solo dopo aver acquisito l'ultimo response delle grid.
        # Usiamo un container stabile (non st.empty) e una tabella semplice
        # senza Pandas Styler, molto meno costosa da serializzare/renderizzare.
        with daily_overview_container:
            st.markdown("#### 📊 Aggregazione per giorno della settimana")

            daily_rows = []
            for day_name in day_options_list:
                t = daily_totals[day_name]
                daily_rows.append({
                    "Giorno": day_name,
                    "Kcal": round(t["kcal"], 1),
                    "Carb (g)": round(t["carbs"], 1),
                    "Grassi (g)": round(t["fats"], 1),
                    "Pro (g)": round(t["prot"], 1),
                })

            st.dataframe(
                pd.DataFrame(daily_rows),
                use_container_width=True,
                hide_index=True,
                height=282,
            )
            st.markdown("---")

        # Overview settimanale lasciata sotto le grid come nella versione
        # stabile precedente, evitando di appesantire il blocco superiore.
        st.markdown("---")
        st.markdown("#### 📊 Overview Settimanale")

        if weekly_totals["kcal"] > 0:
            mt1, mt2 = st.columns(2)
            mt1.metric("Kcal Totali Settimana", f"{weekly_totals['kcal']:.1f}")
            mt2.metric("Kcal giornaliere ( media )", f"{weekly_totals['kcal']/7:.1f}")
            mc1, mc2, mc3 = st.columns(3)
            mc1.metric("Carb medi", f"{weekly_totals['carbs']/7:.1f}g")
            mc2.metric("Grassi medi", f"{weekly_totals['fats']/7:.1f}g")
            mc3.metric("Proteine medie", f"{weekly_totals['prot']/7:.1f}g")

        else:
            st.info("Nessun alimento inserito o grammi a 0.")

        st.markdown("#### 🧬 Overview micronutrienti")
        st.caption(
            "Il calcolo non viene eseguito automaticamente: parte solo su richiesta e usa "
            "l'apporto medio giornaliero del piano (totale dei 7 giorni / 7)."
        )

        if st.button(
            "🧬 Calcola overview micronutrienti",
            key="calculate_editor_micronutrients",
            use_container_width=True,
        ):
            _recalculate_all_slots(
                day_options_list,
                pasti_options,
                food_dict,
                food_js_db,
                trigger="micronutrients_button",
            )
            micro_items = _collect_cached_items(day_options_list, pasti_options)
            if not micro_items:
                st.session_state.pop("diet_micronutrient_overview_editor", None)
                st.error("Inserisci almeno un alimento con quantità maggiore di 0.")
            else:
                try:
                    st.session_state["diet_micronutrient_overview_editor"] = (
                        calculate_diet_micronutrients_overview(
                            tec_conf,
                            micro_items,
                            days_in_plan=7,
                        )
                    )
                except Exception as exc:
                    logger.error("Errore nel calcolo micronutrienti dell'editor", exc_info=True)
                    st.error(f"Impossibile calcolare i micronutrienti: {exc}")

        editor_micro_result = st.session_state.get("diet_micronutrient_overview_editor")
        if editor_micro_result is not None:
            editor_micro_df = _micronutrient_overview_dataframe(editor_micro_result)
            if not editor_micro_df.empty:
                _render_micronutrient_overview_table(
                    editor_micro_df,
                    key="micro_overview_editor"
                )
                _render_micronutrient_reference_messages(editor_micro_result)

            missing_rda = editor_micro_result.get("missing_rda_names", [])
            if missing_rda:
                st.warning(
                    f"Riferimento non configurato per {len(missing_rda)} micronutrienti: "
                    "i relativi min/max sono mostrati come N/D."
                )

        st.markdown("---")

        # --------------------------------------------------------------
        # Persistenza: due azioni distinte sullo stesso draft corrente.
        # --------------------------------------------------------------
        action_col1, action_col2 = st.columns(2)
        with action_col1:
            update_clicked = st.button(
                "♻️ Aggiorna piano alimentare esistente",
                key="update_diet_plan_existing",
                disabled=st.session_state.get("diet_loaded_plan_id") is None,
                use_container_width=True,
            )
        with action_col2:
            save_new_clicked = st.button(
                "💾 Salva come nuovo piano alimentare",
                type="primary",
                key="save_diet_plan_create",
                use_container_width=True,
            )

        if update_clicked or save_new_clicked:
            action_name = "update" if update_clicked else "save_new"

            # Prima della persistenza consolidiamo sempre l'ultimo stato ricevuto
            # da tutte le grid, anche se l'utente non ha premuto Sync.
            _diag_log(
                "persist_clicked_before_recalculate",
                action=action_name,
                active_day=day,
                rx_revision=st.session_state.get("diet_grid_rx_revision", 0),
            )
            _recalculate_all_slots(
                day_options_list,
                pasti_options,
                food_dict,
                food_js_db,
                trigger=f"{action_name}_button",
            )

            temp_processed_items = _collect_cached_items(
                day_options_list,
                pasti_options,
            )
            normalized_name = (diet_name or "").strip()

            if not normalized_name:
                st.error("Inserisci un nome per il piano alimentare.")
            elif not temp_processed_items:
                total_draft_rows = sum(
                    _count_valid_rows(
                        st.session_state.get(_slot_keys(d, m)["grid"]),
                        food_dict,
                    )
                    for d in day_options_list
                    for m in pasti_options
                )
                st.error(
                    "Nessun alimento valido disponibile per il salvataggio. "
                    f"Python vede attualmente {total_draft_rows} righe valide nelle grid."
                )
            else:
                diet_payload = {
                    "patient_id": current_patient_id,
                    "user_id": user_id,
                    "diet_name": normalized_name,
                    "descrizione": descrizione,
                    "warnings": warnings,
                }

                try:
                    if save_new_clicked:
                        # Nuovo piano: il nome deve essere univoco per l'assistito.
                        if diet_name_exists(
                            tec_conf,
                            current_patient_id,
                            normalized_name,
                        ):
                            st.error(
                                f"Esiste gia un piano alimentare chiamato '{normalized_name}' "
                                "per questo assistito. Scegli un nome diverso."
                            )
                        else:
                            new_diet_id = add_diet_plan(
                                tec_conf,
                                diet_payload,
                                temp_processed_items,
                            )
                            st.session_state["diet_flash_message"] = (
                                f"Nuovo piano '{normalized_name}' salvato con successo "
                                f"(ID: {new_diet_id})."
                            )
                            _reset_diet_editor_state(clear_search=True)
                            st.session_state["diet_editor_patient_id"] = current_patient_id
                            st.rerun()

                    else:
                        loaded_id = st.session_state.get("diet_loaded_plan_id")
                        if loaded_id is None:
                            st.error("Importa prima un piano alimentare da aggiornare.")
                        elif diet_name_exists(
                            tec_conf,
                            current_patient_id,
                            normalized_name,
                            exclude_diet_id=loaded_id,
                        ):
                            st.error(
                                f"Esiste gia un altro piano alimentare chiamato '{normalized_name}' "
                                "per questo assistito."
                            )
                        else:
                            update_diet_plan(
                                tec_conf,
                                loaded_id,
                                diet_payload,
                                temp_processed_items,
                            )
                            st.session_state["diet_loaded_plan_name"] = normalized_name
                            st.session_state["diet_flash_message"] = (
                                f"Piano '{normalized_name}' aggiornato con successo."
                            )
                            st.rerun()
                except Exception as exc:
                    logger.error("Errore durante la persistenza del piano alimentare", exc_info=True)
                    st.error(f"Errore durante il salvataggio del piano alimentare: {exc}")

    _render_active_day(selected_day)
