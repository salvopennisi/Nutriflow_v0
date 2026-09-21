import logging
import inspect
import importlib.metadata
from datetime import datetime
from io import BytesIO, StringIO
import html
import re
import uuid
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
    delete_diet_plan,
    diet_name_exists,
    calculate_nutrients_proportional,
    calculate_diet_micronutrients_overview,
    get_recipes_for_diet,
    add_diet_plan_with_recipes,
    update_diet_plan_with_recipes,
    save_recipe_for_diet,
    delete_recipe_from_diet,
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
            "Alimento", "Grammi (g)", "Target settimanale (g)",
            "Assegnati (g)", "Residui (g)", "Kcal", "Fats", "Carbs", "Prots"
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
    "Merenda": 2,
    "Pranzo": 3,
    "Spuntino": 4,
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


def _pdf_quantity_text(item: dict) -> str:
    """Usa i pezzi quando disponibili; la persistenza resta sempre in grammi."""
    raw_pieces = (item or {}).get("pieces") or (item or {}).get("pezzi")
    try:
        pieces = int(float(raw_pieces)) if raw_pieces not in (None, "") else 0
    except Exception:
        pieces = 0
    if pieces > 0:
        return f"{pieces} pz"
    return f"{_pdf_number((item or {}).get('grams')):.1f} g"


def _piece_snapshot_db_key(item: dict) -> str:
    source_type = "recipe" if (item or {}).get("recipe_id") else "food"
    source_id = (item or {}).get("recipe_id") or (item or {}).get("food_id") or ""
    return "|".join([
        str((item or {}).get("giorno_settimana") or ""),
        str((item or {}).get("meal_type") or "").strip(),
        source_type,
        str(source_id),
        f"{_pdf_number((item or {}).get('grams')):.2f}",
    ])


def _diet_with_transient_pieces(diet: dict) -> dict:
    """Reinietta i pezzi dalla sessione senza salvarli nel DB."""
    diet_id = str((diet or {}).get("id") or "")
    snapshot = st.session_state.get(f"diet_piece_snapshot_{diet_id}") or {}
    if not snapshot:
        return diet

    queues = {str(key): list(values or []) for key, values in snapshot.items()}
    result = dict(diet or {})
    enriched_items = []
    for raw_item in (diet or {}).get("items", []) or []:
        item = dict(raw_item)
        key = _piece_snapshot_db_key(item)
        values = queues.get(key) or []
        if values:
            item["pieces"] = values.pop(0)
            queues[key] = values
        enriched_items.append(item)
    result["items"] = enriched_items
    return result


def _saved_recipe_map(diet: dict) -> dict:
    """Mappa recipe_id -> recipe usando il payload restituito dal service layer."""
    result = {}
    for recipe in (diet or {}).get("recipes", []) or []:
        recipe_id = recipe.get("id") or recipe.get("recipe_id")
        if recipe_id:
            result[str(recipe_id)] = recipe
    return result


def _saved_item_display_name(diet: dict, item: dict) -> str:
    recipe_id = item.get("recipe_id")
    if recipe_id:
        recipe = _saved_recipe_map(diet).get(str(recipe_id), {})
        return str(recipe.get("name") or item.get("recipe_name") or "Ricetta").strip()
    return str(item.get("item_name") or item.get("food_name") or "N/D").strip()


def _saved_item_food_components(diet: dict, item: dict) -> list:
    """Espande una riga salvata in alimenti reali, utile per spesa e micronutrienti."""
    grams = _pdf_number(item.get("grams"))
    if grams <= 0:
        return []

    recipe_id = item.get("recipe_id")
    if not recipe_id:
        food_name = str(item.get("food_name") or item.get("item_name") or "").strip()
        if not food_name:
            return []
        return [{
            "food_id": item.get("food_id"),
            "food_name": food_name,
            "grams": grams,
        }]

    recipe = _saved_recipe_map(diet).get(str(recipe_id))
    if not recipe:
        return []
    ingredients = list(recipe.get("ingredients") or [])
    recipe_total = sum(_pdf_number(x.get("grams")) for x in ingredients)
    if recipe_total <= 0:
        return []

    factor = grams / recipe_total
    expanded = []
    for ingredient in ingredients:
        nested_food = ingredient.get("food") or ingredient.get("foods") or {}
        if not isinstance(nested_food, dict):
            nested_food = {}
        food_name = str(
            ingredient.get("food_name")
            or ingredient.get("item_name")
            or nested_food.get("item_name")
            or nested_food.get("name")
            or ""
        ).strip()
        food_id = ingredient.get("food_id") or nested_food.get("id")
        ingredient_grams = _pdf_number(ingredient.get("grams")) * factor
        if food_name and ingredient_grams > 0:
            expanded.append({
                "food_id": food_id,
                "food_name": food_name,
                "grams": ingredient_grams,
            })
    return expanded


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

        for component in _saved_item_food_components(diet, item):
            item_name = str(component.get("food_name") or "N/D").strip()
            normalized_name = item_name.casefold()
            if normalized_name not in shopping:
                shopping[normalized_name] = {"label": item_name, "grams": 0.0}
            shopping[normalized_name]["grams"] += _pdf_number(component.get("grams"))

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

    # Sezione ricette: sempre dopo la lista della spesa e prima delle giornate.
    recipes = sorted(
        list(diet.get("recipes") or []),
        key=lambda recipe: str(recipe.get("name") or "").casefold(),
    )
    if recipes:
        story.append(PageBreak())
        story.append(Paragraph("Ricette", h1_style))
        story.append(Paragraph(
            "Preparazioni utilizzabili nella distribuzione settimanale.",
            body_style,
        ))
        for recipe in recipes:
            recipe_name = str(recipe.get("name") or "Ricetta").strip()
            description = str(recipe.get("description") or "").strip()
            portions = max(1, int(recipe.get("portions") or 1))
            ingredients = list(recipe.get("ingredients") or [])
            total_grams = sum(_pdf_number(item.get("grams")) for item in ingredients)
            per_portion = total_grams / portions if portions else 0.0

            story.append(Paragraph(_pdf_text(recipe_name), h2_style))
            if description:
                story.append(Paragraph(_pdf_text(description), body_style))
            story.append(Paragraph(
                f"Porzioni: {portions} - Peso totale: {total_grams:.1f} g - "
                f"Peso indicativo per porzione: {per_portion:.1f} g",
                small_style,
            ))

            recipe_data = [[
                Paragraph("Ingrediente", table_header_style),
                Paragraph("Quantità", table_header_style),
            ]]
            for ingredient in ingredients:
                recipe_data.append([
                    Paragraph(_pdf_text(ingredient.get("food_name") or "N/D"), small_style),
                    f"{_pdf_number(ingredient.get('grams')):.1f} g",
                ])
            if len(recipe_data) == 1:
                recipe_data.append(["Nessun ingrediente", "-"])
            story.append(_styled_table(
                recipe_data,
                [122 * mm, 43 * mm],
                alignments={0: "LEFT", 1: "RIGHT"},
            ))
            story.append(Spacer(1, 4 * mm))

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
            Paragraph("Quantità", table_header_style),
            Paragraph("Kcal", table_header_style),
            Paragraph("Carb", table_header_style),
            Paragraph("Grassi", table_header_style),
            Paragraph("Prot", table_header_style),
        ]]
        if day_items:
            for item in day_items:
                detail_data.append([
                    Paragraph(_pdf_text(item.get("meal_type")), small_style),
                    Paragraph(_pdf_text(_saved_item_display_name(diet, item)), small_style),
                    _pdf_quantity_text(item),
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
    st.session_state.pop(f"diet_piece_snapshot_{diet_id}", None)
    if str(st.session_state.get("diet_loaded_plan_id")) != str(diet_id):
        return

    for key in list(st.session_state.keys()):
        key_str = str(key)
        if (
            key_str.startswith("diet_slot_")
            or key_str.startswith("ag_diet_slot_")
            or key_str.startswith("diet_weekly_budget")
            or key_str.startswith("ag_diet_weekly_budget")
            or key_str.startswith("diet_distribution_")
            or key_str.startswith("ag_diet_distribution")
            or key_str.startswith("diet_preparation")
            or key_str.startswith("diet_recipes")
        ):
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
        DISTRIBUTION_CSV_VISIBLE_KEY if "DISTRIBUTION_CSV_VISIBLE_KEY" in globals() else "show_distribution_csv_v1",
        DISTRIBUTION_CSV_TEXT_KEY if "DISTRIBUTION_CSV_TEXT_KEY" in globals() else "diet_distribution_csv_text_v1",
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
        # Arricchisce i piani con le ricette persistite, necessarie per visualizzare
        # correttamente le righe diet_meal_items che hanno recipe_id valorizzato.
        for _diet in patient_diets or []:
            try:
                _diet["recipes"] = get_recipes_for_diet(tec_conf, _diet.get("id"))
            except Exception as _recipe_exc:
                logger.warning(
                    "Impossibile caricare le ricette del piano %s: %s",
                    _diet.get("id"), _recipe_exc
                )
                _diet.setdefault("recipes", [])

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
            diet = _diet_with_transient_pieces(diet)
            with st.expander(f"📁 {diet['diet_name']} (ID: {diet['id']})"):
                action_download_col, action_shopping_col, action_micro_col, action_delete_col = st.columns(4)

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
                            micro_items = []
                            for saved_item in diet.get("items", []):
                                micro_items.extend(_saved_item_food_components(diet, saved_item))
                            st.session_state[micro_state_key] = calculate_diet_micronutrients_overview(
                                tec_conf,
                                micro_items,
                                days_in_plan=7,
                            )
                        except Exception as exc:
                            logger.error("Errore nel calcolo micronutrienti della dieta", exc_info=True)
                            st.error(f"Impossibile calcolare i micronutrienti: {exc}")
                with action_shopping_col:
                    if st.button(
                        "🛒 Lista Spesa",
                        key=f"toggle_shopping_{diet['id']}",
                        help="Mostra/nascondi la lista della spesa aggregata a schermo.",
                        use_container_width=True,
                    ):
                        state_key = f"view_shopping_{diet['id']}"
                        st.session_state[state_key] = not st.session_state.get(state_key, False)

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
                if st.session_state.get(f"view_shopping_{diet['id']}", False):
                    st.markdown("#### 🛒 Lista della Spesa Settimanale")
                    shopping_items = {}
                    # Usiamo la stessa affidabile logica presente nel generatore PDF
                    for item in diet.get("items", []):
                        for component in _saved_item_food_components(diet, item):
                            item_name = str(component.get("food_name") or "N/D").strip()
                            norm_name = item_name.casefold()
                            if norm_name not in shopping_items:
                                shopping_items[norm_name] = {"Alimento": item_name, "Quantità totale (g)": 0.0}
                            shopping_items[norm_name]["Quantità totale (g)"] += _pdf_number(component.get("grams"))
                    
                    if shopping_items:
                        df_shopping = pd.DataFrame(list(shopping_items.values()))
                        df_shopping = df_shopping.sort_values(by="Alimento")
                        # Arrotondamento per una lettura a schermo più pulita
                        df_shopping["Quantità totale (g)"] = df_shopping["Quantità totale (g)"].round(1)
                        st.dataframe(df_shopping, use_container_width=True, hide_index=True)
                    else:
                        st.info("Nessun alimento presente nel piano alimentare.")
                    st.markdown("---")
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
                            "Item": _saved_item_display_name(diet, item),
                            "Quantità": _pdf_quantity_text(item),
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


WEEKLY_BUDGET_GRID_KEY = "diet_weekly_budget_grid"
WEEKLY_BUDGET_SIG_KEY = "diet_weekly_budget_sig"
WEEKLY_BUDGET_ITEMS_KEY = "diet_weekly_budget_items"
WEEKLY_BUDGET_TOTALS_KEY = "diet_weekly_budget_totals"
# Snapshot delle grammature effettivamente consolidate nei giorni.
# Non viene aggiornata dai callback AG Grid: cambia solo durante import o pulsante Budget.
WEEKLY_BUDGET_ALLOCATED_KEY = "diet_weekly_budget_allocated_grams_v3"
WEEKLY_BUDGET_OCCURRENCES_KEY = "diet_weekly_budget_occurrences_v1"
WEEKLY_BUDGET_DIRTY_KEY = "diet_weekly_budget_dirty"
WEEKLY_BUDGET_GRAMS_COL = "Target settimanale (g)"


def _empty_weekly_budget(rows=4):
    """Draft generico degli alimenti previsti nell'intera settimana."""
    return pd.DataFrame({
        "option": [""] * rows,
        "__action_touch": [0] * rows,
        "__sync_request": [0] * rows,
        "Alimento": [None] * rows,
        WEEKLY_BUDGET_GRAMS_COL: [0.0] * rows,
    })


def _normalize_weekly_budget_df(data):
    """Mantiene nel session_state solo i campi editabili del budget settimanale."""
    df = pd.DataFrame(data).copy()

    if "option" not in df.columns:
        df["option"] = ""
    if "__action_touch" not in df.columns:
        df["__action_touch"] = 0
    if "__sync_request" not in df.columns:
        df["__sync_request"] = 0
    if "Alimento" not in df.columns:
        df["Alimento"] = None
    if WEEKLY_BUDGET_GRAMS_COL not in df.columns:
        df[WEEKLY_BUDGET_GRAMS_COL] = 0.0
    if "__row_id" not in df.columns:
        df["__row_id"] = [str(i) for i in range(len(df))]

    df = df[[
        "option", "__row_id", "__action_touch", "__sync_request",
        "Alimento", WEEKLY_BUDGET_GRAMS_COL
    ]]
    df["__row_id"] = df["__row_id"].astype(str)
    df[WEEKLY_BUDGET_GRAMS_COL] = pd.to_numeric(
        df[WEEKLY_BUDGET_GRAMS_COL], errors="coerce"
    ).fillna(0.0)
    return df


def _weekly_budget_signature(df):
    df = _normalize_weekly_budget_df(df)
    return tuple(
        (
            "" if pd.isna(food_name) else str(food_name).strip(),
            round(float(grams or 0.0), 4),
        )
        for food_name, grams in df[["Alimento", WEEKLY_BUDGET_GRAMS_COL]].itertuples(
            index=False, name=None
        )
    )


def _weekly_budget_basic_valid_rows(df):
    if df is None:
        return 0
    try:
        frame = _normalize_weekly_budget_df(df)
    except Exception:
        return 0
    count = 0
    for food_name, grams in frame[["Alimento", WEEKLY_BUDGET_GRAMS_COL]].itertuples(
        index=False, name=None
    ):
        name = "" if pd.isna(food_name) else str(food_name).strip()
        if name and _safe_float(grams) > 0:
            count += 1
    return count


def _process_weekly_budget(df, food_dict, food_js_db):
    """Consolida il budget e calcola i macro totali della settimana."""
    normalized = _normalize_weekly_budget_df(df)
    by_food = {}

    for food_name, grams in normalized[["Alimento", WEEKLY_BUDGET_GRAMS_COL]].itertuples(
        index=False, name=None
    ):
        if pd.isna(food_name):
            continue
        name = str(food_name).strip()
        grams_value = _safe_float(grams)
        if not name or grams_value <= 0 or name not in food_dict or name not in food_js_db:
            continue

        entry = by_food.setdefault(name, {
            "food_id": food_dict[name]["id"],
            "food_name": name,
            "grams": 0.0,
            "kcal": 0.0,
            "carbs": 0.0,
            "fats": 0.0,
            "prot": 0.0,
        })
        entry["grams"] += grams_value

    totals = _zero_totals()
    items = []
    for name, entry in by_food.items():
        nutrition = food_js_db[name]
        ratio = entry["grams"] / 100.0
        entry["kcal"] = round(nutrition["kcal"] * ratio, 1)
        entry["carbs"] = round(nutrition["carbs"] * ratio, 1)
        entry["fats"] = round(nutrition["fats"] * ratio, 1)
        entry["prot"] = round(nutrition["prot"] * ratio, 1)
        items.append(entry)
        for key in totals:
            totals[key] += entry[key]

    return items, totals


def _recalculate_weekly_budget(food_dict, food_js_db, trigger="unknown"):
    """Aggiornamento atomico del budget: draft -> source of truth in sessione."""
    raw_df = st.session_state.get(WEEKLY_BUDGET_GRID_KEY)
    if raw_df is None:
        raw_df = _empty_weekly_budget()

    normalized = _normalize_weekly_budget_df(raw_df)
    items, totals = _process_weekly_budget(normalized, food_dict, food_js_db)

    st.session_state[WEEKLY_BUDGET_GRID_KEY] = normalized
    st.session_state[WEEKLY_BUDGET_SIG_KEY] = _weekly_budget_signature(normalized)
    st.session_state[WEEKLY_BUDGET_ITEMS_KEY] = items
    st.session_state[WEEKLY_BUDGET_TOTALS_KEY] = totals
    st.session_state[WEEKLY_BUDGET_DIRTY_KEY] = False
    st.session_state.pop("diet_micronutrient_overview_editor", None)
    st.session_state.pop("diet_budget_micronutrient_overview", None)

    _diag_log(
        "recalculate_weekly_budget",
        trigger=trigger,
        valid_rows=len(items),
        totals=totals,
        draft=_debug_df_payload(normalized),
    )
    return items, totals


def _food_name_key(value):
    """Chiave stabile per confrontare lo stesso alimento tra DB, grid e sessione."""
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


# ------------------------------------------------------------------
# RICETTE / PASTI COMPOSTI - persistenti su recipes + recipe_ingredients
# ------------------------------------------------------------------
RECIPES_KEY = "diet_recipes_v2"
RECIPE_PREFIX = "🍳 "


def _recipe_label(name):
    name = str(name or "").strip()
    return f"{RECIPE_PREFIX}{name}" if name else ""


def _recipe_name_from_label(value):
    value = str(value or "").strip()
    if value.startswith(RECIPE_PREFIX):
        return value[len(RECIPE_PREFIX):].strip()
    return None


def _recipe_client_key(recipe):
    """Chiave stabile client-side usata dal service per mappare recipe create prima del diet_plan."""
    if not isinstance(recipe, dict):
        return None
    value = recipe.get("client_key") or recipe.get("id")
    return str(value) if value else None


def _normalize_recipe(recipe):
    if not isinstance(recipe, dict):
        return None
    name = str(recipe.get("name") or "").strip()
    if not name:
        return None

    recipe_id = recipe.get("id") or recipe.get("recipe_id")
    client_key = recipe.get("client_key") or recipe_id or str(uuid.uuid4())
    ingredients = []
    for item in recipe.get("ingredients", []) or []:
        if not isinstance(item, dict):
            continue
        nested_food = item.get("food") or item.get("foods") or {}
        if not isinstance(nested_food, dict):
            nested_food = {}
        food_id = item.get("food_id") or nested_food.get("id")
        food_name = str(
            item.get("food_name")
            or item.get("item_name")
            or nested_food.get("item_name")
            or nested_food.get("name")
            or ""
        ).strip()
        grams = _safe_float(item.get("grams"))
        if food_id and food_name and grams > 0:
            ingredients.append({
                "food_id": str(food_id),
                "food_name": food_name,
                "grams": grams,
            })
    if not ingredients:
        return None

    return {
        "id": str(recipe_id) if recipe_id else None,
        "client_key": str(client_key),
        "name": name,
        "description": str(recipe.get("description") or "").strip(),
        "portions": max(1, int(recipe.get("portions") or 1)),
        "ingredients": ingredients,
    }


def _set_recipes(recipes):
    normalized = {}
    for recipe in recipes or []:
        item = _normalize_recipe(recipe)
        if item is not None:
            normalized[item["name"]] = item
    st.session_state[RECIPES_KEY] = normalized
    return normalized


def _get_recipes():
    """Ricette correnti dell'editor, indicizzate per nome e comprensive degli ID DB."""
    raw = st.session_state.get(RECIPES_KEY, {}) or {}
    if not isinstance(raw, dict):
        return {}
    result = {}
    for _, recipe in raw.items():
        item = _normalize_recipe(recipe)
        if item is not None:
            result[item["name"]] = item
    return result


def _recipe_by_id(recipe_id):
    if not recipe_id:
        return None
    target = str(recipe_id)
    for recipe in _get_recipes().values():
        if recipe.get("id") and str(recipe.get("id")) == target:
            return recipe
    return None


def _recipe_by_client_key(client_key):
    if not client_key:
        return None
    target = str(client_key)
    for recipe in _get_recipes().values():
        if str(recipe.get("client_key")) == target:
            return recipe
    return None


def _recipe_total_grams(recipe):
    return sum(_safe_float(i.get("grams")) for i in (recipe or {}).get("ingredients", []))


def _recipe_profile(recipe, food_js_db):
    """Profilo nutrizionale per 100 g della ricetta, calcolato dagli ingredienti reali."""
    total_grams = _recipe_total_grams(recipe)
    totals = _zero_totals()
    if total_grams <= 0:
        return None

    for ingredient in recipe.get("ingredients", []):
        food_name = str(ingredient.get("food_name") or "").strip()
        grams = _safe_float(ingredient.get("grams"))
        nutrition = food_js_db.get(food_name)
        if not nutrition or grams <= 0:
            continue
        ratio = grams / 100.0
        totals["kcal"] += _safe_float(nutrition.get("kcal")) * ratio
        totals["carbs"] += _safe_float(nutrition.get("carbs")) * ratio
        totals["fats"] += _safe_float(nutrition.get("fats")) * ratio
        totals["prot"] += _safe_float(nutrition.get("prot")) * ratio

    return {
        "kcal": round(totals["kcal"] * 100.0 / total_grams, 4),
        "carbs": round(totals["carbs"] * 100.0 / total_grams, 4),
        "fats": round(totals["fats"] * 100.0 / total_grams, 4),
        "prot": round(totals["prot"] * 100.0 / total_grams, 4),
    }


def _recipe_food_db(food_js_db):
    """Catalogo virtuale per AG Grid: le ricette sono trattate come alimenti solo a fini visuali."""
    result = {}
    for name, recipe in _get_recipes().items():
        profile = _recipe_profile(recipe, food_js_db)
        if profile is not None:
            result[_recipe_label(name)] = profile
    return result


def _expand_distribution_choice(choice, grams):
    """Espande una riga della Distribuzione negli ingredienti reali, per Budget/coerenza."""
    choice = str(choice or "").strip()
    grams = _safe_float(grams)
    if not choice or grams <= 0:
        return []

    recipe_name = _recipe_name_from_label(choice)
    if recipe_name is None:
        return [{"food_name": choice, "grams": grams, "source": "food"}]

    recipe = _get_recipes().get(recipe_name)
    if not recipe:
        return []

    total_grams = _recipe_total_grams(recipe)
    if total_grams <= 0:
        return []

    factor = grams / total_grams
    expanded = []
    for ingredient in recipe.get("ingredients", []):
        food_name = str(ingredient.get("food_name") or "").strip()
        ingredient_grams = _safe_float(ingredient.get("grams")) * factor
        if food_name and ingredient_grams > 0:
            expanded.append({
                "food_id": ingredient.get("food_id"),
                "food_name": food_name,
                "grams": ingredient_grams,
                "source": "recipe",
                "recipe_id": recipe.get("id"),
                "recipe_client_key": recipe.get("client_key"),
                "recipe_name": recipe_name,
            })
    return expanded


def _diet_item_display_choice(item):
    """Nome mostrato nella Distribuzione per un meal item DB alimento/ricetta."""
    recipe_id = item.get("recipe_id")
    if recipe_id:
        recipe = _recipe_by_id(recipe_id)
        recipe_name = (
            (recipe or {}).get("name")
            or item.get("recipe_name")
            or item.get("name")
        )
        return _recipe_label(recipe_name) if recipe_name else ""
    return str(item.get("food_name") or item.get("item_name") or "").strip()


def _expand_persisted_diet_item(item):
    """Espande un meal item già persistito per ricostruire Budget/Assegnati."""
    grams = _safe_float(item.get("grams"))
    if grams <= 0:
        return []
    choice = _diet_item_display_choice(item)
    return _expand_distribution_choice(choice, grams)


def _recipe_is_used(recipe_name):
    target = _food_name_key(_recipe_label(recipe_name))
    raw = st.session_state.get(DISTRIBUTION_GRID_KEY)
    if raw is None:
        return False
    df = _normalize_distribution_df(raw)
    return any(_food_name_key(v) == target for v in df["Alimento"].fillna("").astype(str))


def _rename_recipe_in_distribution(old_name, new_name):
    raw = st.session_state.get(DISTRIBUTION_GRID_KEY)
    if raw is None:
        return
    df = _normalize_distribution_df(raw)
    old_label = _recipe_label(old_name)
    new_label = _recipe_label(new_name)
    mask = df["Alimento"].fillna("").astype(str).map(_food_name_key) == _food_name_key(old_label)
    if mask.any():
        df.loc[mask, "Alimento"] = new_label
        st.session_state[DISTRIBUTION_GRID_KEY] = _normalize_distribution_df(df)
        st.session_state[DISTRIBUTION_SIG_KEY] = _distribution_signature(df)
        st.session_state["diet_aggregations_dirty"] = True
        st.session_state["diet_budget_comparison_stale"] = True


def _recipes_payload_for_persistence():
    """Payload del service: recipe + ingredients. client_key mappa le ricette nuove ai meal item."""
    payload = []
    for recipe in _get_recipes().values():
        payload.append({
            "id": recipe.get("id"),
            "client_key": recipe.get("client_key"),
            "name": recipe.get("name"),
            "description": str(recipe.get("description") or "").strip(),
            "portions": int(recipe.get("portions") or 1),
            "ingredients": [
                {
                    "food_id": item.get("food_id"),
                    "food_name": item.get("food_name"),
                    "grams": round(_safe_float(item.get("grams")), 2),
                }
                for item in recipe.get("ingredients", [])
                if item.get("food_id") and _safe_float(item.get("grams")) > 0
            ],
        })
    return payload

# ------------------------------------------------------------------
# DISTRIBUZIONE SETTIMANALE CANONICA - singola grid filtrabile
# ------------------------------------------------------------------
DISTRIBUTION_GRID_KEY = "diet_distribution_grid_v1"
DISTRIBUTION_SIG_KEY = "diet_distribution_sig_v1"
DISTRIBUTION_ITEMS_KEY = "diet_distribution_items_v1"
DISTRIBUTION_DAILY_TOTALS_KEY = "diet_distribution_daily_totals_v1"
DISTRIBUTION_WEEKLY_TOTALS_KEY = "diet_distribution_weekly_totals_v1"


def _empty_distribution_df(rows=4):
    """DataFrame canonico della distribuzione: una riga = una allocazione.

    __item_id + Alimento costituiscono la coppia tecnica ID/nome. In AG Grid
    l'ID resta nascosto: l'utente vede e modifica solo il nome dell'item.
    Pezzi e esclusivamente un dato di rappresentazione e NON viene persistito.
    """
    return pd.DataFrame({
        "option": [""] * rows,
        "__row_id": [f"dist_{i}" for i in range(rows)],
        "__action_touch": [0] * rows,
        "__deleted": [0] * rows,
        "__item_id": [None] * rows,
        "__item_type": [None] * rows,
        "Giorno": [None] * rows,
        "Pasto": [None] * rows,
        "Alimento": [None] * rows,
        "Grammi (g)": [0.0] * rows,
        "Pezzi": [0] * rows,
    })


def _pieces_value(value) -> int:
    """Normalizza il numero pezzi. Zero significa: rappresenta l'item in grammi."""
    if value is None:
        return 0
    try:
        if pd.isna(value):
            return 0
    except Exception:
        pass
    raw = str(value).strip().replace(",", ".")
    if not raw:
        return 0
    try:
        numeric = float(raw)
    except Exception:
        return 0
    if numeric <= 0 or not numeric.is_integer():
        return 0
    return int(numeric)


def _normalize_distribution_df(data):
    """Normalizza il master DataFrame della Distribuzione settimanale."""
    df = pd.DataFrame(data).copy()
    defaults = {
        "option": "",
        "__action_touch": 0,
        "__deleted": 0,
        "__item_id": None,
        "__item_type": None,
        "Giorno": None,
        "Pasto": None,
        "Alimento": None,
        "Grammi (g)": 0.0,
        "Pezzi": 0,
    }
    for col, default in defaults.items():
        if col not in df.columns:
            df[col] = default
    if "__row_id" not in df.columns:
        df["__row_id"] = [f"dist_{i}" for i in range(len(df))]

    df = df[[
        "option", "__row_id", "__action_touch", "__deleted",
        "__item_id", "__item_type",
        "Giorno", "Pasto", "Alimento", "Grammi (g)", "Pezzi"
    ]]
    df["__row_id"] = df["__row_id"].astype(str)
    df["__deleted"] = pd.to_numeric(df["__deleted"], errors="coerce").fillna(0).astype(int)
    df["Grammi (g)"] = pd.to_numeric(df["Grammi (g)"], errors="coerce").fillna(0.0)
    df["Pezzi"] = df["Pezzi"].map(_pieces_value)
    df["__item_id"] = df["__item_id"].map(
        lambda value: None if value is None or str(value).strip() in {"", "nan", "None"} else str(value).strip()
    )
    df["__item_type"] = df["__item_type"].map(
        lambda value: None if value is None or str(value).strip() in {"", "nan", "None"} else str(value).strip().lower()
    )
    return df


def _sync_distribution_identity(data, food_dict):
    """Sincronizza la coppia tecnica (__item_id, Alimento) per food e recipe.

    L'utente vede solo Alimento. Ogni volta che la grid viene consolidata,
    l'ID nascosto viene ricalcolato dal catalogo/ricette correnti, evitando
    associazioni stale dopo la modifica del nome da frontend.
    """
    df = _normalize_distribution_df(data)
    recipes = _get_recipes()
    for idx, row in df.iterrows():
        choice = "" if pd.isna(row.get("Alimento")) else str(row.get("Alimento") or "").strip()
        if not choice:
            df.at[idx, "__item_id"] = None
            df.at[idx, "__item_type"] = None
            continue

        recipe_name = _recipe_name_from_label(choice)
        if recipe_name is not None:
            recipe = recipes.get(recipe_name)
            if recipe:
                df.at[idx, "__item_id"] = str(recipe.get("id") or recipe.get("client_key") or "") or None
                df.at[idx, "__item_type"] = "recipe"
            else:
                df.at[idx, "__item_id"] = None
                df.at[idx, "__item_type"] = "recipe"
            continue

        food = food_dict.get(choice)
        if food and food.get("id"):
            df.at[idx, "__item_id"] = str(food.get("id"))
            df.at[idx, "__item_type"] = "food"
        else:
            df.at[idx, "__item_id"] = None
            df.at[idx, "__item_type"] = "food"
    return _normalize_distribution_df(df)


def _sort_distribution_df(data):
    """Ordina per giorno Lunedì->Domenica e poi Colazione, Merenda, Pranzo, Spuntino, Cena."""
    df = _normalize_distribution_df(data)
    day_rank = {label: code for code, label in GIORNI_MAP.items()}
    df["__day_order"] = df["Giorno"].map(lambda x: day_rank.get(str(x).strip(), 99))
    df["__meal_order"] = df["Pasto"].map(lambda x: MEAL_ORDER.get(str(x).strip(), 99))
    df["__original_order"] = range(len(df))
    df = df.sort_values(
        by=["__day_order", "__meal_order", "__original_order"],
        kind="stable",
    ).drop(columns=["__day_order", "__meal_order", "__original_order"])
    return _normalize_distribution_df(df.reset_index(drop=True))


DISTRIBUTION_CSV_TEXT_KEY = "diet_distribution_csv_text_v1"
DISTRIBUTION_CSV_VISIBLE_KEY = "show_distribution_csv_v1"


def _distribution_csv_dataframe(data, food_dict):
    """Esporta il master in CSV esplicito ID + nome, mantenendo Pezzi solo come presentazione."""
    df = _sync_distribution_identity(data, food_dict)
    rows = []
    recipes = _get_recipes()
    for _, row in df.iterrows():
        choice = "" if pd.isna(row.get("Alimento")) else str(row.get("Alimento") or "").strip()
        if not choice and _safe_float(row.get("Grammi (g)")) <= 0:
            continue
        item_type = str(row.get("__item_type") or "").strip().lower()
        item_id = str(row.get("__item_id") or "").strip()
        item_name = choice
        if item_type == "recipe":
            recipe_name = _recipe_name_from_label(choice)
            recipe = recipes.get(recipe_name) if recipe_name else None
            item_name = str((recipe or {}).get("name") or recipe_name or choice).strip()
        rows.append({
            "Giorno": row.get("Giorno") or "",
            "Pasto": row.get("Pasto") or "",
            "Tipo": item_type,
            "Item ID": item_id,
            "Item Name": item_name,
            "Grammi (g)": round(_safe_float(row.get("Grammi (g)")), 2),
            "Pezzi": _pieces_value(row.get("Pezzi")),
        })
    return pd.DataFrame(rows, columns=[
        "Giorno", "Pasto", "Tipo", "Item ID", "Item Name", "Grammi (g)", "Pezzi"
    ])


def _distribution_to_csv_text(data, food_dict):
    return _distribution_csv_dataframe(data, food_dict).to_csv(
        index=False, sep=";", decimal=","
    )


def _parse_csv_number(value, field_name, row_number):
    raw = str(value or "").strip().replace(".", "").replace(",", ".")
    # Se il CSV e stato prodotto con decimal='.' non vogliamo rimuovere il punto
    # decimale. Ripristiniamo il caso semplice con un solo punto e nessuna virgola.
    original = str(value or "").strip()
    if "," not in original and original.count(".") <= 1:
        raw = original
    try:
        return float(raw)
    except Exception as exc:
        raise ValueError(f"Riga {row_number}: {field_name} non valido ({value!r}).") from exc


def _distribution_from_csv_text(text_value, food_dict, days, meals):
    """Importa la Distribuzione da CSV validando la tupla ID + Item Name."""
    text_value = str(text_value or "").strip()
    if not text_value:
        return _empty_distribution_df(rows=0)

    frame = pd.read_csv(StringIO(text_value), sep=";", dtype=str, keep_default_na=False)
    required = ["Giorno", "Pasto", "Tipo", "Item ID", "Item Name", "Grammi (g)"]
    missing = [col for col in required if col not in frame.columns]
    if missing:
        raise ValueError("CSV Distribuzione: colonne mancanti: " + ", ".join(missing))
    if "Pezzi" not in frame.columns:
        frame["Pezzi"] = ""

    food_by_id = {
        str(food.get("id")): name
        for name, food in food_dict.items()
        if food.get("id")
    }
    recipes = _get_recipes()
    recipe_by_id = {}
    for recipe in recipes.values():
        rid = recipe.get("id") or recipe.get("client_key")
        if rid:
            recipe_by_id[str(rid)] = recipe

    allowed_days = set(days)
    allowed_meals = set(meals)
    rows = []
    for idx, raw in frame.iterrows():
        row_number = idx + 2
        day = _resolve_distribution_day_label(raw.get("Giorno"))
        if day not in allowed_days:
            raise ValueError(f"Riga {row_number}: Giorno non valido: {raw.get('Giorno')!r}.")

        meal_raw = str(raw.get("Pasto") or "").strip()
        meal = next((m for m in meals if m.casefold() == meal_raw.casefold()), None)
        if meal not in allowed_meals:
            raise ValueError(f"Riga {row_number}: Pasto non valido: {meal_raw!r}.")

        item_type = str(raw.get("Tipo") or "").strip().lower()
        if item_type not in {"food", "recipe"}:
            raise ValueError(f"Riga {row_number}: Tipo deve essere 'food' oppure 'recipe'.")

        item_id = str(raw.get("Item ID") or "").strip()
        item_name = str(raw.get("Item Name") or "").strip()
        if not item_id or not item_name:
            raise ValueError(f"Riga {row_number}: Item ID e Item Name sono obbligatori.")

        if item_type == "food":
            canonical_name = food_by_id.get(item_id)
            if canonical_name is None:
                raise ValueError(f"Riga {row_number}: food_id {item_id} non presente nel catalogo corrente.")
            if _food_name_key(canonical_name) != _food_name_key(item_name):
                raise ValueError(
                    f"Riga {row_number}: la coppia food_id / Item Name non coincide: "
                    f"{item_id} corrisponde a '{canonical_name}', non a '{item_name}'."
                )
            display_choice = canonical_name
        else:
            recipe = recipe_by_id.get(item_id)
            if recipe is None:
                raise ValueError(f"Riga {row_number}: recipe_id {item_id} non appartiene al piano corrente.")
            canonical_name = str(recipe.get("name") or "").strip()
            if _food_name_key(canonical_name) != _food_name_key(item_name):
                raise ValueError(
                    f"Riga {row_number}: la coppia recipe_id / Item Name non coincide: "
                    f"{item_id} corrisponde a '{canonical_name}', non a '{item_name}'."
                )
            display_choice = _recipe_label(canonical_name)

        grams = _parse_csv_number(raw.get("Grammi (g)"), "Grammi (g)", row_number)
        if grams <= 0:
            raise ValueError(f"Riga {row_number}: Grammi (g) deve essere maggiore di zero.")

        pieces_raw = str(raw.get("Pezzi") or "").strip()
        pieces = 0
        if pieces_raw:
            pieces_num = _parse_csv_number(pieces_raw, "Pezzi", row_number)
            if pieces_num < 0 or not float(pieces_num).is_integer():
                raise ValueError(f"Riga {row_number}: Pezzi deve essere un intero maggiore o uguale a zero.")
            pieces = int(pieces_num)

        rows.append({
            "option": "",
            "__row_id": f"dist_csv_{uuid.uuid4()}",
            "__action_touch": 0,
            "__deleted": 0,
            "__item_id": item_id,
            "__item_type": item_type,
            "Giorno": day,
            "Pasto": meal,
            "Alimento": display_choice,
            "Grammi (g)": round(grams, 2),
            "Pezzi": pieces,
        })

    return _normalize_distribution_df(pd.DataFrame(rows))



def _distribution_piece_snapshot(data, food_dict, recipe_id_by_name=None):
    """Snapshot volatile dei pezzi, indicizzato come i meal item DB.

    Non viene mai persistito: serve solo a mantenere la rappresentazione in pezzi
    durante la sessione corrente (tabella di dettaglio/PDF).
    """
    df = _sync_distribution_identity(data, food_dict)
    day_code_map = {label: code for code, label in GIORNI_MAP.items()}
    snapshot = {}
    for _, row in df.iterrows():
        pieces = _pieces_value(row.get("Pezzi"))
        if pieces <= 0:
            continue
        day_code = day_code_map.get(str(row.get("Giorno") or "").strip())
        meal = str(row.get("Pasto") or "").strip()
        item_type = str(row.get("__item_type") or "").strip().lower()
        item_id = str(row.get("__item_id") or "").strip()
        if item_type == "recipe" and recipe_id_by_name:
            recipe_name = _recipe_name_from_label(row.get("Alimento"))
            mapped_id = recipe_id_by_name.get(str(recipe_name or "").casefold())
            if mapped_id:
                item_id = str(mapped_id)
        grams = _safe_float(row.get("Grammi (g)"))
        if day_code not in GIORNI_MAP or not meal or item_type not in {"food", "recipe"} or not item_id or grams <= 0:
            continue
        key = "|".join([
            str(day_code),
            meal,
            item_type,
            item_id,
            f"{grams:.2f}",
        ])
        snapshot.setdefault(key, []).append(pieces)
    return snapshot


def _resolve_distribution_day_label(value):
    """Tollera giorno DB come int/Decimal/stringa numerica oppure label italiana."""
    if value in GIORNI_MAP:
        return GIORNI_MAP.get(value)

    raw = "" if value is None else str(value).strip()
    if not raw:
        return None

    try:
        numeric = int(raw)
    except (TypeError, ValueError):
        numeric = None
    if numeric in GIORNI_MAP:
        return GIORNI_MAP[numeric]

    normalized = raw.casefold()
    for label in GIORNI_MAP.values():
        if label.casefold() == normalized:
            return label
    return None


def _distribution_df_from_diet_items(items):
    """Ricostruisce la Distribuzione preservando ID/nome e recipe_id."""
    rows = []
    for idx, item in enumerate(items or []):
        day_label = _resolve_distribution_day_label(item.get("giorno_settimana"))
        meal_label = str(item.get("meal_type") or "").strip()
        display_choice = _diet_item_display_choice(item)
        if not day_label or not meal_label or not display_choice:
            continue
        recipe_id = item.get("recipe_id")
        item_id = recipe_id or item.get("food_id")
        rows.append({
            "option": "",
            "__row_id": f"dist_import_{idx}",
            "__action_touch": 0,
            "__deleted": 0,
            "__item_id": str(item_id) if item_id else None,
            "__item_type": "recipe" if recipe_id else "food",
            "Giorno": day_label,
            "Pasto": meal_label,
            "Alimento": display_choice,
            "Grammi (g)": _safe_float(item.get("grams")),
            # Pezzi e solo rappresentazione UI e non viene ricostruito dal DB.
            "Pezzi": _pieces_value(item.get("pieces") or item.get("pezzi")),
        })

    if not rows:
        return _empty_distribution_df()
    return _normalize_distribution_df(pd.DataFrame(rows))


def _distribution_signature(data):
    df = _normalize_distribution_df(data)
    return tuple(
        (
            str(row_id),
            "" if pd.isna(day) else str(day).strip(),
            "" if pd.isna(meal) else str(meal).strip(),
            "" if pd.isna(food) else str(food).strip(),
            "" if item_id is None else str(item_id),
            "" if item_type is None else str(item_type),
            round(_safe_float(grams), 4),
            _pieces_value(pieces),
        )
        for row_id, day, meal, food, item_id, item_type, grams, pieces in df[
            ["__row_id", "Giorno", "Pasto", "Alimento", "__item_id", "__item_type", "Grammi (g)", "Pezzi"]
        ].itertuples(index=False, name=None)
    )


def _distribution_basic_valid_rows(data):
    try:
        df = _normalize_distribution_df(data)
    except Exception:
        return 0
    count = 0
    for day, meal, food, grams in df[["Giorno", "Pasto", "Alimento", "Grammi (g)"]].itertuples(index=False, name=None):
        day_text = "" if pd.isna(day) else str(day).strip()
        meal_text = "" if pd.isna(meal) else str(meal).strip()
        food_text = "" if pd.isna(food) else str(food).strip()
        if day_text and meal_text and food_text and _safe_float(grams) > 0:
            count += 1
    return count


def _distribution_aggregate_grams(data=None):
    """Somma le grammature reali consumate, espandendo le preparazioni nei loro ingredienti."""
    raw_df = data if data is not None else st.session_state.get(DISTRIBUTION_GRID_KEY)
    if raw_df is None:
        return {}
    df = _normalize_distribution_df(raw_df)
    by_key = {}
    for choice, grams in df[["Alimento", "Grammi (g)"]].itertuples(index=False, name=None):
        for item in _expand_distribution_choice(choice, grams):
            name = str(item.get("food_name") or "").strip()
            grams_value = _safe_float(item.get("grams"))
            key = _food_name_key(name)
            if not key or grams_value <= 0:
                continue
            entry = by_key.setdefault(key, {"label": name, "grams": 0.0})
            entry["grams"] += grams_value
    return {entry["label"]: entry["grams"] for entry in by_key.values()}


def _distribution_occurrences(data=None):
    """Conta le presenze per alimento reale; una ricetta vale una presenza per ingrediente."""
    raw_df = data if data is not None else st.session_state.get(DISTRIBUTION_GRID_KEY)
    if raw_df is None:
        return {}
    df = _normalize_distribution_df(raw_df)
    by_key = {}
    for choice, grams in df[["Alimento", "Grammi (g)"]].itertuples(index=False, name=None):
        row_foods = {}
        for item in _expand_distribution_choice(choice, grams):
            name = str(item.get("food_name") or "").strip()
            key = _food_name_key(name)
            if key:
                row_foods.setdefault(key, name)
        for key, name in row_foods.items():
            entry = by_key.setdefault(key, {"label": name, "count": 0})
            entry["count"] += 1
    return {entry["label"]: int(entry["count"]) for entry in by_key.values()}


def _process_distribution(data, food_dict, food_js_db, days, meals):
    """Converte la grid in meal item persistibili preservando la coppia ID/nome.

    Le ricette NON vengono esplose in diet_meal_items: l'espansione avviene solo
    per Budget/coerenza. Pezzi e un attributo transitorio di presentazione.
    """
    df = _normalize_distribution_df(data)
    day_code_map = {label: code for code, label in GIORNI_MAP.items()}
    allowed_days = set(days)
    allowed_meals = set(meals)
    items = []
    weekly = _zero_totals()
    daily = {day: _zero_totals() for day in days}

    for day, meal, choice, item_id, item_type, grams, pieces in df[[
        "Giorno", "Pasto", "Alimento", "__item_id", "__item_type", "Grammi (g)", "Pezzi"
    ]].itertuples(index=False, name=None):
        day = "" if pd.isna(day) else str(day).strip()
        meal = "" if pd.isna(meal) else str(meal).strip()
        choice = "" if pd.isna(choice) else str(choice).strip()
        item_id = "" if item_id is None else str(item_id).strip()
        item_type = "" if item_type is None else str(item_type).strip().lower()
        grams_value = _safe_float(grams)
        pieces_value = _pieces_value(pieces)
        if day not in allowed_days or meal not in allowed_meals or not choice or grams_value <= 0:
            continue

        recipe_name = _recipe_name_from_label(choice)
        is_recipe = item_type == "recipe" or recipe_name is not None
        if not is_recipe:
            if choice not in food_dict or choice not in food_js_db:
                continue
            catalog_id = str(food_dict[choice].get("id") or "")
            if not catalog_id:
                continue
            # L'ID nascosto deve rappresentare la stessa entita mostrata per nome.
            if item_id and item_id != catalog_id:
                continue
            nutrition = food_js_db[choice]
            ratio = grams_value / 100.0
            kcal = round(_safe_float(nutrition.get("kcal")) * ratio, 1)
            carbs = round(_safe_float(nutrition.get("carbs")) * ratio, 1)
            fats = round(_safe_float(nutrition.get("fats")) * ratio, 1)
            prot = round(_safe_float(nutrition.get("prot")) * ratio, 1)
            item = {
                "source_type": "food",
                "giorno_settimana": day_code_map[day],
                "giorno_label": day,
                "meal_type": meal,
                "food_id": catalog_id,
                "food_name": choice,
                "recipe_id": None,
                "recipe_ref": None,
                "grams": round(grams_value, 2),
                "pieces": pieces_value,
                "kcal": kcal,
                "carbs": carbs,
                "fats": fats,
                "prot": prot,
            }
        else:
            recipe = _recipe_by_id(item_id) if item_id else None
            if recipe is None and recipe_name is not None:
                recipe = _get_recipes().get(recipe_name)
            profile = _recipe_profile(recipe, food_js_db) if recipe else None
            if not recipe or profile is None:
                continue
            effective_name = str(recipe.get("name") or recipe_name or "").strip()
            if recipe_name and _food_name_key(effective_name) != _food_name_key(recipe_name):
                continue
            ratio = grams_value / 100.0
            kcal = round(_safe_float(profile.get("kcal")) * ratio, 1)
            carbs = round(_safe_float(profile.get("carbs")) * ratio, 1)
            fats = round(_safe_float(profile.get("fats")) * ratio, 1)
            prot = round(_safe_float(profile.get("prot")) * ratio, 1)
            item = {
                "source_type": "recipe",
                "giorno_settimana": day_code_map[day],
                "giorno_label": day,
                "meal_type": meal,
                "food_id": None,
                "food_name": None,
                "recipe_id": recipe.get("id"),
                "recipe_ref": recipe.get("client_key"),
                "recipe_name": effective_name,
                "grams": round(grams_value, 2),
                "pieces": pieces_value,
                "kcal": kcal,
                "carbs": carbs,
                "fats": fats,
                "prot": prot,
            }

        items.append(item)
        for key, value in (("kcal", kcal), ("carbs", carbs), ("fats", fats), ("prot", prot)):
            weekly[key] += value
            daily[day][key] += value

    return items, weekly, daily


def _recalculate_distribution(food_dict, food_js_db, days, meals, trigger="unknown"):

    """Consolida SOLO la Distribuzione; sincronizza ID/nome e applica l'ordinamento canonico."""
    raw_df = st.session_state.get(DISTRIBUTION_GRID_KEY, _empty_distribution_df())
    normalized = _sync_distribution_identity(raw_df, food_dict)
    normalized = _sort_distribution_df(normalized)
    items, weekly, daily = _process_distribution(normalized, food_dict, food_js_db, days, meals)
    st.session_state[DISTRIBUTION_GRID_KEY] = normalized
    st.session_state[DISTRIBUTION_SIG_KEY] = _distribution_signature(normalized)
    st.session_state[DISTRIBUTION_ITEMS_KEY] = items
    st.session_state[DISTRIBUTION_WEEKLY_TOTALS_KEY] = weekly
    st.session_state[DISTRIBUTION_DAILY_TOTALS_KEY] = daily
    st.session_state["diet_aggregations_dirty"] = False
    st.session_state["diet_last_consolidation_revision"] = int(
        st.session_state.get("diet_last_consolidation_revision", 0) or 0
    ) + 1
    _diag_log(
        "recalculate_distribution_single_grid",
        trigger=trigger,
        valid_rows=len(items),
        weekly=weekly,
    )
    return items, weekly, daily


def _extract_distribution_dataframe(grid_response):
    if grid_response is None:
        return None
    data = grid_response.get("data") if isinstance(grid_response, dict) else getattr(grid_response, "data", None)
    if data is None:
        return None
    try:
        return _normalize_distribution_df(data)
    except Exception as exc:
        logger.warning("Impossibile normalizzare la Distribuzione settimanale: %s", exc)
        return None


def _merge_distribution_view(master_df, edited_view_df, visible_ids):
    """Merge row-id based senza inferire cancellazioni da response parziali.

    AG Grid puo restituire temporaneamente una vista vuota durante mount/rerun.
    Per questo l'assenza di un row_id nella response NON equivale mai a delete.
    Una riga viene rimossa soltanto quando il renderer imposta __deleted = 1.
    """
    master = _normalize_distribution_df(master_df)
    edited = _normalize_distribution_df(edited_view_df)

    # Response vuota durante mount: conserva integralmente il master.
    if edited.empty:
        return master.reset_index(drop=True)

    editable_cols = [
        "option", "__row_id", "__action_touch", "__deleted",
        "__item_id", "__item_type",
        "Giorno", "Pasto", "Alimento", "Grammi (g)", "Pezzi"
    ]
    master_index = {
        str(row_id): idx
        for idx, row_id in zip(master.index, master["__row_id"].astype(str))
    }
    new_rows = []
    for row in edited[editable_cols].to_dict(orient="records"):
        row_id = str(row["__row_id"])
        if row_id in master_index:
            idx = master_index[row_id]
            for col in editable_cols:
                master.at[idx, col] = row[col]
        else:
            new_rows.append(row)

    if new_rows:
        master = pd.concat([master, pd.DataFrame(new_rows)], ignore_index=True)

    master = _normalize_distribution_df(master.reset_index(drop=True))
    # Delete esplicito: solo righe marcate dal pulsante X.
    master = master.loc[master["__deleted"] != 1].copy().reset_index(drop=True)
    return _normalize_distribution_df(master)


def _make_distribution_capture_callback(visible_ids):
    """Cattura la vista filtrata e la riunisce al master canonico per row_id."""
    visible_ids = tuple(str(x) for x in visible_ids)

    def _capture(grid_response):
        edited_view = _extract_distribution_dataframe(grid_response)
        if edited_view is None:
            return
        master_before = _normalize_distribution_df(
            st.session_state.get(DISTRIBUTION_GRID_KEY, _empty_distribution_df())
        )
        old_sig = _distribution_signature(master_before)
        merged = _merge_distribution_view(master_before, edited_view, visible_ids)
        new_sig = _distribution_signature(merged)
        st.session_state[DISTRIBUTION_GRID_KEY] = merged
        st.session_state[DISTRIBUTION_SIG_KEY] = new_sig
        if old_sig != new_sig:
            st.session_state["diet_aggregations_dirty"] = True
            st.session_state["diet_budget_comparison_stale"] = True
        st.session_state["diet_grid_rx_revision"] = int(
            st.session_state.get("diet_grid_rx_revision", 0) or 0
        ) + 1

    return _capture


def _append_distribution_row(day=None, meal=None, food=None):
    master = _normalize_distribution_df(
        st.session_state.get(DISTRIBUTION_GRID_KEY, _empty_distribution_df(rows=0))
    )
    import uuid
    row = {
        "option": "",
        "__row_id": f"dist_{uuid.uuid4()}",
        "__action_touch": 0,
        "__deleted": 0,
        "__item_id": None,
        "__item_type": None,
        "Giorno": day,
        "Pasto": meal,
        "Alimento": food,
        "Grammi (g)": 0.0,
        "Pezzi": 0,
    }
    master = pd.concat([master, pd.DataFrame([row])], ignore_index=True)
    st.session_state[DISTRIBUTION_GRID_KEY] = _normalize_distribution_df(master)
    st.session_state["diet_aggregations_dirty"] = True
    st.session_state["diet_budget_comparison_stale"] = True


def _apply_distribution_batch_edit(visible_ids, mode, value):
    master = _normalize_distribution_df(
        st.session_state.get(DISTRIBUTION_GRID_KEY, _empty_distribution_df())
    )
    ids = {str(x) for x in visible_ids}
    mask = master["__row_id"].astype(str).isin(ids)
    # Non tocchiamo righe vuote: una modifica massiva deve agire su allocazioni reali.
    mask &= master["Alimento"].fillna("").astype(str).str.strip().ne("")
    current = pd.to_numeric(master.loc[mask, "Grammi (g)"], errors="coerce").fillna(0.0)
    if mode == "Imposta grammi":
        updated = pd.Series([max(0.0, float(value))] * len(current), index=current.index)
    elif mode == "Aggiungi / sottrai grammi":
        updated = (current + float(value)).clip(lower=0.0)
    elif mode == "Variazione percentuale":
        updated = (current * (1.0 + float(value) / 100.0)).clip(lower=0.0)
    else:
        return 0
    master.loc[updated.index, "Grammi (g)"] = updated.round(2)
    st.session_state[DISTRIBUTION_GRID_KEY] = _normalize_distribution_df(master)
    st.session_state["diet_aggregations_dirty"] = True
    st.session_state["diet_budget_comparison_stale"] = True
    return int(len(updated))


def _aggregate_allocated_grams(days, meals):
    """Somma le grammature dagli item Python gia processati (compatibilita)."""
    allocated = {}
    for day in days:
        for meal in meals:
            for item in st.session_state.get(_slot_keys(day, meal)["items"], []):
                name = str(item.get("food_name") or "").strip()
                if not name:
                    continue
                allocated[name] = allocated.get(name, 0.0) + _safe_float(item.get("grams"))
    return allocated


def _aggregate_allocated_grams_from_drafts(days, meals):
    """Somma DIRETTAMENTE le grammature della Distribuzione corrente.

    Se e presente la nuova grid canonica usa quella; mantiene il fallback ai vecchi
    35 slot solo per compatibilita con sessioni aperte prima dell'evolutiva.
    """
    if DISTRIBUTION_GRID_KEY in st.session_state:
        return _distribution_aggregate_grams(st.session_state.get(DISTRIBUTION_GRID_KEY))

    """Somma DIRETTAMENTE le grammature presenti nei dataframe dei 35 slot.

    Questa funzione non dipende da ``_process_slot`` e quindi continua a funzionare
    anche quando un alimento di un piano storico non e piu presente nel catalogo,
    e il suo nome non viene piu riconosciuto da ``food_dict``. E' la sorgente
    corretta per il contatore Assegnati/Residui, che deve rappresentare cio che
    l'utente vede effettivamente nelle grid giornaliere.
    """
    by_key = {}

    for day in days:
        for meal in meals:
            raw_df = st.session_state.get(_slot_keys(day, meal)["grid"])
            if raw_df is None:
                continue

            try:
                slot_df = _normalize_slot_df(raw_df)
            except Exception:
                continue

            for food_name, grams in slot_df[["Alimento", "Grammi (g)"]].itertuples(
                index=False, name=None
            ):
                name = "" if pd.isna(food_name) else str(food_name).strip()
                grams_value = _safe_float(grams)
                if not name or grams_value <= 0:
                    continue

                key = _food_name_key(name)
                if not key:
                    continue

                if key not in by_key:
                    by_key[key] = {"label": name, "grams": 0.0}
                by_key[key]["grams"] += grams_value

    return {
        entry["label"]: entry["grams"]
        for entry in by_key.values()
    }


def _aggregate_allocated_occurrences_from_drafts(days, meals):
    """Conta quante volte ogni alimento compare nella Distribuzione corrente."""
    if DISTRIBUTION_GRID_KEY in st.session_state:
        return _distribution_occurrences(st.session_state.get(DISTRIBUTION_GRID_KEY))

    """Conta quante volte ogni alimento compare nei RAW della Distribuzione.

    Ogni riga con alimento valorizzato e grammatura > 0 vale una presenza.
    Il conteggio e indipendente dai macro e viene usato esclusivamente dal
    riepilogo del Budget alimentare.
    """
    by_key = {}

    for day in days:
        for meal in meals:
            raw_df = st.session_state.get(_slot_keys(day, meal)["grid"])
            if raw_df is None:
                continue

            try:
                slot_df = _normalize_slot_df(raw_df)
            except Exception:
                continue

            for food_name, grams in slot_df[["Alimento", "Grammi (g)"]].itertuples(
                index=False, name=None
            ):
                name = "" if pd.isna(food_name) else str(food_name).strip()
                grams_value = _safe_float(grams)
                if not name or grams_value <= 0:
                    continue

                key = _food_name_key(name)
                if not key:
                    continue

                if key not in by_key:
                    by_key[key] = {"label": name, "count": 0}
                by_key[key]["count"] += 1

    return {
        entry["label"]: int(entry["count"])
        for entry in by_key.values()
    }


def _aggregate_budget_targets_from_draft(data=None):
    """Aggrega i target del budget leggendo direttamente il draft della grid.

    E' volutamente indipendente dai macro consolidati: serve per i controlli di
    coerenza e deve riflettere esattamente cio che l'utente ha scritto.
    """
    raw_df = data if data is not None else st.session_state.get(WEEKLY_BUDGET_GRID_KEY)
    if raw_df is None:
        raw_df = _empty_weekly_budget()

    df = _normalize_weekly_budget_df(raw_df)
    by_key = {}
    for food_name, grams in df[["Alimento", WEEKLY_BUDGET_GRAMS_COL]].itertuples(
        index=False, name=None
    ):
        name = "" if pd.isna(food_name) else str(food_name).strip()
        grams_value = _safe_float(grams)
        if not name or grams_value <= 0:
            continue
        key = _food_name_key(name)
        if not key:
            continue
        if key not in by_key:
            by_key[key] = {"label": name, "grams": 0.0}
        by_key[key]["grams"] += grams_value

    return {entry["label"]: entry["grams"] for entry in by_key.values()}


def _budget_distribution_consistency(days, meals, budget_df=None, tolerance=0.05):
    """Confronta i RAW correnti di budget e distribuzione senza usare cache intermedie.

    Restituisce una tabella per alimento e un booleano di coerenza. Il salvataggio
    e consentito solo quando ogni alimento ha Budget == Assegnato (entro tolleranza)
    e non esistono alimenti assegnati fuori budget.
    """
    targets = _aggregate_budget_targets_from_draft(budget_df)
    allocated = _aggregate_allocated_grams_from_drafts(days, meals)
    occurrences = _aggregate_allocated_occurrences_from_drafts(days, meals)
    occurrences_by_key = {
        _food_name_key(name): int(count)
        for name, count in occurrences.items()
        if _food_name_key(name)
    }

    targets_by_key = {}
    labels = {}
    for name, grams in targets.items():
        key = _food_name_key(name)
        targets_by_key[key] = targets_by_key.get(key, 0.0) + _safe_float(grams)
        labels.setdefault(key, name)

    allocated_by_key = {}
    for name, grams in allocated.items():
        key = _food_name_key(name)
        allocated_by_key[key] = allocated_by_key.get(key, 0.0) + _safe_float(grams)
        labels.setdefault(key, name)

    rows = []
    coherent = True
    for key in sorted(set(targets_by_key) | set(allocated_by_key), key=lambda k: labels.get(k, k).casefold()):
        target = _safe_float(targets_by_key.get(key, 0.0))
        assigned = _safe_float(allocated_by_key.get(key, 0.0))
        delta = target - assigned

        if key not in targets_by_key and assigned > tolerance:
            status = "FUORI BUDGET"
            coherent = False
        elif abs(delta) <= tolerance:
            status = "COERENTE"
            delta = 0.0
        elif assigned < target:
            status = "ASSEGNATO < BUDGET"
            coherent = False
        else:
            status = "ASSEGNATO > BUDGET"
            coherent = False

        rows.append({
            "N. volte": int(occurrences_by_key.get(key, 0)),
            "Alimento": labels.get(key, key),
            "Budget (g)": round(target, 1),
            "Assegnati (g)": round(assigned, 1),
            "Differenza (g)": round(delta, 1),
            "Stato": status,
        })

    if not rows:
        coherent = False

    return pd.DataFrame(rows), coherent


def _weekly_budget_status_dataframe(days, meals):
    """Vista del budget basata SOLO sull'ultimo confronto esplicito del Budget.

    I draft giornalieri non aggiornano automaticamente ``Assegnati``: lo snapshot
    cambia soltanto all'import iniziale o premendo il pulsante dedicato al Budget.
    La Distribuzione settimanale ha quindi un ciclo di aggiornamento indipendente.
    """
    allocated = st.session_state.get(WEEKLY_BUDGET_ALLOCATED_KEY, {}) or {}
    occurrences = st.session_state.get(WEEKLY_BUDGET_OCCURRENCES_KEY, {}) or {}
    occurrences_by_key = {
        _food_name_key(name): int(count or 0)
        for name, count in dict(occurrences).items()
        if _food_name_key(name)
    }
    allocated = {
        str(name).strip(): _safe_float(grams)
        for name, grams in dict(allocated).items()
        if str(name).strip()
    }

    allocated_by_key = {}
    allocated_label_by_key = {}
    for allocated_name, grams in allocated.items():
        key = _food_name_key(allocated_name)
        if not key:
            continue
        allocated_by_key[key] = allocated_by_key.get(key, 0.0) + _safe_float(grams)
        allocated_label_by_key.setdefault(key, allocated_name)

    rows = []
    budget_keys = set()

    for item in st.session_state.get(WEEKLY_BUDGET_ITEMS_KEY, []):
        name = str(item.get("food_name") or "").strip()
        if not name:
            continue
        key = _food_name_key(name)
        budget_keys.add(key)
        target = _safe_float(item.get("grams"))
        assigned = _safe_float(allocated_by_key.get(key, 0.0))
        residual = target - assigned
        if abs(residual) <= 0.05:
            residual = 0.0
            status = "COERENTE"
        elif assigned < target:
            status = "ASSEGNATO < BUDGET"
        else:
            status = "ASSEGNATO > BUDGET"
        rows.append({
            "N. volte": int(occurrences_by_key.get(key, 0)),
            "Alimento": name,
            WEEKLY_BUDGET_GRAMS_COL: round(target, 1),
            "Assegnati (g)": round(assigned, 1),
            "Residui (g)": round(residual, 1),
            "Stato": status,
        })

    outside_budget = {
        allocated_label_by_key[key]: grams
        for key, grams in allocated_by_key.items()
        if key not in budget_keys and grams > 0
    }
    return pd.DataFrame(rows), outside_budget


def _weekly_budget_allowed_food_db(food_js_db):
    """Catalogo proposto nelle grid giornaliere: solo alimenti consolidati nel budget."""
    allowed_names = {
        str(item.get("food_name") or "").strip()
        for item in st.session_state.get(WEEKLY_BUDGET_ITEMS_KEY, [])
        if str(item.get("food_name") or "").strip()
    }
    return {
        name: food_js_db[name]
        for name in allowed_names
        if name in food_js_db
    }


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
    # - __sync_request resta per compatibilita tecnica con versioni precedenti.
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
        if (
            key_str.startswith("diet_slot_")
            or key_str.startswith("ag_diet_slot_")
            or key_str.startswith("diet_weekly_budget")
            or key_str.startswith("ag_diet_weekly_budget")
            or key_str.startswith("diet_distribution_")
            or key_str.startswith("ag_diet_distribution")
            or key_str.startswith("diet_preparation")
            or key_str.startswith("diet_recipes")
        ):
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
        "diet_budget_micronutrient_overview",
        "diet_budget_flash_message",
        "diet_distribution_batch_flash",
        "diet_distribution_filter_days",
        "diet_distribution_filter_meals",
        "diet_distribution_filter_food",
        "diet_distribution_filter_incoherent",
        "diet_distribution_flash_message",
        "diet_budget_last_check_rows",
        "diet_budget_last_check_coherent",
        "diet_budget_comparison_stale",
        "diet_weekly_budget_import_needs_recalc",
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

    # Carica dal DB le ricette persistite prima di ricostruire Budget e Distribuzione.
    persisted_recipes = diet.get("recipes")
    if persisted_recipes is None:
        try:
            persisted_recipes = get_recipes_for_diet(tec_conf, diet.get("id"))
        except Exception as exc:
            logger.error("Errore nel caricamento delle ricette del piano", exc_info=True)
            st.warning(f"Impossibile caricare le ricette del piano: {exc}")
            persisted_recipes = []
    _set_recipes(persisted_recipes or [])

    items_by_slot = {}
    for item in diet.get("items", []):
        day = GIORNI_MAP.get(item.get("giorno_settimana"))
        meal = item.get("meal_type")
        if not day or not meal:
            continue
        items_by_slot.setdefault((day, meal), []).append(item)

    # Il Budget non ha una tabella separata: viene ricostruito dalle allocazioni persistite.
    # Le righe recipe_id vengono prima esplose proporzionalmente nei relativi ingredienti.
    budget_by_food = {}
    budget_occurrences_by_food = {}
    for item in diet.get("items", []):
        row_foods = set()
        for expanded in _expand_persisted_diet_item(item):
            food_name = str(expanded.get("food_name") or "").strip()
            grams = _safe_float(expanded.get("grams"))
            if not food_name or grams <= 0:
                continue
            budget_by_food[food_name] = budget_by_food.get(food_name, 0.0) + grams
            row_foods.add(_food_name_key(food_name))
        for food_key in row_foods:
            label = next((n for n in budget_by_food if _food_name_key(n) == food_key), food_key)
            budget_occurrences_by_food[label] = budget_occurrences_by_food.get(label, 0) + 1

    budget_rows = max(4, len(budget_by_food))
    budget_df = _empty_weekly_budget(rows=budget_rows)
    budget_df["__row_id"] = [
        f"budget_import_{idx}"
        for idx in range(budget_rows)
    ]
    for idx, (food_name, grams) in enumerate(budget_by_food.items()):
        budget_df.at[idx, "Alimento"] = food_name
        budget_df.at[idx, WEEKLY_BUDGET_GRAMS_COL] = grams

    budget_df = _normalize_weekly_budget_df(budget_df)
    st.session_state[WEEKLY_BUDGET_GRID_KEY] = budget_df
    st.session_state[WEEKLY_BUDGET_SIG_KEY] = _weekly_budget_signature(budget_df)
    st.session_state[WEEKLY_BUDGET_ITEMS_KEY] = []
    st.session_state[WEEKLY_BUDGET_TOTALS_KEY] = _zero_totals()
    # All'apertura di un piano esistente le allocazioni salvate sono gia dati
    # consolidati: inizializziamo subito lo snapshot, senza attendere un rerun.
    st.session_state[WEEKLY_BUDGET_ALLOCATED_KEY] = dict(budget_by_food)
    st.session_state[WEEKLY_BUDGET_OCCURRENCES_KEY] = dict(budget_occurrences_by_food)
    st.session_state["diet_budget_comparison_stale"] = False
    st.session_state[WEEKLY_BUDGET_DIRTY_KEY] = True
    st.session_state["diet_weekly_budget_import_needs_recalc"] = True

    # Nuovo master canonico: tutte le allocazioni della settimana in un'unica grid.
    # La conversione e centralizzata e tollera anche giorni DB serializzati come stringhe.
    distribution_df = _distribution_df_from_diet_items(diet.get("items", []))
    st.session_state[DISTRIBUTION_GRID_KEY] = distribution_df
    st.session_state[DISTRIBUTION_SIG_KEY] = _distribution_signature(distribution_df)
    st.session_state[DISTRIBUTION_ITEMS_KEY] = []
    st.session_state[DISTRIBUTION_DAILY_TOTALS_KEY] = {
        day: _zero_totals() for day in GIORNI_MAP.values()
    }
    st.session_state[DISTRIBUTION_WEEKLY_TOTALS_KEY] = _zero_totals()
    st.session_state["diet_distribution_recovery_checked_v2"] = True

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
                slot_df.at[idx, "Alimento"] = _diet_item_display_choice(item)
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

    # Commit atomico dello snapshot delle grammature assegnate. I callback delle
    # singole grid modificano solo i draft; questo snapshot cambia esclusivamente
    # quando viene eseguito un consolidamento esplicito/import.
    # IMPORTANTE: Assegnati/Residui devono riflettere le grammature realmente
    # presenti nelle grid, non soltanto gli item riconosciuti dal catalogo.
    # Un piano storico puo contenere alimenti rinominati/rimossi dal catalogo:
    # in quel caso _process_slot li esclude dai macro, ma NON devono sparire dal
    # conteggio delle grammature assegnate.
    allocated_snapshot = _aggregate_allocated_grams_from_drafts(days, meals)
    st.session_state[WEEKLY_BUDGET_ALLOCATED_KEY] = dict(allocated_snapshot)

    _diag_log(
        "recalculate_all_slots_end",
        trigger=trigger,
        total_basic_rows=total_basic_rows,
        total_processed_items=total_processed,
        allocated_snapshot=allocated_snapshot,
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


def _extract_weekly_budget_dataframe(grid_response):
    if grid_response is None:
        return None
    if isinstance(grid_response, dict):
        data = grid_response.get("data")
    else:
        data = getattr(grid_response, "data", None)
    if data is None:
        return None
    try:
        return _normalize_weekly_budget_df(data)
    except Exception as exc:
        logger.warning("Impossibile normalizzare la grid budget settimanale: %s", exc)
        return None


def _make_weekly_budget_capture_callback():
    """Cattura solo il draft. I calcoli ufficiali partono dal pulsante Budget."""
    def _capture(grid_response):
        edited_df = _extract_weekly_budget_dataframe(grid_response)
        if edited_df is None:
            return

        old_df = st.session_state.get(WEEKLY_BUDGET_GRID_KEY)
        old_sig = _weekly_budget_signature(old_df) if old_df is not None else None
        new_sig = _weekly_budget_signature(edited_df)

        st.session_state[WEEKLY_BUDGET_GRID_KEY] = edited_df
        st.session_state[WEEKLY_BUDGET_SIG_KEY] = new_sig
        if old_sig != new_sig:
            # Il budget ha un proprio ciclo di consolidamento indipendente dalla
            # distribuzione settimanale. Una modifica qui NON sporca i macro dei giorni.
            st.session_state[WEEKLY_BUDGET_DIRTY_KEY] = True
            st.session_state["diet_budget_comparison_stale"] = True
            st.session_state.pop("diet_budget_micronutrient_overview", None)

        st.session_state["diet_grid_rx_revision"] = int(
            st.session_state.get("diet_grid_rx_revision", 0) or 0
        ) + 1

    return _capture


def _rerun_after_numeric_sync():
    """Rinfresca la UI dopo il commit numerico, preferendo il solo fragment."""
    try:
        st.rerun(scope="fragment")
    except Exception:
        # Fallback per versioni senza scope o per esecuzioni full-app del fragment.
        st.rerun()


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
            st.session_state["diet_budget_comparison_stale"] = True
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
                // Ctrl/Cmd+Enter chiude solo l'editing della cella.
                // I consolidamenti sono avviati esclusivamente dai pulsanti dedicati.
                params.stopEditing();
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
    # della grid; in questa versione il tasto non avvia piu alcun consolidamento.
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

    weekly_row_options_renderer = JsCode(r"""
class WeeklyRowOptionsRenderer {
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
            return button;
        };

        const del = makeButton('x', 'Rimuovi riga');
        const add = makeButton('+', 'Aggiungi riga sotto');
        const stop = (event) => { event.preventDefault(); event.stopPropagation(); };
        del.addEventListener('mousedown', stop);
        add.addEventListener('mousedown', stop);

        del.addEventListener('click', (event) => {
            stop(event);
            if (params.api.getDisplayedRowCount() <= 1) return;
            params.api.stopEditing();
            const oldIndex = params.node.rowIndex == null ? 0 : params.node.rowIndex;
            params.api.applyTransaction({ remove: [params.data] });
            const remaining = params.api.getDisplayedRowCount();
            const targetNode = params.api.getDisplayedRowAtIndex(Math.min(oldIndex, remaining - 1));
            if (targetNode) targetNode.setDataValue('__action_touch', Date.now());
        });

        add.addEventListener('click', (event) => {
            stop(event);
            params.api.stopEditing();
            const id = (typeof crypto !== 'undefined' && crypto.randomUUID)
                ? crypto.randomUUID()
                : `weekly_${Date.now()}_${Math.random().toString(36).slice(2)}`;
            const row = {
                option: '',
                __row_id: id,
                __action_touch: 0,
                __sync_request: 0,
                Alimento: null,
                'Target settimanale (g)': 0.0
            };
            const currentIndex = params.node.rowIndex == null
                ? params.api.getDisplayedRowCount() - 1
                : params.node.rowIndex;
            const tx = params.api.applyTransaction({ add: [row], addIndex: currentIndex + 1 });
            const addedNode = tx && tx.add && tx.add.length ? tx.add[0] : null;
            if (addedNode) addedNode.setDataValue('__action_touch', Date.now());
        });

        this.eGui.appendChild(del);
        this.eGui.appendChild(add);
        if (params.api.getDisplayedRowCount() <= 1) {
            del.disabled = true;
            del.style.opacity = '0.35';
        }
    }
    getGui() { return this.eGui; }
    refresh() { return false; }
}
""")

    distribution_row_options_renderer = JsCode(r"""
class DistributionRowOptionsRenderer {
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
            return button;
        };

        const del = makeButton('x', 'Rimuovi allocazione');
        const add = makeButton('+', 'Aggiungi allocazione sotto');
        const stop = (event) => { event.preventDefault(); event.stopPropagation(); };
        del.addEventListener('mousedown', stop);
        add.addEventListener('mousedown', stop);

        del.addEventListener('click', (event) => {
            stop(event);
            params.api.stopEditing();
            // Delete esplicito: non rimuoviamo subito la riga dal client.
            // Il marker viene sincronizzato a Python e solo allora il master la elimina.
            if (params.node) {
                params.node.setDataValue('__deleted', 1);
                params.node.setDataValue('__action_touch', Date.now());
            }
        });

        add.addEventListener('click', (event) => {
            stop(event);
            params.api.stopEditing();
            const context = params.context || {};
            const id = (typeof crypto !== 'undefined' && crypto.randomUUID)
                ? crypto.randomUUID()
                : `dist_${Date.now()}_${Math.random().toString(36).slice(2)}`;
            const row = {
                option: '',
                __row_id: id,
                __action_touch: 0,
                __deleted: 0,
                __item_id: null,
                __item_type: null,
                Giorno: context.defaultDay || 'Lunedì',
                Pasto: context.defaultMeal || 'Colazione',
                Alimento: context.defaultFood || null,
                'Grammi (g)': 0.0,
                Pezzi: 0
            };
            const currentIndex = params.node && params.node.rowIndex != null
                ? params.node.rowIndex
                : params.api.getDisplayedRowCount() - 1;
            const tx = params.api.applyTransaction({ add: [row], addIndex: currentIndex + 1 });
            const addedNode = tx && tx.add && tx.add.length ? tx.add[0] : null;
            if (addedNode) addedNode.setDataValue('__action_touch', Date.now());
        });

        this.eGui.appendChild(del);
        this.eGui.appendChild(add);
    }
    getGui() { return this.eGui; }
    refresh() { return false; }
}
""")

    day_options_list = list(GIORNI_MAP.values())
    pasti_options = ["Colazione", "Spuntino", "Pranzo", "Merenda", "Cena"]

    # Un piano importato ricostruisce prima il budget generico e poi le allocazioni.
    if st.session_state.pop("diet_weekly_budget_import_needs_recalc", False):
        _recalculate_weekly_budget(
            food_dict,
            food_js_db,
            trigger="import_existing_diet",
        )

    # La Distribuzione importata viene consolidata dal master canonico unico.
    if st.session_state.pop("diet_import_needs_recalc", False):
        _recalculate_distribution(
            food_dict,
            food_js_db,
            day_options_list,
            pasti_options,
            trigger="import_existing_diet",
        )

    # Migrazione one-shot per sessioni gia aperte quando viene caricata questa versione.
    # Inizializza la baseline degli assegnati una sola volta; da qui in avanti lo
    # snapshot cambia esclusivamente col pulsante dedicato al Budget.
    if WEEKLY_BUDGET_ALLOCATED_KEY not in st.session_state:
        if st.session_state.get("diet_loaded_plan_id") is not None:
            st.session_state[WEEKLY_BUDGET_ALLOCATED_KEY] = (
                _aggregate_allocated_grams_from_drafts(day_options_list, pasti_options)
            )
            if not st.session_state.get(WEEKLY_BUDGET_ITEMS_KEY):
                _recalculate_weekly_budget(
                    food_dict,
                    food_js_db,
                    trigger="session_migration_v3",
                )
        else:
            st.session_state[WEEKLY_BUDGET_ALLOCATED_KEY] = {}
        st.session_state["diet_budget_comparison_stale"] = False

    if WEEKLY_BUDGET_GRID_KEY not in st.session_state:
        initial_budget_df = _empty_weekly_budget()
        initial_budget_df["__row_id"] = [
            f"budget_{idx}" for idx in range(len(initial_budget_df))
        ]
        st.session_state[WEEKLY_BUDGET_GRID_KEY] = _normalize_weekly_budget_df(initial_budget_df)
        st.session_state[WEEKLY_BUDGET_SIG_KEY] = _weekly_budget_signature(initial_budget_df)
        st.session_state[WEEKLY_BUDGET_ITEMS_KEY] = []
        st.session_state[WEEKLY_BUDGET_TOTALS_KEY] = _zero_totals()
        st.session_state[WEEKLY_BUDGET_DIRTY_KEY] = False

    # ------------------------------------------------------------------
    # 4. Editor UX: Budget principale + singola Distribuzione filtrabile.
    # ------------------------------------------------------------------
    fragment_decorator = getattr(st, "fragment", lambda func: func)

    if DISTRIBUTION_GRID_KEY not in st.session_state:
        dist_df = _empty_distribution_df()
        st.session_state[DISTRIBUTION_GRID_KEY] = dist_df
        st.session_state[DISTRIBUTION_SIG_KEY] = _distribution_signature(dist_df)
        st.session_state[DISTRIBUTION_ITEMS_KEY] = []
        st.session_state[DISTRIBUTION_DAILY_TOTALS_KEY] = {
            day_name: _zero_totals() for day_name in day_options_list
        }
        st.session_state[DISTRIBUTION_WEEKLY_TOTALS_KEY] = _zero_totals()
        st.session_state["diet_aggregations_dirty"] = False

    # Recovery one-shot per sessione per la versione che poteva svuotare il master
    # durante il mount iniziale di AG Grid. Se il piano caricato contiene item ma il
    # master corrente non contiene allocazioni reali, lo ricostruiamo dal DB una volta.
    if not st.session_state.get("diet_distribution_recovery_checked_v2", False):
        st.session_state["diet_distribution_recovery_checked_v2"] = True
        loaded_plan_id = st.session_state.get("diet_loaded_plan_id")
        current_distribution = _normalize_distribution_df(
            st.session_state.get(DISTRIBUTION_GRID_KEY, _empty_distribution_df())
        )
        if loaded_plan_id is not None and _distribution_basic_valid_rows(current_distribution) == 0:
            loaded_diet = next(
                (
                    d for d in patient_diets
                    if str(d.get("id")) == str(loaded_plan_id)
                ),
                None,
            )
            if loaded_diet is not None:
                recovered_distribution = _distribution_df_from_diet_items(
                    loaded_diet.get("items", [])
                )
                if _distribution_basic_valid_rows(recovered_distribution) > 0:
                    st.session_state[DISTRIBUTION_GRID_KEY] = recovered_distribution
                    st.session_state[DISTRIBUTION_SIG_KEY] = _distribution_signature(
                        recovered_distribution
                    )
                    _recalculate_distribution(
                        food_dict,
                        food_js_db,
                        day_options_list,
                        pasti_options,
                        trigger="recover_empty_single_grid_v2",
                    )
                    st.session_state["diet_distribution_flash_message"] = (
                        "Distribuzione settimanale ricostruita automaticamente dal piano salvato."
                    )

    @fragment_decorator
    def _render_diet_editor():
        # ==============================================================
        # BUDGET ALIMENTARE - sezione principale
        # ==============================================================
        st.markdown("### Budget alimentare settimanale")
        st.caption(
            "Definisci gli alimenti e le quantità complessive della settimana. "
            "Budget e Distribuzione hanno cicli di aggiornamento separati."
        )

        budget_raw_df = _normalize_weekly_budget_df(
            st.session_state.get(WEEKLY_BUDGET_GRID_KEY, _empty_weekly_budget())
        )
        st.session_state[WEEKLY_BUDGET_GRID_KEY] = budget_raw_df

        _, outside_budget = _weekly_budget_status_dataframe(day_options_list, pasti_options)
        assigned_map = {}
        for allocated_name, allocated_grams in dict(
            st.session_state.get(WEEKLY_BUDGET_ALLOCATED_KEY, {}) or {}
        ).items():
            key = _food_name_key(allocated_name)
            if key:
                assigned_map[key] = assigned_map.get(key, 0.0) + _safe_float(allocated_grams)

        occurrence_map = {}
        for occurrence_name, occurrence_count in dict(
            st.session_state.get(WEEKLY_BUDGET_OCCURRENCES_KEY, {}) or {}
        ).items():
            key = _food_name_key(occurrence_name)
            if key:
                occurrence_map[key] = occurrence_map.get(key, 0) + int(occurrence_count or 0)

        budget_grid_df = budget_raw_df.copy()
        budget_grid_df.insert(0, "N. volte", [
            int(occurrence_map.get(_food_name_key(name), 0)) if not pd.isna(name) else 0
            for name in budget_grid_df["Alimento"]
        ])
        budget_grid_df["Assegnati (g)"] = [
            round(assigned_map.get(_food_name_key(name), 0.0), 1) if not pd.isna(name) else 0.0
            for name in budget_grid_df["Alimento"]
        ]
        budget_grid_df["Residui (g)"] = (
            pd.to_numeric(budget_grid_df[WEEKLY_BUDGET_GRAMS_COL], errors="coerce").fillna(0.0)
            - pd.to_numeric(budget_grid_df["Assegnati (g)"], errors="coerce").fillna(0.0)
        ).round(1)

        budget_gb = GridOptionsBuilder.from_dataframe(budget_grid_df)
        for hidden_col in ("::auto_unique_id::", "__row_id", "__action_touch", "__sync_request"):
            budget_gb.configure_column(hidden_col, hide=True, suppressColumnsToolPanel=True)
        budget_gb.configure_column(
            "N. volte", editable=False, pinned="left", type="numericColumn",
            width=88, minWidth=88, maxWidth=88, sortable=True
        )
        budget_gb.configure_column(
            "option", headerName="option", editable=False, sortable=False, filter=False,
            resizable=False, pinned="left", width=92, minWidth=92, maxWidth=92,
            suppressColumnsToolPanel=True, cellRenderer=weekly_row_options_renderer,
        )
        budget_gb.configure_column(
            "Alimento", editable=True, singleClickEdit=True,
            cellEditor=food_autocomplete_editor, flex=2.2,
        )
        budget_gb.configure_column(
            WEEKLY_BUDGET_GRAMS_COL, editable=True, type="numericColumn", flex=1.3
        )
        budget_gb.configure_column("Assegnati (g)", editable=False, type="numericColumn", flex=1.0)
        budget_gb.configure_column(
            "Residui (g)", editable=False, type="numericColumn", flex=1.0,
            cellStyle=JsCode("""
            function(params) {
                const v = Number(params.value || 0);
                if (v < 0) return {backgroundColor: '#FDE2E2', color: '#8B1E1E', fontWeight: '600'};
                if (v === 0) return {backgroundColor: '#EAF7EE', color: '#205C37'};
                return {backgroundColor: '#FFF7CC', color: '#6B5200'};
            }
            """),
        )
        budget_gb.configure_grid_options(
            domLayout="normal", editable=True,
            context={"foodDb": food_js_db, "foodVersion": food_catalog_version},
            getRowId=JsCode("function(params) { return String(params.data.__row_id); }"),
        )
        budget_kwargs = dict(
            gridOptions=budget_gb.build(),
            update_mode=GridUpdateMode.VALUE_CHANGED,
            allow_unsafe_jscode=True,
            fit_columns_on_grid_load=False,
            height=min(360, 42 + max(1, len(budget_grid_df)) * 35),
            theme="streamlit",
            key=f"ag_diet_weekly_budget_{int(st.session_state.get('diet_editor_revision', 0) or 0)}",
        )
        if _aggrid_supports_parameter("update_on"):
            budget_kwargs["update_on"] = []
        if DataReturnMode is not None:
            budget_kwargs["data_return_mode"] = DataReturnMode.AS_INPUT
        budget_callback_supported = _aggrid_supports_parameter("callback")
        if budget_callback_supported:
            budget_kwargs["callback"] = _make_weekly_budget_capture_callback()
        if _aggrid_supports_parameter("server_sync_strategy"):
            budget_kwargs["server_sync_strategy"] = "client_wins"

        budget_response = AgGrid(budget_grid_df, **budget_kwargs)

        # Vista CSV copiabile del Budget alimentare settimanale.
        # Espone soltanto le colonne utente, escludendo i campi tecnici della grid.
        budget_csv_df = budget_grid_df[[
            "N. volte",
            "Alimento",
            WEEKLY_BUDGET_GRAMS_COL,
            "Assegnati (g)",
            "Residui (g)",
        ]].copy()

        if st.button(
            "Mostra </>",
            key="toggle_weekly_budget_csv",
            help="Mostra o nasconde il Budget in formato CSV copiabile.",
        ):
            st.session_state["show_weekly_budget_csv"] = not st.session_state.get(
                "show_weekly_budget_csv", False
            )

        if st.session_state.get("show_weekly_budget_csv", False):
            st.code(
                budget_csv_df.to_csv(index=False, sep=";", decimal=","),
                language="text",
                wrap_lines=False,
            )

        if not budget_callback_supported:
            returned_budget_df = _extract_weekly_budget_dataframe(budget_response)
            if returned_budget_df is not None:
                old_sig = _weekly_budget_signature(st.session_state.get(WEEKLY_BUDGET_GRID_KEY, _empty_weekly_budget()))
                new_sig = _weekly_budget_signature(returned_budget_df)
                st.session_state[WEEKLY_BUDGET_GRID_KEY] = returned_budget_df
                st.session_state[WEEKLY_BUDGET_SIG_KEY] = new_sig
                if old_sig != new_sig:
                    st.session_state[WEEKLY_BUDGET_DIRTY_KEY] = True
                    st.session_state["diet_budget_comparison_stale"] = True
                    st.session_state.pop("diet_budget_micronutrient_overview", None)

        budget_totals = st.session_state.get(WEEKLY_BUDGET_TOTALS_KEY, _zero_totals())
        bm1, bm2, bm3, bm4 = st.columns(4)
        bm1.metric("Kcal medie / giorno", f"{budget_totals['kcal'] / 7:.1f}")
        bm2.metric("Carboidrati medi", f"{budget_totals['carbs'] / 7:.1f} g")
        bm3.metric("Grassi medi", f"{budget_totals['fats'] / 7:.1f} g")
        bm4.metric("Proteine medie", f"{budget_totals['prot'] / 7:.1f} g")

        budget_update_clicked = st.button(
            "🔄 Aggiorna i valori medi del Budget alimentare",
            key="update_weekly_budget_values",
            help=(
                "Consolida i valori del Budget e confronta Target, Assegnati e Residui "
                "leggendo la Distribuzione corrente. Non ricalcola i macro della Distribuzione."
            ),
            use_container_width=True,
        )

        if budget_update_clicked:
            _recalculate_weekly_budget(food_dict, food_js_db, trigger="budget_update_button")
            allocated_now = _distribution_aggregate_grams(st.session_state.get(DISTRIBUTION_GRID_KEY))
            occurrences_now = _distribution_occurrences(st.session_state.get(DISTRIBUTION_GRID_KEY))
            st.session_state[WEEKLY_BUDGET_ALLOCATED_KEY] = dict(allocated_now)
            st.session_state[WEEKLY_BUDGET_OCCURRENCES_KEY] = dict(occurrences_now)
            comparison_df, budget_coherent = _budget_distribution_consistency(
                day_options_list, pasti_options,
                budget_df=st.session_state.get(WEEKLY_BUDGET_GRID_KEY),
            )
            st.session_state["diet_budget_last_check_rows"] = comparison_df.to_dict(orient="records")
            st.session_state["diet_budget_last_check_coherent"] = bool(budget_coherent)
            st.session_state["diet_budget_comparison_stale"] = False
            if budget_coherent:
                st.session_state["diet_budget_flash_message"] = "Budget aggiornato: distribuzione coerente con tutti i target."
            else:
                counts = comparison_df["Stato"].value_counts().to_dict() if not comparison_df.empty else {}
                st.session_state["diet_budget_flash_message"] = (
                    "Budget aggiornato: "
                    f"{counts.get('ASSEGNATO < BUDGET', 0)} sotto target, "
                    f"{counts.get('ASSEGNATO > BUDGET', 0)} sopra target, "
                    f"{counts.get('FUORI BUDGET', 0)} fuori budget, "
                    f"{counts.get('COERENTE', 0)} coerenti."
                )
            _rerun_after_numeric_sync()

        budget_flash = st.session_state.pop("diet_budget_flash_message", None)
        if budget_flash:
            if st.session_state.get("diet_budget_last_check_coherent", False):
                st.success(budget_flash)
            else:
                st.warning(budget_flash)
        if st.session_state.get(WEEKLY_BUDGET_DIRTY_KEY, False):
            st.caption("⚠️ Budget modificato: aggiorna i valori medi per consolidare macro e confronto.")
        elif st.session_state.get("diet_budget_comparison_stale", False):
            st.caption("⚠️ La Distribuzione è cambiata dopo l'ultimo controllo del Budget: Assegnati/Residui sono lo snapshot precedente.")
        else:
            st.caption("✅ Budget consolidato e confronto con la Distribuzione aggiornato.")

        if outside_budget:
            st.warning(
                "Sono presenti allocazioni non incluse nell'ultimo Budget consolidato: "
                + ", ".join(sorted(outside_budget.keys()))
            )

        # Micronutrienti: ora appartengono al Budget, non alla Distribuzione.
        st.markdown("#### 🧬 Micronutrienti del Budget")
        st.caption(
            "Il calcolo usa direttamente gli alimenti e le grammature del Budget settimanale, "
            "normalizzati a media giornaliera su 7 giorni."
        )
        if st.button(
            "🧬 Calcola micronutrienti del Budget",
            key="calculate_budget_micronutrients",
            use_container_width=True,
        ):
            raw_budget = _normalize_weekly_budget_df(
                st.session_state.get(WEEKLY_BUDGET_GRID_KEY, _empty_weekly_budget())
            )
            micro_items, _ = _process_weekly_budget(raw_budget, food_dict, food_js_db)
            if not micro_items:
                st.session_state.pop("diet_budget_micronutrient_overview", None)
                st.error("Inserisci almeno un alimento valido con quantità maggiore di 0 nel Budget.")
            else:
                try:
                    st.session_state["diet_budget_micronutrient_overview"] = (
                        calculate_diet_micronutrients_overview(
                            tec_conf, micro_items, days_in_plan=7
                        )
                    )
                except Exception as exc:
                    logger.error("Errore nel calcolo micronutrienti del Budget", exc_info=True)
                    st.error(f"Impossibile calcolare i micronutrienti: {exc}")

        budget_micro_result = st.session_state.get("diet_budget_micronutrient_overview")
        if budget_micro_result is not None:
            budget_micro_df = _micronutrient_overview_dataframe(budget_micro_result)
            if not budget_micro_df.empty:
                _render_micronutrient_overview_table(
                    budget_micro_df, key="micro_overview_budget"
                )
                _render_micronutrient_reference_messages(budget_micro_result)
            missing_rda = budget_micro_result.get("missing_rda_names", [])
            if missing_rda:
                st.warning(
                    f"Riferimento non configurato per {len(missing_rda)} micronutrienti: "
                    "i relativi min/max sono mostrati come N/D."
                )

        st.markdown("---")

        # ==============================================================
        # RICETTE - aggregazioni persistenti di alimenti del Budget
        # ==============================================================
        st.markdown("### 🍳 Ricette")
        st.caption(
            "Raggruppa gli alimenti del Budget in ricette persistite su recipes / recipe_ingredients. "
            "Nella Distribuzione una ricetta resta una singola riga con recipe_id; per il controllo Budget viene esplosa nei suoi ingredienti."
        )
        budget_targets_for_prep = _aggregate_budget_targets_from_draft(
            st.session_state.get(WEEKLY_BUDGET_GRID_KEY)
        )
        available_prep_foods = sorted(budget_targets_for_prep.keys(), key=str.casefold)
        preparations = _get_recipes()
        prep_names = sorted(preparations.keys(), key=str.casefold)

        prep_choice = st.selectbox(
            "Ricetta da creare o modificare",
            options=["➕ Nuova ricetta"] + prep_names,
            key="diet_preparation_editor_choice",
        )
        editing_prep_name = None if prep_choice == "➕ Nuova ricetta" else prep_choice
        editing_prep = preparations.get(editing_prep_name, {}) if editing_prep_name else {}
        prep_key_suffix = re.sub(r"[^A-Za-z0-9]+", "_", editing_prep_name or "new").strip("_") or "new"

        with st.expander("Definizione ricetta", expanded=not bool(prep_names) or editing_prep_name is not None):
            pcol1, pcol2 = st.columns([2.2, 1])
            with pcol1:
                prep_name_value = st.text_input(
                    "Nome ricetta",
                    value=str(editing_prep.get("name") or ""),
                    placeholder="es. Pancake / Frullato banana",
                    key=f"diet_preparation_name_{prep_key_suffix}",
                )
            with pcol2:
                prep_portions = st.number_input(
                    "Porzioni teoriche",
                    min_value=1,
                    step=1,
                    value=max(1, int(editing_prep.get("portions") or 1)),
                    key=f"diet_preparation_portions_{prep_key_suffix}",
                )

            prep_description = st.text_area(
                "Descrizione ricetta",
                value=str(editing_prep.get("description") or ""),
                placeholder="es. Pancake proteici da preparare in padella antiaderente; dividere in 5 porzioni.",
                max_chars=500,
                height=80,
                key=f"diet_preparation_description_{prep_key_suffix}",
                help="Breve descrizione o indicazione di preparazione (max 500 caratteri).",
            )

            current_ingredients = {
                str(item.get("food_name") or "").strip(): _safe_float(item.get("grams"))
                for item in editing_prep.get("ingredients", []) or []
                if str(item.get("food_name") or "").strip()
            }
            default_ingredients = [name for name in current_ingredients if name in available_prep_foods]
            selected_prep_foods = st.multiselect(
                "Ingredienti dal Budget settimanale",
                options=available_prep_foods,
                default=default_ingredients,
                key=f"diet_preparation_ingredients_{prep_key_suffix}",
            )

            prep_ingredients = []
            if selected_prep_foods:
                st.caption("Indica la quantità del Budget utilizzata per l'intera preparazione.")
                for idx, food_name in enumerate(selected_prep_foods):
                    c1, c2, c3 = st.columns([2.3, 1.1, 1.2])
                    with c1:
                        st.write(food_name)
                    with c2:
                        target = _safe_float(budget_targets_for_prep.get(food_name))
                        st.caption(f"Budget: {target:.1f} g")
                    with c3:
                        default_grams = _safe_float(current_ingredients.get(food_name))
                        grams_value = st.number_input(
                            f"g {food_name}",
                            min_value=0.0,
                            step=5.0,
                            value=float(default_grams),
                            label_visibility="collapsed",
                            key=f"diet_preparation_grams_{prep_key_suffix}_{idx}_{_food_name_key(food_name)}",
                        )
                    if grams_value > 0:
                        prep_ingredients.append({"food_id": food_dict[food_name]["id"], "food_name": food_name, "grams": float(grams_value)})

            prep_total_grams = sum(_safe_float(i.get("grams")) for i in prep_ingredients)
            if prep_total_grams > 0:
                per_portion = prep_total_grams / max(1, int(prep_portions))
                st.info(
                    f"Peso teorico ricetta: **{prep_total_grams:.1f} g** · "
                    f"circa **{per_portion:.1f} g per porzione**."
                )

            recipe_db_plan_id = st.session_state.get("diet_loaded_plan_id")
            if recipe_db_plan_id is None:
                st.info(
                    "Le ricette vengono salvate direttamente a DB e richiedono un piano già esistente. "
                    "Salva prima il nuovo piano, quindi riaprilo per creare le ricette."
                )

            save_prep_col, delete_prep_col = st.columns(2)
            with save_prep_col:
                save_prep_clicked = st.button(
                    "💾 Salva ricetta a DB",
                    key=f"save_preparation_{prep_key_suffix}",
                    disabled=recipe_db_plan_id is None,
                    use_container_width=True,
                )
            with delete_prep_col:
                delete_prep_clicked = st.button(
                    "🗑️ Elimina ricetta da DB",
                    key=f"delete_preparation_{prep_key_suffix}",
                    disabled=(
                        editing_prep_name is None
                        or recipe_db_plan_id is None
                        or not editing_prep.get("id")
                    ),
                    use_container_width=True,
                )

            if save_prep_clicked:
                clean_prep_name = str(prep_name_value or "").strip()
                all_preps = _get_recipes()
                if not clean_prep_name:
                    st.error("Inserisci un nome per la ricetta.")
                elif not prep_ingredients:
                    st.error("Seleziona almeno un ingrediente con quantità maggiore di 0.")
                elif (
                    clean_prep_name != editing_prep_name
                    and clean_prep_name in all_preps
                ):
                    st.error(f"Esiste già una ricetta chiamata '{clean_prep_name}'.")
                else:
                    over_budget = [
                        item["food_name"]
                        for item in prep_ingredients
                        if _safe_float(item["grams"]) > _safe_float(
                            budget_targets_for_prep.get(item["food_name"])
                        ) + 0.05
                    ]
                    if over_budget:
                        st.error(
                            "Quantità della ricetta superiore al Budget per: "
                            + ", ".join(over_budget)
                        )
                    else:
                        recipe_payload = {
                            "id": editing_prep.get("id") if editing_prep_name else None,
                            "name": clean_prep_name,
                            "description": str(prep_description or "").strip(),
                            "portions": int(prep_portions),
                            "ingredients": prep_ingredients,
                        }
                        try:
                            save_recipe_for_diet(
                                tec_conf,
                                recipe_db_plan_id,
                                current_patient_id,
                                recipe_payload,
                            )
                            # recipe_id resta invariato in caso di rename: aggiorniamo
                            # soltanto la label mostrata nella Distribuzione corrente.
                            if editing_prep_name and editing_prep_name != clean_prep_name:
                                _rename_recipe_in_distribution(editing_prep_name, clean_prep_name)

                            _set_recipes(get_recipes_for_diet(tec_conf, recipe_db_plan_id))
                            st.session_state["diet_preparation_flash"] = (
                                f"Ricetta '{clean_prep_name}' salvata direttamente a DB."
                            )
                            st.session_state["diet_aggregations_dirty"] = True
                            st.session_state["diet_budget_comparison_stale"] = True
                            _rerun_after_numeric_sync()
                        except Exception as exc:
                            logger.error("Errore nel salvataggio diretto della ricetta", exc_info=True)
                            st.error(f"Impossibile salvare la ricetta: {exc}")

            if delete_prep_clicked and editing_prep_name:
                if _recipe_is_used(editing_prep_name):
                    st.error(
                        "La ricetta è usata nella Distribuzione corrente. "
                        "Rimuovi prima le relative allocazioni."
                    )
                else:
                    try:
                        delete_recipe_from_diet(
                            tec_conf,
                            recipe_db_plan_id,
                            current_patient_id,
                            editing_prep.get("id"),
                        )
                        _set_recipes(get_recipes_for_diet(tec_conf, recipe_db_plan_id))
                        st.session_state["diet_preparation_flash"] = (
                            f"Ricetta '{editing_prep_name}' eliminata dal DB."
                        )
                        _rerun_after_numeric_sync()
                    except Exception as exc:
                        logger.error("Errore nell'eliminazione diretta della ricetta", exc_info=True)
                        st.error(f"Impossibile eliminare la ricetta: {exc}")

        prep_flash = st.session_state.pop("diet_preparation_flash", None)
        if prep_flash:
            st.success(prep_flash)

        preparations = _get_recipes()
        if preparations:
            prep_summary_rows = []
            for prep_name, prep in sorted(preparations.items(), key=lambda x: x[0].casefold()):
                total_g = _recipe_total_grams(prep)
                profile = _recipe_profile(prep, food_js_db) or _zero_totals()
                prep_summary_rows.append({
                    "Ricetta": _recipe_label(prep_name),
                    "Descrizione": str(prep.get("description") or ""),
                    "Ingredienti": ", ".join(i["food_name"] for i in prep.get("ingredients", [])),
                    "Peso totale (g)": round(total_g, 1),
                    "Porzioni": int(prep.get("portions") or 1),
                    "g / porzione": round(total_g / max(1, int(prep.get("portions") or 1)), 1),
                    "Kcal / 100 g": round(_safe_float(profile.get("kcal")), 1),
                })
            st.dataframe(pd.DataFrame(prep_summary_rows), use_container_width=True, hide_index=True)
        else:
            st.caption("Nessuna ricetta definita. Puoi comunque usare direttamente gli alimenti nella Distribuzione.")

        st.markdown("---")

        # ==============================================================
        # DISTRIBUZIONE SETTIMANALE - unica grid con filtri
        # ==============================================================
        st.markdown("### Distribuzione settimanale")
        st.caption(
            "Tutte le allocazioni sono nella stessa tabella. Puoi usare un alimento singolo oppure una "
            "ricetta (🍳). Le ricette sono persistite tramite recipe_id e vengono esplose solo per Budget e controlli."
        )

        # --------------------------------------------------------------
        # Modalità CSV editabile: stesso master della grid, con ID + Item Name.
        # --------------------------------------------------------------
        csv_toggle_col, csv_help_col = st.columns([1, 4])
        with csv_toggle_col:
            if st.button(
                "Mostra </>",
                key="toggle_distribution_csv",
                help="Mostra/nasconde il CSV editabile della Distribuzione.",
            ):
                new_state = not st.session_state.get(DISTRIBUTION_CSV_VISIBLE_KEY, False)
                st.session_state[DISTRIBUTION_CSV_VISIBLE_KEY] = new_state
                if new_state:
                    st.session_state[DISTRIBUTION_CSV_TEXT_KEY] = _distribution_to_csv_text(
                        st.session_state.get(DISTRIBUTION_GRID_KEY, _empty_distribution_df()),
                        food_dict,
                    )
        with csv_help_col:
            st.caption(
                "CSV e frontend modificano lo stesso master. Nel CSV ogni item è identificato dalla coppia "
                "Item ID + Item Name; nella grid l'ID resta nascosto e viene mostrato solo il nome."
            )

        if st.session_state.get(DISTRIBUTION_CSV_VISIBLE_KEY, False):
            if DISTRIBUTION_CSV_TEXT_KEY not in st.session_state:
                st.session_state[DISTRIBUTION_CSV_TEXT_KEY] = _distribution_to_csv_text(
                    st.session_state.get(DISTRIBUTION_GRID_KEY, _empty_distribution_df()),
                    food_dict,
                )
            st.text_area(
                "CSV Distribuzione",
                key=DISTRIBUTION_CSV_TEXT_KEY,
                height=240,
                help=(
                    "Formato: Giorno;Pasto;Tipo;Item ID;Item Name;Grammi (g);Pezzi. "
                    "Tipo = food oppure recipe. Pezzi è opzionale e non viene persistito nel DB."
                ),
            )
            csv_apply_col, csv_reload_col = st.columns(2)
            with csv_apply_col:
                if st.button(
                    "⬆️ Applica CSV alla Distribuzione",
                    key="apply_distribution_csv",
                    use_container_width=True,
                ):
                    try:
                        imported_distribution = _distribution_from_csv_text(
                            st.session_state.get(DISTRIBUTION_CSV_TEXT_KEY, ""),
                            food_dict,
                            day_options_list,
                            pasti_options,
                        )
                        imported_distribution = _sort_distribution_df(imported_distribution)
                        st.session_state[DISTRIBUTION_GRID_KEY] = imported_distribution
                        st.session_state[DISTRIBUTION_SIG_KEY] = _distribution_signature(imported_distribution)
                        st.session_state["diet_aggregations_dirty"] = True
                        st.session_state["diet_budget_comparison_stale"] = True
                        st.session_state[DISTRIBUTION_CSV_TEXT_KEY] = _distribution_to_csv_text(
                            imported_distribution, food_dict
                        )
                        st.session_state["diet_distribution_flash_message"] = (
                            "Distribuzione caricata dal CSV. Aggiorna i valori medi per consolidare macro e ordinamento."
                        )
                        _rerun_after_numeric_sync()
                    except Exception as exc:
                        st.error(f"CSV non applicato: {exc}")
            with csv_reload_col:
                if st.button(
                    "↩️ Ricarica CSV dalla grid",
                    key="reload_distribution_csv",
                    use_container_width=True,
                ):
                    st.session_state[DISTRIBUTION_CSV_TEXT_KEY] = _distribution_to_csv_text(
                        st.session_state.get(DISTRIBUTION_GRID_KEY, _empty_distribution_df()),
                        food_dict,
                    )
                    _rerun_after_numeric_sync()

        distribution_flash = st.session_state.pop("diet_distribution_flash_message", None)
        if distribution_flash:
            st.success(distribution_flash)

        master_distribution = _normalize_distribution_df(
            st.session_state.get(DISTRIBUTION_GRID_KEY, _empty_distribution_df())
        )
        st.session_state[DISTRIBUTION_GRID_KEY] = master_distribution

        # Filtri combinabili. Selectbox alimento è searchable nativamente in Streamlit.
        f1, f2, f3, f4 = st.columns([1.4, 1.4, 1.8, 1.4])
        with f1:
            filter_days = st.multiselect(
                "Filtra giorno",
                options=day_options_list,
                key="diet_distribution_filter_days",
            )
        with f2:
            filter_meals = st.multiselect(
                "Filtra pasto",
                options=pasti_options,
                key="diet_distribution_filter_meals",
            )

        food_names = sorted({
            str(name).strip()
            for name in master_distribution["Alimento"].dropna().tolist()
            if str(name).strip()
        } | {
            str(item.get("food_name") or "").strip()
            for item in st.session_state.get(WEEKLY_BUDGET_ITEMS_KEY, [])
            if str(item.get("food_name") or "").strip()
        } | {
            _recipe_label(name) for name in _get_recipes()
        }, key=str.casefold)
        current_food_filter = st.session_state.get("diet_distribution_filter_food", "Tutti")
        if current_food_filter not in (["Tutti"] + food_names):
            food_names.append(current_food_filter)
            food_names = sorted(set(food_names), key=str.casefold)
        with f3:
            filter_food = st.selectbox(
                "Filtra alimento",
                options=["Tutti"] + food_names,
                key="diet_distribution_filter_food",
            )
        with f4:
            only_incoherent = st.checkbox(
                "Solo incoerenti",
                key="diet_distribution_filter_incoherent",
                help="Usa lo stato dell'ultimo controllo esplicito del Budget.",
            )

        filtered_distribution = master_distribution.copy()
        if filter_days:
            filtered_distribution = filtered_distribution.loc[
                filtered_distribution["Giorno"].isin(filter_days)
            ]
        if filter_meals:
            filtered_distribution = filtered_distribution.loc[
                filtered_distribution["Pasto"].isin(filter_meals)
            ]
        if filter_food != "Tutti":
            target_key = _food_name_key(filter_food)
            filtered_distribution = filtered_distribution.loc[
                filtered_distribution["Alimento"].map(_food_name_key) == target_key
            ]
        if only_incoherent:
            last_rows = st.session_state.get("diet_budget_last_check_rows", []) or []
            incoherent_keys = {
                _food_name_key(row.get("Alimento"))
                for row in last_rows
                if str(row.get("Stato") or "") != "COERENTE"
            }
            def _row_matches_incoherence(row):
                choice = str(row.get("Alimento") or "").strip()
                grams = _safe_float(row.get("Grammi (g)"))
                expanded_keys = {
                    _food_name_key(item.get("food_name"))
                    for item in _expand_distribution_choice(choice, grams)
                    if _food_name_key(item.get("food_name"))
                }
                return bool(expanded_keys & incoherent_keys)

            filtered_distribution = filtered_distribution.loc[
                filtered_distribution.apply(_row_matches_incoherence, axis=1)
            ]
            if st.session_state.get("diet_budget_comparison_stale", False):
                st.caption("⚠️ Il filtro 'Solo incoerenti' usa l'ultimo check Budget, che è precedente alle ultime modifiche della Distribuzione.")

        visible_ids = tuple(filtered_distribution["__row_id"].astype(str))
        st.caption(
            f"Visualizzate **{len(filtered_distribution)}** allocazioni su **{len(master_distribution)}** totali."
        )

        # I controlli sono posizionati visivamente prima della grid ma valutati
        # dopo la response AG Grid, così anche sulle versioni senza callback le
        # modifiche appena fatte vengono prima riportate nel master Python.
        distribution_controls_container = st.container()
        distribution_grid_container = st.container()

        allocation_food_js_db = _weekly_budget_allowed_food_db(food_js_db)
        # Le preparazioni sono voci virtuali: i macro per 100 g derivano dagli ingredienti.
        allocation_food_js_db.update(_recipe_food_db(food_js_db))
        # Per piani storici o budget appena modificati manteniamo visibili anche gli alimenti
        # già presenti nella Distribuzione, così possono essere corretti/rimossi.
        for existing_name in master_distribution["Alimento"].dropna().astype(str):
            if existing_name in food_js_db:
                allocation_food_js_db.setdefault(existing_name, food_js_db[existing_name])

        with distribution_grid_container:
            if filtered_distribution.empty:
                st.info("Nessuna allocazione corrisponde ai filtri correnti. Usa 'Aggiungi allocazione' oppure modifica i filtri.")
            else:
                distribution_view = filtered_distribution.copy()
                gb = GridOptionsBuilder.from_dataframe(distribution_view)
                for hidden_col in (
                    "::auto_unique_id::", "__row_id", "__action_touch", "__deleted",
                    "__item_id", "__item_type"
                ):
                    gb.configure_column(hidden_col, hide=True, suppressColumnsToolPanel=True)
                gb.configure_column(
                    "option", headerName="option", editable=False, sortable=False, filter=False,
                    resizable=False, pinned="left", width=92, minWidth=92, maxWidth=92,
                    suppressColumnsToolPanel=True, cellRenderer=distribution_row_options_renderer,
                )
                gb.configure_column(
                    "Giorno", editable=True, cellEditor="agSelectCellEditor",
                    cellEditorParams={"values": day_options_list}, minWidth=125, flex=1.1,
                )
                gb.configure_column(
                    "Pasto", editable=True, cellEditor="agSelectCellEditor",
                    cellEditorParams={"values": pasti_options}, minWidth=125, flex=1.1,
                )
                gb.configure_column(
                    "Alimento", headerName="Alimento / Ricetta", editable=True, singleClickEdit=True,
                    cellEditor=food_autocomplete_editor, minWidth=260, flex=2.3,
                    cellStyle=JsCode("""
                    function(params) {
                        const v = String(params.value || '');
                        if (v.startsWith('🍳 ')) {
                            return {fontWeight: '600', backgroundColor: '#FFF8E8'};
                        }
                        return null;
                    }
                    """),
                )
                gb.configure_column(
                    "Grammi (g)", editable=True, type="numericColumn", minWidth=110, flex=0.9
                )
                gb.configure_column(
                    "Pezzi", editable=True, type="numericColumn", minWidth=90, flex=0.7,
                    headerTooltip=(
                        "Solo rappresentazione: se maggiore di 0 viene mostrato il numero di pezzi; "
                        "la persistenza e i calcoli restano sempre basati sui grammi."
                    ),
                )
                gb.configure_column("Kcal", valueGetter=js_kcal, type="numericColumn", editable=False, flex=0.8)
                gb.configure_column("Carbs", valueGetter=js_carbs, type="numericColumn", editable=False, flex=0.8)
                gb.configure_column("Fats", valueGetter=js_fats, type="numericColumn", editable=False, flex=0.8)
                gb.configure_column("Prots", valueGetter=js_prot, type="numericColumn", editable=False, flex=0.8)

                default_day = filter_days[0] if len(filter_days) == 1 else day_options_list[0]
                default_meal = filter_meals[0] if len(filter_meals) == 1 else pasti_options[0]
                default_food = filter_food if filter_food != "Tutti" else None
                gb.configure_grid_options(
                    domLayout="normal",
                    editable=True,
                    context={
                        "foodDb": allocation_food_js_db,
                        "foodVersion": food_catalog_version,
                        "defaultDay": default_day,
                        "defaultMeal": default_meal,
                        "defaultFood": default_food,
                    },
                    getRowId=JsCode("function(params) { return String(params.data.__row_id); }"),
                    onCellValueChanged=js_refresh_macro_columns,
                )
                distribution_kwargs = dict(
                    gridOptions=gb.build(),
                    update_mode=GridUpdateMode.VALUE_CHANGED,
                    allow_unsafe_jscode=True,
                    fit_columns_on_grid_load=False,
                    height=min(620, 50 + max(1, len(distribution_view)) * 35),
                    theme="streamlit",
                    key=f"ag_diet_distribution_{int(st.session_state.get('diet_editor_revision', 0) or 0)}",
                )
                if _aggrid_supports_parameter("update_on"):
                    # cellValueChanged copre gli edit; rowDataUpdated rende affidabili anche
                    # add/delete eseguiti dal renderer della singola grid.
                    distribution_kwargs["update_on"] = ["cellValueChanged", "rowDataUpdated"]
                if DataReturnMode is not None:
                    distribution_kwargs["data_return_mode"] = DataReturnMode.AS_INPUT
                callback_supported = _aggrid_supports_parameter("callback")
                if callback_supported:
                    distribution_kwargs["callback"] = _make_distribution_capture_callback(visible_ids)
                if _aggrid_supports_parameter("server_sync_strategy"):
                    distribution_kwargs["server_sync_strategy"] = "client_wins"

                distribution_response = AgGrid(distribution_view, **distribution_kwargs)
                if not callback_supported:
                    returned_view = _extract_distribution_dataframe(distribution_response)
                    if returned_view is not None:
                        master_before = _normalize_distribution_df(
                            st.session_state.get(DISTRIBUTION_GRID_KEY, _empty_distribution_df())
                        )
                        old_sig = _distribution_signature(master_before)
                        merged = _merge_distribution_view(master_before, returned_view, visible_ids)
                        new_sig = _distribution_signature(merged)
                        st.session_state[DISTRIBUTION_GRID_KEY] = merged
                        st.session_state[DISTRIBUTION_SIG_KEY] = new_sig
                        if old_sig != new_sig:
                            st.session_state["diet_aggregations_dirty"] = True
                            st.session_state["diet_budget_comparison_stale"] = True

        with distribution_controls_container:
            add_col, batch_col = st.columns([1, 3])
            with add_col:
                if st.button("➕ Aggiungi allocazione", key="add_distribution_row", use_container_width=True):
                    default_day = filter_days[0] if len(filter_days) == 1 else day_options_list[0]
                    default_meal = filter_meals[0] if len(filter_meals) == 1 else pasti_options[0]
                    default_food = filter_food if filter_food != "Tutti" else None
                    _append_distribution_row(default_day, default_meal, default_food)
                    _rerun_after_numeric_sync()
            with batch_col:
                with st.expander("⚡ Modifica massiva dei risultati filtrati", expanded=False):
                    b1, b2, b3 = st.columns([1.5, 1, 1])
                    with b1:
                        batch_mode = st.selectbox(
                            "Operazione",
                            ["Imposta grammi", "Aggiungi / sottrai grammi", "Variazione percentuale"],
                            key="diet_distribution_batch_mode",
                        )
                    with b2:
                        batch_value = st.number_input(
                            "Valore",
                            value=0.0,
                            step=5.0,
                            key="diet_distribution_batch_value",
                        )
                    with b3:
                        st.write("")
                        st.write("")
                        if st.button(
                            "Applica ai filtrati",
                            key="apply_distribution_batch",
                            use_container_width=True,
                            disabled=len(visible_ids) == 0,
                        ):
                            changed = _apply_distribution_batch_edit(visible_ids, batch_mode, batch_value)
                            st.session_state["diet_distribution_batch_flash"] = (
                                f"Modifica massiva applicata a {changed} allocazioni."
                            )
                            _rerun_after_numeric_sync()

        batch_flash = st.session_state.pop("diet_distribution_batch_flash", None)
        if batch_flash:
            st.success(batch_flash)

        distribution_update_clicked = st.button(
            "🔄 Aggiorna valori medi della Distribuzione settimanale",
            key="update_weekly_distribution_values",
            help=(
                "Ricalcola esclusivamente i macro della Distribuzione e l'aggregazione per giorno. "
                "Non esegue controlli e non aggiorna Assegnati/Residui del Budget."
            ),
            use_container_width=True,
        )
        if distribution_update_clicked:
            _recalculate_distribution(
                food_dict, food_js_db, day_options_list, pasti_options,
                trigger="distribution_update_button",
            )
            st.session_state["diet_distribution_flash_message"] = (
                "Valori della Distribuzione aggiornati. Nessun controllo rispetto al Budget è stato eseguito."
            )
            _rerun_after_numeric_sync()

        distribution_flash = st.session_state.pop("diet_distribution_flash_message", None)
        if distribution_flash:
            st.success(distribution_flash)
        if st.session_state.get("diet_aggregations_dirty", False):
            st.caption("⚠️ Distribuzione modificata: aggiorna i valori medi per consolidare i macro per giorno.")
        else:
            st.caption("✅ Valori della Distribuzione allineati all'ultimo consolidamento.")

        # Unico riepilogo macro utile per la Distribuzione: dettaglio per giorno.
        daily_totals = st.session_state.get(
            DISTRIBUTION_DAILY_TOTALS_KEY,
            {day_name: _zero_totals() for day_name in day_options_list},
        )
        st.markdown("#### Riepilogo per giorno")
        daily_rows = []
        for day_name in day_options_list:
            t = daily_totals.get(day_name, _zero_totals())
            daily_rows.append({
                "Giorno": day_name,
                "Kcal": round(t["kcal"], 1),
                "Carb (g)": round(t["carbs"], 1),
                "Grassi (g)": round(t["fats"], 1),
                "Pro (g)": round(t["prot"], 1),
            })
        st.dataframe(pd.DataFrame(daily_rows), use_container_width=True, hide_index=True)

        st.markdown("---")

        # ==============================================================
        # PERSISTENZA - check indipendente e bloccante
        # ==============================================================
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
            normalized_name = (diet_name or "").strip()

            consistency_df, is_coherent = _budget_distribution_consistency(
                day_options_list,
                pasti_options,
                budget_df=st.session_state.get(WEEKLY_BUDGET_GRID_KEY),
            )
            raw_budget_targets = _aggregate_budget_targets_from_draft(
                st.session_state.get(WEEKLY_BUDGET_GRID_KEY)
            )
            raw_allocated = _distribution_aggregate_grams(
                st.session_state.get(DISTRIBUTION_GRID_KEY)
            )
            invalid_budget_foods = sorted({name for name in raw_budget_targets if name not in food_dict})
            invalid_distribution_foods = sorted({name for name in raw_allocated if name not in food_dict})
            # Una ricetta non è un alimento DB: è valida se esiste nell'editor e
            # tutti i suoi ingredienti, dopo l'espansione, appartengono al catalogo.
            raw_distribution_df = _normalize_distribution_df(
                st.session_state.get(DISTRIBUTION_GRID_KEY, _empty_distribution_df())
            )
            invalid_recipe_refs = sorted({
                str(choice).strip()
                for choice in raw_distribution_df["Alimento"].dropna().tolist()
                if str(choice).strip().startswith(RECIPE_PREFIX)
                and _recipe_name_from_label(choice) not in _get_recipes()
            })

            if not normalized_name:
                st.error("Inserisci un nome per il piano alimentare.")
            elif not raw_budget_targets:
                st.warning("Salvataggio bloccato: definisci almeno un alimento con grammatura positiva nel Budget alimentare.")
            elif not is_coherent:
                st.warning(
                    "⚠️ Salvataggio bloccato: Budget e Distribuzione non sono coerenti. "
                    "Per ogni alimento il Budget deve coincidere con la somma delle grammature realmente assegnate."
                )
                inconsistent_df = consistency_df.loc[consistency_df["Stato"] != "COERENTE"].copy()
                st.dataframe(
                    inconsistent_df if not inconsistent_df.empty else consistency_df,
                    use_container_width=True,
                    hide_index=True,
                )
            elif invalid_recipe_refs:
                st.warning(
                    "⚠️ Salvataggio bloccato: alcune ricette usate nella Distribuzione non esistono più: "
                    + ", ".join(invalid_recipe_refs)
                )
            elif invalid_budget_foods or invalid_distribution_foods:
                invalid_names = sorted(set(invalid_budget_foods) | set(invalid_distribution_foods))
                st.warning(
                    "⚠️ Salvataggio bloccato: alcuni alimenti non sono presenti nel catalogo corrente: "
                    + ", ".join(invalid_names)
                )
            else:
                weekly_budget_items, _ = _recalculate_weekly_budget(
                    food_dict, food_js_db, trigger=f"{action_name}_after_consistency_check"
                )
                temp_processed_items, _, _ = _recalculate_distribution(
                    food_dict, food_js_db, day_options_list, pasti_options,
                    trigger=f"{action_name}_after_consistency_check",
                )

                if not weekly_budget_items:
                    st.warning("Salvataggio bloccato: il Budget non contiene alimenti validi consolidabili.")
                elif not temp_processed_items:
                    st.warning("Salvataggio bloccato: la Distribuzione non contiene alimenti validi persistibili.")
                else:
                    st.session_state[WEEKLY_BUDGET_ALLOCATED_KEY] = dict(raw_allocated)
                    st.session_state[WEEKLY_BUDGET_OCCURRENCES_KEY] = dict(
                        _distribution_occurrences(st.session_state.get(DISTRIBUTION_GRID_KEY))
                    )
                    st.session_state["diet_budget_last_check_rows"] = consistency_df.to_dict(orient="records")
                    st.session_state["diet_budget_last_check_coherent"] = True
                    st.session_state["diet_budget_comparison_stale"] = False

                    diet_payload = {
                        "patient_id": current_patient_id,
                        "user_id": user_id,
                        "diet_name": normalized_name,
                        "descrizione": descrizione,
                        "warnings": warnings,
                    }
                    recipes_payload = _recipes_payload_for_persistence()
                    try:
                        if save_new_clicked:
                            if diet_name_exists(tec_conf, current_patient_id, normalized_name):
                                st.error(
                                    f"Esiste gia un piano alimentare chiamato '{normalized_name}' "
                                    "per questo assistito. Scegli un nome diverso."
                                )
                            else:
                                new_diet_id = add_diet_plan_with_recipes(
                                    tec_conf, diet_payload, temp_processed_items, recipes_payload
                                )
                                new_recipes = get_recipes_for_diet(tec_conf, new_diet_id)
                                new_recipe_ids_by_name = {
                                    str(recipe.get("name") or "").casefold(): recipe.get("id")
                                    for recipe in new_recipes
                                    if recipe.get("id")
                                }
                                st.session_state[f"diet_piece_snapshot_{new_diet_id}"] = _distribution_piece_snapshot(
                                    st.session_state.get(DISTRIBUTION_GRID_KEY, _empty_distribution_df()),
                                    food_dict,
                                    recipe_id_by_name=new_recipe_ids_by_name,
                                )
                                st.session_state["diet_flash_message"] = (
                                    f"Nuovo piano '{normalized_name}' salvato con successo (ID: {new_diet_id})."
                                )
                                _reset_diet_editor_state(clear_search=True)
                                st.session_state["diet_editor_patient_id"] = current_patient_id
                                st.rerun()
                        else:
                            loaded_id = st.session_state.get("diet_loaded_plan_id")
                            if loaded_id is None:
                                st.error("Importa prima un piano alimentare da aggiornare.")
                            elif diet_name_exists(
                                tec_conf, current_patient_id, normalized_name,
                                exclude_diet_id=loaded_id,
                            ):
                                st.error(
                                    f"Esiste gia un altro piano alimentare chiamato '{normalized_name}' "
                                    "per questo assistito."
                                )
                            else:
                                update_diet_plan_with_recipes(
                                    tec_conf, loaded_id, diet_payload, temp_processed_items, recipes_payload
                                )
                                st.session_state[f"diet_piece_snapshot_{loaded_id}"] = _distribution_piece_snapshot(
                                    st.session_state.get(DISTRIBUTION_GRID_KEY, _empty_distribution_df()),
                                    food_dict,
                                )
                                st.session_state["diet_loaded_plan_name"] = normalized_name
                                st.session_state["diet_flash_message"] = (
                                    f"Piano '{normalized_name}' aggiornato con successo."
                                )
                                st.rerun()
                    except Exception as exc:
                        logger.error("Errore durante la persistenza del piano alimentare", exc_info=True)
                        st.error(f"Errore durante il salvataggio del piano alimentare: {exc}")

        with st.expander("🧪 Diagnostica editor", expanded=False):
            master_now = _normalize_distribution_df(
                st.session_state.get(DISTRIBUTION_GRID_KEY, _empty_distribution_df())
            )
            st.caption(
                f"Righe Distribuzione: {len(master_now)} · "
                f"allocazioni complete: {_distribution_basic_valid_rows(master_now)} · "
                f"revisioni grid ricevute: {int(st.session_state.get('diet_grid_rx_revision', 0) or 0)}"
            )
            st.dataframe(master_now[["Giorno", "Pasto", "Alimento", "Grammi (g)"]], use_container_width=True, hide_index=True)

    _render_diet_editor()
