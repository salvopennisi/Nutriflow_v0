import json
import logging
import inspect
from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

import pandas as pd
import streamlit as st
from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode, JsCode
try:
    from st_aggrid import DataReturnMode
except ImportError:
    DataReturnMode = None

from Common.configuration import Configuration
from Backend.services.patient_service import get_all_patients
from Backend.services.workout_service import (
    ALLOWED_BLOCK_TYPES,
    ALLOWED_PROGRESSION_METRICS,
    ALLOWED_TECHNIQUES,
    add_workout_plan,
    add_workout_session_measurements,
    archive_workout_plan,
    get_exercise_progress,
    get_workout_measurements,
    get_workout_plan,
    get_workout_plans,
    restore_workout_plan,
    update_workout_measurements_batch,
    update_workout_plan,
    workout_name_exists,
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("WorkoutApp")

tec_conf = st.session_state.get("tec_conf", Configuration)

LIST_SECTION = "📋 Workout"
EDITOR_SECTION = "✏️ Editor workout"
PROGRESS_SECTION = "📈 Progressi"

BLOCK_TYPES = [
    "STANDARD",
    "SUPERSET",
    "JUMP_SET",
    "TRI_SET",
    "GIANT_SET",
    "CIRCUIT",
]
TECHNIQUES = [
    "STANDARD",
    "DROP_SET",
    "REST_PAUSE",
    "MYO_REPS",
    "CLUSTER",
    "AMRAP",
    "TEMPO",
    "PAUSE_REPS",
    "PARTIAL_REPS",
    "BACK_OFF",
]
PROGRESSION_METRICS = ["LOAD", "VOLUME", "TUT", "DENSITY", "REPS"]


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def _is_empty(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    try:
        return bool(pd.isna(value))
    except Exception:
        return False


def _to_int_or_none(value):
    if _is_empty(value):
        return None
    return int(float(value))


def _to_float_or_none(value):
    if _is_empty(value):
        return None
    return float(value)


def _decimal_to_float(value):
    if isinstance(value, Decimal):
        return float(value)
    return value


def _json_to_text(value) -> str:
    if not value:
        return "{}"
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _parse_json_object(value, exercise_name: str) -> dict:
    if _is_empty(value):
        return {}
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Parametri tecnica non validi per '{exercise_name}': JSON non valido ({exc.msg})."
        ) from exc
    if not isinstance(parsed, dict):
        raise ValueError(
            f"Parametri tecnica non validi per '{exercise_name}': deve essere un oggetto JSON."
        )
    return parsed


def _parse_progression_metrics(value, exercise_name: str) -> list[str]:
    if _is_empty(value):
        return ["LOAD"]
    if isinstance(value, (list, tuple, set)):
        raw_metrics = list(value)
    else:
        raw_metrics = str(value).replace(";", ",").split(",")

    metrics = []
    for raw in raw_metrics:
        metric = str(raw).strip().upper()
        if not metric:
            continue
        if metric not in ALLOWED_PROGRESSION_METRICS:
            raise ValueError(
                f"Metrica '{metric}' non valida per '{exercise_name}'. "
                f"Valori ammessi: {', '.join(PROGRESSION_METRICS)}."
            )
        if metric not in metrics:
            metrics.append(metric)

    if not metrics:
        raise ValueError(f"Seleziona almeno una metrica di progressione per '{exercise_name}'.")
    return metrics


def _block_label(index: int) -> str:
    # A..Z, poi B27, B28... per evitare dipendenze da utility esterne.
    if index < 26:
        return chr(ord("A") + index)
    return f"B{index + 1}"


WORKOUT_SESSION_COLUMN = "__session_local_id"

WORKOUT_EDITOR_COLUMNS = [
    "Azioni",
    "Ordine",
    "Esercizio",
    "Tipo blocco",
    "Blocco",
    "Ordine blocco",
    "Round blocco",
    "Serie",
    "Rep min",
    "Rep max",
    "Carico kg",
    "TUT sec",
    "Recupero sec",
    "RIR",
    "RPE",
    "Tecnica",
    "Parametri tecnica",
    "Progressione",
    "Note",
    WORKOUT_SESSION_COLUMN,
    "__id",
    "__row_id",
    "__action_touch",
]


def _new_exercise_row(order: int = 1, session_local_id: str | None = None) -> dict:
    """Crea una riga UI associata esplicitamente a una sessione.

    ``__session_local_id`` e la chiave tecnica che permette di mantenere TUTTI
    gli esercizi del workout in un solo DataFrame globale, pur mostrando nella
    grid soltanto le righe della sessione attiva.
    """
    return {
        "Azioni": "",
        "Ordine": order,
        "Esercizio": "",
        "Tipo blocco": "STANDARD",
        "Blocco": "",
        "Ordine blocco": None,
        "Round blocco": None,
        "Serie": None,
        "Rep min": None,
        "Rep max": None,
        "Carico kg": None,
        "TUT sec": None,
        "Recupero sec": None,
        "RIR": None,
        "RPE": None,
        "Tecnica": "STANDARD",
        "Parametri tecnica": "{}",
        "Progressione": "LOAD",
        "Note": "",
        WORKOUT_SESSION_COLUMN: session_local_id,
        "__id": None,
        "__row_id": str(uuid4()),
        "__action_touch": 0,
    }


def _empty_exercise_dataframe(session_local_id: str | None = None) -> pd.DataFrame:
    """DataFrame vuoto oppure placeholder di una specifica sessione."""
    if session_local_id is None:
        return pd.DataFrame(columns=WORKOUT_EDITOR_COLUMNS)
    return pd.DataFrame(
        [_new_exercise_row(1, session_local_id)],
        columns=WORKOUT_EDITOR_COLUMNS,
    )


def _normalize_exercise_editor_df(
    data,
    default_session_local_id: str | None = None,
) -> pd.DataFrame:
    """Normalizza il DataFrame globale senza inventare righe anonime.

    La colonna ``__session_local_id`` viene preservata per-riga. Se stiamo
    normalizzando la risposta della grid filtrata, ``default_session_local_id``
    permette di ripristinare l'appartenenza alla sessione anche con release di
    AG Grid che non restituiscono correttamente una colonna nascosta.
    """
    if data is None:
        return _empty_exercise_dataframe()

    frame = pd.DataFrame(data).copy()
    if frame.empty:
        return _empty_exercise_dataframe()

    defaults = _new_exercise_row(1, default_session_local_id)
    for column in WORKOUT_EDITOR_COLUMNS:
        if column not in frame.columns:
            if column == "__row_id":
                frame[column] = None
            elif column == WORKOUT_SESSION_COLUMN:
                frame[column] = default_session_local_id
            else:
                frame[column] = defaults.get(column)

    frame = frame[WORKOUT_EDITOR_COLUMNS].copy()

    if default_session_local_id is not None:
        frame[WORKOUT_SESSION_COLUMN] = frame[WORKOUT_SESSION_COLUMN].where(
            frame[WORKOUT_SESSION_COLUMN].notna(),
            default_session_local_id,
        )
        empty_session_mask = (
            frame[WORKOUT_SESSION_COLUMN].astype(str).str.strip().isin(["", "None", "nan"])
        )
        frame.loc[empty_session_mask, WORKOUT_SESSION_COLUMN] = default_session_local_id

    row_ids = []
    for _, row in frame.iterrows():
        row_id = row.get("__row_id")
        if _is_empty(row_id):
            persisted_id = row.get("__id")
            row_id = (
                f"exercise_{persisted_id}"
                if not _is_empty(persisted_id)
                else str(uuid4())
            )
        row_ids.append(str(row_id))
    frame["__row_id"] = row_ids

    frame["Azioni"] = ""
    frame["__action_touch"] = pd.to_numeric(
        frame["__action_touch"], errors="coerce"
    ).fillna(0)

    numeric_columns = [
        "Ordine", "Ordine blocco", "Round blocco", "Serie", "Rep min",
        "Rep max", "Carico kg", "TUT sec", "Recupero sec", "RIR", "RPE",
    ]
    for column in numeric_columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")

    text_defaults = {
        "Esercizio": "",
        "Tipo blocco": "STANDARD",
        "Blocco": "",
        "Tecnica": "STANDARD",
        "Parametri tecnica": "{}",
        "Progressione": "LOAD",
        "Note": "",
    }
    for column, default in text_defaults.items():
        frame[column] = frame[column].where(frame[column].notna(), default)

    return frame


def _session_has_real_exercises(data: pd.DataFrame) -> bool:
    frame = pd.DataFrame(data)
    if frame.empty or "Esercizio" not in frame.columns:
        return False
    return frame["Esercizio"].fillna("").astype(str).str.strip().ne("").any()


def _merge_session_grid_into_global(
    global_df: pd.DataFrame,
    session_local_id: str,
    returned_df: pd.DataFrame,
) -> pd.DataFrame:
    """Sostituisce nel DataFrame globale SOLO la vista della sessione corrente.

    Le righe delle altre sessioni non vengono mai toccate. Inoltre una response
    completamente vuota/stale non puo cancellare una sessione gia valorizzata.
    """
    global_df = _normalize_exercise_editor_df(global_df)
    returned_df = _normalize_exercise_editor_df(
        returned_df,
        default_session_local_id=session_local_id,
    )

    current_mask = (
        global_df[WORKOUT_SESSION_COLUMN].astype(str) == str(session_local_id)
        if not global_df.empty
        else pd.Series(dtype=bool)
    )
    current_df = global_df.loc[current_mask].copy() if not global_df.empty else _empty_exercise_dataframe()

    if returned_df.empty:
        return global_df

    returned_df[WORKOUT_SESSION_COLUMN] = str(session_local_id)

    # Protegge dai remount di AG Grid che possono restituire il placeholder
    # iniziale mentre il DataFrame globale contiene gia esercizi reali.
    if _session_has_real_exercises(current_df) and not _session_has_real_exercises(returned_df):
        return global_df

    other_df = (
        global_df.loc[~current_mask].copy()
        if not global_df.empty
        else _empty_exercise_dataframe()
    )
    merged = pd.concat([other_df, returned_df], ignore_index=True)

    # __row_id e univoco a livello client; in caso di duplicati teniamo la
    # versione appena restituita dalla grid.
    if "__row_id" in merged.columns:
        merged = merged.drop_duplicates(subset=["__row_id"], keep="last")

    return _normalize_exercise_editor_df(merged)


def _aggrid_supports_parameter(name: str) -> bool:
    try:
        return name in inspect.signature(AgGrid).parameters
    except (TypeError, ValueError):
        return False


def _extract_workout_grid_dataframe(response, session_local_id: str):
    if response is None:
        return None
    if isinstance(response, dict):
        data = response.get("data")
    else:
        data = getattr(response, "data", None)
    if data is None:
        return None
    try:
        return _normalize_exercise_editor_df(
            data,
            default_session_local_id=session_local_id,
        )
    except Exception:
        logger.warning("Impossibile normalizzare la response AG Grid workout", exc_info=True)
        return None


WORKOUT_ROW_OPTIONS_RENDERER = JsCode(r"""
class WorkoutRowOptionsRenderer {
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

        const deleteButton = makeButton('×', 'Rimuovi esercizio');
        const addButton = makeButton('+', 'Aggiungi esercizio sotto');

        const stopGridEvent = (event) => {
            event.preventDefault();
            event.stopPropagation();
        };
        deleteButton.addEventListener('mousedown', stopGridEvent);
        addButton.addEventListener('mousedown', stopGridEvent);

        deleteButton.addEventListener('click', (event) => {
            stopGridEvent(event);
            if (params.api.getDisplayedRowCount() <= 1) {
                return;
            }
            params.api.stopEditing();
            const oldIndex = params.node.rowIndex == null ? 0 : params.node.rowIndex;
            params.api.applyTransaction({ remove: [params.data] });
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

            let maxOrder = 0;
            params.api.forEachNode((node) => {
                const value = Number(node.data && node.data['Ordine']);
                if (Number.isFinite(value)) {
                    maxOrder = Math.max(maxOrder, value);
                }
            });

            const id = (typeof crypto !== 'undefined' && crypto.randomUUID)
                ? crypto.randomUUID()
                : `row_${Date.now()}_${Math.random().toString(36).slice(2)}`;

            const newRow = {
                'Azioni': '',
                'Ordine': maxOrder + 1,
                'Esercizio': '',
                'Tipo blocco': 'STANDARD',
                'Blocco': '',
                'Ordine blocco': null,
                'Round blocco': null,
                'Serie': null,
                'Rep min': null,
                'Rep max': null,
                'Carico kg': null,
                'TUT sec': null,
                'Recupero sec': null,
                'RIR': null,
                'RPE': null,
                'Tecnica': 'STANDARD',
                'Parametri tecnica': '{}',
                'Progressione': 'LOAD',
                'Note': '',
                '__id': null,
                '__row_id': id,
                '__action_touch': 0,
                '__session_local_id': params.data && params.data['__session_local_id']
                    ? params.data['__session_local_id']
                    : null
            };

            const currentIndex = params.node.rowIndex == null
                ? params.api.getDisplayedRowCount() - 1
                : params.node.rowIndex;
            const tx = params.api.applyTransaction({
                add: [newRow],
                addIndex: currentIndex + 1
            });
            const addedNode = tx && tx.add && tx.add.length ? tx.add[0] : null;
            if (addedNode) {
                addedNode.setDataValue('__action_touch', Date.now());
            }
        });

        this.eGui.appendChild(deleteButton);
        this.eGui.appendChild(addButton);

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


def _render_workout_exercise_grid(
    draft_key: str,
    session_local_id: str,
    token: str,
) -> pd.DataFrame:
    """Mostra la vista filtrata della sessione usando UN solo DataFrame globale."""
    global_df = _normalize_exercise_editor_df(st.session_state.get(draft_key))

    session_mask = (
        global_df[WORKOUT_SESSION_COLUMN].astype(str) == str(session_local_id)
        if not global_df.empty
        else pd.Series(dtype=bool)
    )
    grid_df = global_df.loc[session_mask].copy() if not global_df.empty else _empty_exercise_dataframe()

    # Ogni sessione mantiene almeno una riga placeholder nel DataFrame globale.
    if grid_df.empty:
        grid_df = _empty_exercise_dataframe(session_local_id)
        global_df = pd.concat([global_df, grid_df], ignore_index=True)
        global_df = _normalize_exercise_editor_df(global_df)
        st.session_state[draft_key] = global_df

    gb = GridOptionsBuilder.from_dataframe(grid_df)
    gb.configure_default_column(
        editable=True,
        sortable=False,
        filter=False,
        resizable=True,
    )

    for technical in (WORKOUT_SESSION_COLUMN, "__id", "__row_id", "__action_touch"):
        gb.configure_column(technical, hide=True, suppressColumnsToolPanel=True)

    gb.configure_column(
        "Azioni",
        headerName="",
        editable=False,
        pinned="left",
        width=88,
        minWidth=88,
        maxWidth=88,
        resizable=False,
        suppressColumnsToolPanel=True,
        cellRenderer=WORKOUT_ROW_OPTIONS_RENDERER,
    )
    gb.configure_column(
        "Ordine", editable=True, pinned="left", type="numericColumn",
        width=82, minWidth=82, maxWidth=90,
    )
    gb.configure_column(
        "Esercizio", editable=True, pinned="left", minWidth=220, width=260,
        singleClickEdit=True,
    )
    gb.configure_column(
        "Tipo blocco", editable=True, minWidth=145, width=155,
        cellEditor="agSelectCellEditor", cellEditorParams={"values": BLOCK_TYPES},
    )
    gb.configure_column("Blocco", editable=True, minWidth=100, width=110)
    gb.configure_column("Ordine blocco", editable=True, type="numericColumn", minWidth=120, width=125)
    gb.configure_column("Round blocco", editable=True, type="numericColumn", minWidth=115, width=120)
    gb.configure_column("Serie", editable=True, type="numericColumn", minWidth=80, width=85)
    gb.configure_column("Rep min", editable=True, type="numericColumn", minWidth=88, width=92)
    gb.configure_column("Rep max", editable=True, type="numericColumn", minWidth=88, width=92)
    gb.configure_column("Carico kg", editable=True, type="numericColumn", minWidth=105, width=110)
    gb.configure_column("TUT sec", editable=True, type="numericColumn", minWidth=95, width=100)
    gb.configure_column("Recupero sec", editable=True, type="numericColumn", minWidth=120, width=125)
    gb.configure_column("RIR", editable=True, type="numericColumn", minWidth=72, width=76)
    gb.configure_column("RPE", editable=True, type="numericColumn", minWidth=72, width=76)
    gb.configure_column(
        "Tecnica", editable=True, minWidth=140, width=150,
        cellEditor="agSelectCellEditor", cellEditorParams={"values": TECHNIQUES},
    )
    gb.configure_column("Parametri tecnica", editable=True, minWidth=230, width=260)
    gb.configure_column("Progressione", editable=True, minWidth=180, width=200)
    gb.configure_column("Note", editable=True, minWidth=260, width=320)

    gb.configure_grid_options(
        domLayout="normal",
        rowHeight=38,
        headerHeight=42,
        singleClickEdit=True,
        stopEditingWhenCellsLoseFocus=True,
        suppressColumnVirtualisation=False,
        getRowId=JsCode("function(params) { return String(params.data.__row_id); }"),
    )
    grid_options = gb.build()

    grid_height = min(610, max(230, 52 + len(grid_df) * 39))
    grid_revision = int(
        st.session_state.get(
            f"workout_grid_revision_{token}_{session_local_id}", 0
        ) or 0
    )

    kwargs = dict(
        gridOptions=grid_options,
        allow_unsafe_jscode=True,
        fit_columns_on_grid_load=False,
        height=grid_height,
        theme="streamlit",
        key=f"ag_workout_grid_{token}_{session_local_id}_{grid_revision}",
    )

    if _aggrid_supports_parameter("update_on"):
        kwargs["update_mode"] = GridUpdateMode.NO_UPDATE
        kwargs["update_on"] = ["cellValueChanged"]
    else:
        kwargs["update_mode"] = GridUpdateMode.VALUE_CHANGED

    if DataReturnMode is not None:
        kwargs["data_return_mode"] = DataReturnMode.AS_INPUT
    if _aggrid_supports_parameter("server_sync_strategy"):
        kwargs["server_sync_strategy"] = "client_wins"

    response = AgGrid(grid_df, **kwargs)
    returned_df = _extract_workout_grid_dataframe(response, session_local_id)

    if returned_df is not None:
        global_df = _merge_session_grid_into_global(
            st.session_state.get(draft_key, global_df),
            session_local_id,
            returned_df,
        )
        st.session_state[draft_key] = global_df

    current_global = _normalize_exercise_editor_df(st.session_state.get(draft_key, global_df))
    current_mask = current_global[WORKOUT_SESSION_COLUMN].astype(str) == str(session_local_id)
    return current_global.loc[current_mask].copy()


def _session_to_editor_dataframe(
    session: dict,
    session_local_id: str,
) -> tuple[pd.DataFrame, dict[str, str]]:
    """Converte una sessione persistita in righe del DataFrame globale."""
    block_ids = []
    for exercise in session.get("exercises", []):
        block_id = exercise.get("block_id")
        if block_id and str(block_id) not in block_ids:
            block_ids.append(str(block_id))

    id_to_label = {
        block_id: _block_label(index) for index, block_id in enumerate(block_ids)
    }
    label_to_id = {label: block_id for block_id, label in id_to_label.items()}

    rows = []
    for exercise in session.get("exercises", []):
        block_id = exercise.get("block_id")
        metrics = exercise.get("progression_metrics") or ["LOAD"]
        rows.append(
            {
                "Ordine": int(exercise.get("exercise_order") or 0),
                "Esercizio": exercise.get("exercise_name") or "",
                "Tipo blocco": exercise.get("block_type") or "STANDARD",
                "Blocco": id_to_label.get(str(block_id), "") if block_id else "",
                "Ordine blocco": _decimal_to_float(exercise.get("block_order")),
                "Round blocco": _decimal_to_float(exercise.get("block_rounds")),
                "Serie": _decimal_to_float(exercise.get("target_sets")),
                "Rep min": _decimal_to_float(exercise.get("target_reps_min")),
                "Rep max": _decimal_to_float(exercise.get("target_reps_max")),
                "Carico kg": _decimal_to_float(exercise.get("target_load_kg")),
                "TUT sec": _decimal_to_float(exercise.get("target_tut_seconds")),
                "Recupero sec": _decimal_to_float(exercise.get("target_rest_seconds")),
                "RIR": _decimal_to_float(exercise.get("target_rir")),
                "RPE": _decimal_to_float(exercise.get("target_rpe")),
                "Tecnica": exercise.get("technique") or "STANDARD",
                "Parametri tecnica": _json_to_text(exercise.get("technique_params")),
                "Progressione": ",".join(str(m) for m in metrics),
                "Note": exercise.get("notes") or "",
                WORKOUT_SESSION_COLUMN: session_local_id,
                "__id": str(exercise.get("id")) if exercise.get("id") else None,
            }
        )

    if not rows:
        return _empty_exercise_dataframe(session_local_id), label_to_id
    return _normalize_exercise_editor_df(
        pd.DataFrame(rows),
        default_session_local_id=session_local_id,
    ), label_to_id


SESSION_META_COLUMNS = [
    "__session_local_id",
    "__id",
    "session_name",
    "description",
    "notes",
]


def _normalize_session_meta_df(data) -> pd.DataFrame:
    """Normalizza la source of truth dei metadati delle sessioni.

    Una riga = una sessione. I widget testuali sono solo una vista temporanea
    della riga selezionata e non sono piu usati come storage persistente UI.
    """
    if data is None:
        return pd.DataFrame(columns=SESSION_META_COLUMNS)

    frame = pd.DataFrame(data).copy()
    for column in SESSION_META_COLUMNS:
        if column not in frame.columns:
            frame[column] = None

    frame = frame[SESSION_META_COLUMNS].copy()
    for column in ("__session_local_id", "session_name", "description", "notes"):
        frame[column] = frame[column].where(frame[column].notna(), "")
        frame[column] = frame[column].astype(str)

    # __id deve restare None per le sessioni nuove.
    frame["__id"] = frame["__id"].where(frame["__id"].notna(), None)
    return frame.drop_duplicates(subset=["__session_local_id"], keep="last").reset_index(drop=True)


def _session_meta_row(meta: dict) -> dict:
    return {
        "__session_local_id": str(meta["local_id"]),
        "__id": meta.get("id"),
        "session_name": str(meta.get("session_name") or ""),
        "description": str(meta.get("description") or ""),
        "notes": str(meta.get("notes") or ""),
    }


def _global_editor_keys(token: str) -> dict[str, str]:
    return {
        "draft": f"workout_exercise_draft_{token}",
        "block_maps": f"workout_block_maps_{token}",
        "session_meta": f"workout_session_meta_{token}",
    }


def _get_session_meta(token: str, session_local_id: str) -> dict:
    global_keys = _global_editor_keys(token)
    frame = _normalize_session_meta_df(st.session_state.get(global_keys["session_meta"]))
    mask = frame["__session_local_id"].astype(str) == str(session_local_id)
    if not mask.any():
        return {
            "__session_local_id": str(session_local_id),
            "__id": None,
            "session_name": "",
            "description": "",
            "notes": "",
        }
    return frame.loc[mask].iloc[0].to_dict()


def _update_session_meta(
    token: str,
    session_local_id: str,
    *,
    session_name: str,
    description: str,
    notes: str,
) -> None:
    """Aggiorna SOLO la riga della sessione selezionata nel DataFrame metadati."""
    global_keys = _global_editor_keys(token)
    frame = _normalize_session_meta_df(st.session_state.get(global_keys["session_meta"]))
    mask = frame["__session_local_id"].astype(str) == str(session_local_id)

    values = {
        "session_name": str(session_name or ""),
        "description": str(description or ""),
        "notes": str(notes or ""),
    }

    if mask.any():
        idx = frame.index[mask][0]
        for column, value in values.items():
            frame.at[idx, column] = value
    else:
        new_row = {
            "__session_local_id": str(session_local_id),
            "__id": None,
            **values,
        }
        frame = pd.concat([frame, pd.DataFrame([new_row])], ignore_index=True)

    st.session_state[global_keys["session_meta"]] = _normalize_session_meta_df(frame)


def _capture_session_meta_from_widget_state(token: str, session_local_id: str) -> None:
    """Persisti nel DataFrame meta gli ultimi valori dei widget ancora in session_state.

    Streamlit puo eliminare lo stato dei widget condizionali quando non vengono
    piu renderizzati. Questa funzione viene chiamata PRIMA del cambio sessione,
    usando l'ultima sessione renderizzata come riferimento.
    """
    name_key = f"workout_session_name_widget_{token}_{session_local_id}"
    description_key = f"workout_session_description_widget_{token}_{session_local_id}"
    notes_key = f"workout_session_notes_widget_{token}_{session_local_id}"

    if not any(key in st.session_state for key in (name_key, description_key, notes_key)):
        return

    current = _get_session_meta(token, session_local_id)
    _update_session_meta(
        token,
        session_local_id,
        session_name=st.session_state.get(name_key, current.get("session_name", "")),
        description=st.session_state.get(
            description_key, current.get("description", "")
        ),
        notes=st.session_state.get(notes_key, current.get("notes", "")),
    )


def _append_session_meta(token: str, meta: dict) -> None:
    global_keys = _global_editor_keys(token)
    frame = _normalize_session_meta_df(st.session_state.get(global_keys["session_meta"]))
    frame = pd.concat([frame, pd.DataFrame([_session_meta_row(meta)])], ignore_index=True)
    st.session_state[global_keys["session_meta"]] = _normalize_session_meta_df(frame)


def _remove_session_meta(token: str, session_local_id: str) -> None:
    global_keys = _global_editor_keys(token)
    frame = _normalize_session_meta_df(st.session_state.get(global_keys["session_meta"]))
    if not frame.empty:
        frame = frame.loc[
            frame["__session_local_id"].astype(str) != str(session_local_id)
        ].copy()
    st.session_state[global_keys["session_meta"]] = _normalize_session_meta_df(frame)


def _new_session_meta(order: int, persisted_session: dict | None = None) -> dict:
    persisted_session = persisted_session or {}
    persisted_id = persisted_session.get("id")
    local_id = f"persisted_{persisted_id}" if persisted_id else f"new_{uuid4()}"
    return {
        "local_id": local_id,
        "id": str(persisted_id) if persisted_id else None,
        "session_name": persisted_session.get("session_name") or f"Sessione {order}",
        "description": persisted_session.get("description") or "",
        "notes": persisted_session.get("notes") or "",
    }


def _initialize_sessions_editor(token: str, plan: dict | None) -> list[dict]:
    sessions_key = f"workout_sessions_{token}"
    global_keys = _global_editor_keys(token)

    if (
        sessions_key in st.session_state
        and global_keys["draft"] in st.session_state
        and global_keys["session_meta"] in st.session_state
    ):
        metas = st.session_state[sessions_key]
        if global_keys["block_maps"] not in st.session_state:
            st.session_state[global_keys["block_maps"]] = {
                meta["local_id"]: {} for meta in metas
            }
        return metas

    persisted_sessions = (plan or {}).get("sessions") or []
    metas = []
    frames = []
    block_maps = {}
    meta_rows = []

    if persisted_sessions:
        for index, session in enumerate(persisted_sessions, start=1):
            meta = _new_session_meta(index, session)
            metas.append(meta)
            meta_rows.append(_session_meta_row(meta))
            frame, block_map = _session_to_editor_dataframe(
                session,
                meta["local_id"],
            )
            frames.append(frame)
            block_maps[meta["local_id"]] = block_map
    else:
        meta = _new_session_meta(1)
        metas = [meta]
        meta_rows = [_session_meta_row(meta)]
        frames = [_empty_exercise_dataframe(meta["local_id"])]
        block_maps[meta["local_id"]] = {}

    global_df = (
        pd.concat(frames, ignore_index=True)
        if frames
        else _empty_exercise_dataframe()
    )
    global_df = _normalize_exercise_editor_df(global_df)

    st.session_state[sessions_key] = metas
    st.session_state[global_keys["draft"]] = global_df
    st.session_state[global_keys["block_maps"]] = block_maps
    st.session_state[global_keys["session_meta"]] = _normalize_session_meta_df(meta_rows)
    return metas


def _append_new_session_to_global_dataframe(token: str, session_local_id: str) -> None:
    global_keys = _global_editor_keys(token)
    global_df = _normalize_exercise_editor_df(
        st.session_state.get(global_keys["draft"])
    )
    new_row_df = _empty_exercise_dataframe(session_local_id)
    global_df = pd.concat([global_df, new_row_df], ignore_index=True)
    st.session_state[global_keys["draft"]] = _normalize_exercise_editor_df(global_df)

    block_maps = dict(st.session_state.get(global_keys["block_maps"], {}))
    block_maps[session_local_id] = {}
    st.session_state[global_keys["block_maps"]] = block_maps


def _remove_session_from_global_dataframe(token: str, session_local_id: str) -> None:
    global_keys = _global_editor_keys(token)
    global_df = _normalize_exercise_editor_df(
        st.session_state.get(global_keys["draft"])
    )
    if not global_df.empty:
        mask = global_df[WORKOUT_SESSION_COLUMN].astype(str) != str(session_local_id)
        global_df = global_df.loc[mask].copy()
    st.session_state[global_keys["draft"]] = _normalize_exercise_editor_df(global_df)

    block_maps = dict(st.session_state.get(global_keys["block_maps"], {}))
    block_maps.pop(session_local_id, None)
    st.session_state[global_keys["block_maps"]] = block_maps


def _clear_single_session_state(token: str, meta: dict):
    """Rimuove solo eventuali widget temporanei e stato AG Grid della sessione."""
    local_id = meta["local_id"]
    temporary_widget_prefixes = (
        f"workout_session_name_widget_{token}_{local_id}",
        f"workout_session_description_widget_{token}_{local_id}",
        f"workout_session_notes_widget_{token}_{local_id}",
        f"workout_grid_revision_{token}_{local_id}",
        f"ag_workout_grid_{token}_{local_id}",
    )
    for key in list(st.session_state.keys()):
        key_text = str(key)
        if any(key_text.startswith(prefix) for prefix in temporary_widget_prefixes):
            st.session_state.pop(key, None)


def _editor_rows_to_payload(
    data: pd.DataFrame,
    existing_block_map: dict[str, str],
) -> tuple[list[dict], dict[str, str]]:
    frame = pd.DataFrame(data).copy()
    payload = []
    block_map = dict(existing_block_map or {})
    block_definitions = {}
    seen_orders = set()
    block_row_counter = {}

    for row_index, row in frame.iterrows():
        exercise_name = str(row.get("Esercizio") or "").strip()
        if not exercise_name:
            # Le righe completamente vuote aggiunte dal data editor vengono ignorate.
            meaningful_values = [
                row.get("Serie"),
                row.get("Rep min"),
                row.get("Rep max"),
                row.get("Carico kg"),
                row.get("Note"),
            ]
            if any(not _is_empty(value) for value in meaningful_values):
                raise ValueError(f"Riga {row_index + 1}: specifica il nome dell'esercizio.")
            continue

        exercise_order = _to_int_or_none(row.get("Ordine"))
        if exercise_order is None:
            exercise_order = len(payload) + 1
        if exercise_order <= 0:
            raise ValueError(f"'{exercise_name}': l'ordine deve essere maggiore di zero.")
        if exercise_order in seen_orders:
            raise ValueError(f"Ordine {exercise_order} duplicato nella sessione.")
        seen_orders.add(exercise_order)

        block_type = str(row.get("Tipo blocco") or "STANDARD").strip().upper()
        if block_type not in ALLOWED_BLOCK_TYPES:
            raise ValueError(f"Tipo blocco non valido per '{exercise_name}'.")

        block_label = str(row.get("Blocco") or "").strip().upper()
        block_id = None
        block_order = None
        block_rounds = None

        if block_type != "STANDARD":
            if not block_label:
                raise ValueError(
                    f"'{exercise_name}': indica un'etichetta di blocco (es. A) per {block_type}."
                )
            block_id = block_map.setdefault(block_label, str(uuid4()))
            block_row_counter[block_label] = block_row_counter.get(block_label, 0) + 1
            block_order = _to_int_or_none(row.get("Ordine blocco"))
            if block_order is None:
                block_order = block_row_counter[block_label]
            block_rounds = _to_int_or_none(row.get("Round blocco"))

            definition = block_definitions.setdefault(
                block_label,
                {"block_type": block_type, "block_rounds": block_rounds},
            )
            if definition["block_type"] != block_type:
                raise ValueError(
                    f"Il blocco {block_label} usa più tipi diversi ({definition['block_type']} / {block_type})."
                )
            if (
                definition["block_rounds"] is not None
                and block_rounds is not None
                and definition["block_rounds"] != block_rounds
            ):
                raise ValueError(
                    f"Il blocco {block_label} deve avere lo stesso numero di round su tutte le righe."
                )
            if definition["block_rounds"] is None and block_rounds is not None:
                definition["block_rounds"] = block_rounds
        else:
            block_label = ""

        technique = str(row.get("Tecnica") or "STANDARD").strip().upper()
        if technique not in ALLOWED_TECHNIQUES:
            raise ValueError(f"Tecnica non valida per '{exercise_name}'.")

        reps_min = _to_int_or_none(row.get("Rep min"))
        reps_max = _to_int_or_none(row.get("Rep max"))
        if reps_min is not None and reps_min < 0:
            raise ValueError(f"'{exercise_name}': Rep min non può essere negativa.")
        if reps_max is not None and reps_max < 0:
            raise ValueError(f"'{exercise_name}': Rep max non può essere negativa.")
        if reps_min is not None and reps_max is not None and reps_max < reps_min:
            raise ValueError(f"'{exercise_name}': Rep max non può essere minore di Rep min.")

        target_sets = _to_int_or_none(row.get("Serie"))
        target_load = _to_float_or_none(row.get("Carico kg"))
        target_tut = _to_int_or_none(row.get("TUT sec"))
        target_rest = _to_int_or_none(row.get("Recupero sec"))
        target_rir = _to_float_or_none(row.get("RIR"))
        target_rpe = _to_float_or_none(row.get("RPE"))

        if target_sets is not None and target_sets <= 0:
            raise ValueError(f"'{exercise_name}': Serie deve essere maggiore di zero.")
        if target_load is not None and target_load < 0:
            raise ValueError(f"'{exercise_name}': Carico kg non può essere negativo.")
        if target_tut is not None and target_tut < 0:
            raise ValueError(f"'{exercise_name}': TUT non può essere negativo.")
        if target_rest is not None and target_rest < 0:
            raise ValueError(f"'{exercise_name}': Recupero non può essere negativo.")
        if target_rir is not None and not 0 <= target_rir <= 10:
            raise ValueError(f"'{exercise_name}': RIR deve essere compreso tra 0 e 10.")
        if target_rpe is not None and not 0 <= target_rpe <= 10:
            raise ValueError(f"'{exercise_name}': RPE deve essere compreso tra 0 e 10.")
        if block_order is not None and block_order <= 0:
            raise ValueError(f"'{exercise_name}': Ordine blocco deve essere maggiore di zero.")
        if block_rounds is not None and block_rounds <= 0:
            raise ValueError(f"'{exercise_name}': Round blocco deve essere maggiore di zero.")

        payload.append(
            {
                "id": None if _is_empty(row.get("__id")) else str(row.get("__id")),
                "exercise_name": exercise_name,
                "exercise_order": exercise_order,
                "block_id": block_id,
                "block_type": block_type,
                "block_order": block_order,
                "block_rounds": block_rounds,
                "target_sets": target_sets,
                "target_reps_min": reps_min,
                "target_reps_max": reps_max,
                "target_load_kg": target_load,
                "target_tut_seconds": target_tut,
                "target_rest_seconds": target_rest,
                "target_rir": target_rir,
                "target_rpe": target_rpe,
                "technique": technique,
                "technique_params": _parse_json_object(
                    row.get("Parametri tecnica"), exercise_name
                ),
                "progression_metrics": _parse_progression_metrics(
                    row.get("Progressione"), exercise_name
                ),
                "notes": None if _is_empty(row.get("Note")) else str(row.get("Note")).strip(),
            }
        )

    if not payload:
        raise ValueError("Inserisci almeno un esercizio nella sessione.")

    # Uniforma i round mancanti all'interno dello stesso blocco quando almeno una
    # riga li specifica. Il DB resta denormalizzato su 3 tabelle ma la UI evita
    # valori discordanti.
    for item in payload:
        if item["block_type"] == "STANDARD":
            continue
        label = next(
            (label for label, value in block_map.items() if value == item["block_id"]),
            None,
        )
        if label and item["block_rounds"] is None:
            item["block_rounds"] = block_definitions.get(label, {}).get("block_rounds")

    payload.sort(key=lambda item: item["exercise_order"])
    return payload, block_map



def _collect_sessions_payload(token: str, sessions_meta: list[dict]) -> list[dict]:
    if not sessions_meta:
        raise ValueError("Inserisci almeno una sessione nel workout.")

    global_keys = _global_editor_keys(token)
    global_df = _normalize_exercise_editor_df(
        st.session_state.get(global_keys["draft"])
    )
    session_meta_df = _normalize_session_meta_df(
        st.session_state.get(global_keys["session_meta"])
    )
    block_maps = dict(st.session_state.get(global_keys["block_maps"], {}))

    payload = []
    seen_names = set()

    for session_order, meta in enumerate(sessions_meta, start=1):
        local_id = str(meta["local_id"])
        meta_mask = session_meta_df["__session_local_id"].astype(str) == local_id
        if not meta_mask.any():
            raise ValueError(f"Metadati mancanti per la sessione {session_order}.")

        session_meta = session_meta_df.loc[meta_mask].iloc[0]
        session_name = str(session_meta.get("session_name") or "").strip()
        if not session_name:
            raise ValueError(f"Sessione {session_order}: il nome e obbligatorio.")

        normalized_name = session_name.casefold()
        if normalized_name in seen_names:
            raise ValueError(f"Nome sessione duplicato: '{session_name}'.")
        seen_names.add(normalized_name)

        session_df = global_df.loc[
            global_df[WORKOUT_SESSION_COLUMN].astype(str) == local_id
        ].copy()

        try:
            exercises_payload, updated_block_map = _editor_rows_to_payload(
                session_df,
                block_maps.get(local_id, {}),
            )
        except ValueError as exc:
            raise ValueError(f"Sessione '{session_name}': {exc}") from exc

        block_maps[local_id] = updated_block_map
        payload.append(
            {
                "id": meta.get("id"),
                "session_name": session_name,
                "session_order": session_order,
                "description": str(session_meta.get("description") or "").strip() or None,
                "notes": str(session_meta.get("notes") or "").strip() or None,
                "exercises": exercises_payload,
            }
        )

    st.session_state[global_keys["block_maps"]] = block_maps
    return payload


def _format_plan_period(plan: dict) -> str:
    start = plan.get("start_date")
    end = plan.get("end_date")
    if start and end:
        return f"{start} → {end}"
    if start:
        return f"dal {start}"
    if end:
        return f"fino al {end}"
    return "Periodo non definito"


def _navigate(section: str, editor_plan_id=None, reset_new: bool = False):
    st.session_state["workout_nav_target"] = section
    if editor_plan_id is not None:
        st.session_state["workout_editor_plan_id"] = str(editor_plan_id)
    elif section == EDITOR_SECTION:
        st.session_state["workout_editor_plan_id"] = None
        if reset_new:
            st.session_state["workout_new_revision"] = int(
                st.session_state.get("workout_new_revision", 0)
            ) + 1
    st.rerun()


def _clear_editor_token(token: str):
    exact_keys = {
        f"workout_name_{token}",
        f"workout_objective_{token}",
        f"workout_description_{token}",
        f"workout_has_period_{token}",
        f"workout_start_date_{token}",
        f"workout_end_date_{token}",
        f"workout_sessions_{token}",
        f"workout_session_select_{token}",
        f"workout_session_nav_target_{token}",
        f"workout_session_last_rendered_{token}",
        f"workout_exercise_draft_{token}",
        f"workout_block_maps_{token}",
        f"workout_session_meta_{token}",
    }
    dynamic_prefixes = (
        f"workout_session_name_widget_{token}_",
        f"workout_session_description_widget_{token}_",
        f"workout_session_notes_widget_{token}_",
        f"workout_grid_revision_{token}_",
        f"ag_workout_grid_{token}_",
    )
    for key in list(st.session_state.keys()):
        key_text = str(key)
        if key_text in exact_keys or any(
            key_text.startswith(prefix) for prefix in dynamic_prefixes
        ):
            st.session_state.pop(key, None)


TRAINING_EXERCISE_COLUMN = "__exercise_id"
TRAINING_EXERCISE_NAME_COLUMN = "__exercise_name"
TRAINING_EXERCISE_ORDER_COLUMN = "__exercise_order"
TRAINING_ROW_ID_COLUMN = "__training_row_id"

# I blocchi seguenti rappresentano piu esercizi che devono essere eseguiti come
# una singola unita di lavoro/round durante la registrazione della seduta.
MULTI_EXERCISE_BLOCK_TYPES = {
    "SUPERSET",
    "JUMP_SET",
    "TRI_SET",
    "GIANT_SET",
    "CIRCUIT",
}

# Tecniche intra-esercizio: non aggregano esercizi diversi, ma la UI le tratta
# come una singola unita logica e mantiene serie/segmenti nello stesso step.
SEGMENTED_TECHNIQUES = {
    "DROP_SET",
    "REST_PAUSE",
    "MYO_REPS",
    "CLUSTER",
}

TRAINING_COLUMNS = [
    "Serie",
    "Segmento",
    "Reps",
    "Carico kg",
    "TUT sec",
    "Recupero sec",
    "Durata sec",
    "RIR",
    "RPE",
    "Note",
    TRAINING_EXERCISE_COLUMN,
    TRAINING_EXERCISE_NAME_COLUMN,
    TRAINING_EXERCISE_ORDER_COLUMN,
    TRAINING_ROW_ID_COLUMN,
]


def _training_default_set_count(exercise: dict) -> int:
    """Numero di righe iniziali: nei blocchi multi-esercizio una serie = un round."""
    block_type = str(exercise.get("block_type") or "STANDARD").strip().upper()
    if block_type in MULTI_EXERCISE_BLOCK_TYPES:
        block_rounds = _to_int_or_none(exercise.get("block_rounds"))
        if block_rounds is not None and block_rounds > 0:
            return max(1, min(block_rounds, 30))

    target_sets = _to_int_or_none(exercise.get("target_sets")) or 1
    return max(1, min(target_sets, 30))


def _blank_training_rows(exercise: dict, set_count: int | None = None) -> pd.DataFrame:
    """Crea righe vuote per una nuova sessione allenante.

    Per superset/jump-set/tri-set/giant-set/circuit il numero di righe iniziale
    usa ``block_rounds``: il campo ``Serie`` diventa di fatto il round reale del
    blocco. Per un esercizio standard continua a rappresentare la serie.
    """
    exercise_id = str(exercise["id"])
    row_count = set_count if set_count is not None else _training_default_set_count(exercise)
    row_count = max(1, min(int(row_count), 30))

    rows = []
    for set_number in range(1, row_count + 1):
        rows.append(
            {
                "Serie": set_number,
                "Segmento": 1,
                "Reps": None,
                "Carico kg": None,
                "TUT sec": None,
                "Recupero sec": None,
                "Durata sec": None,
                "RIR": None,
                "RPE": None,
                "Note": "",
                TRAINING_EXERCISE_COLUMN: exercise_id,
                TRAINING_EXERCISE_NAME_COLUMN: exercise.get("exercise_name") or "",
                TRAINING_EXERCISE_ORDER_COLUMN: int(exercise.get("exercise_order") or 0),
                TRAINING_ROW_ID_COLUMN: str(uuid4()),
            }
        )
    return pd.DataFrame(rows, columns=TRAINING_COLUMNS)


def _normalize_training_df(data) -> pd.DataFrame:
    if data is None:
        return pd.DataFrame(columns=TRAINING_COLUMNS)

    frame = pd.DataFrame(data).copy()
    for column in TRAINING_COLUMNS:
        if column not in frame.columns:
            frame[column] = None
    frame = frame[TRAINING_COLUMNS].copy()

    for column in (
        "Serie", "Segmento", "Reps", "Carico kg", "TUT sec",
        "Recupero sec", "Durata sec", "RIR", "RPE",
        TRAINING_EXERCISE_ORDER_COLUMN,
    ):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")

    frame["Note"] = frame["Note"].where(frame["Note"].notna(), "")
    for column in (TRAINING_EXERCISE_COLUMN, TRAINING_EXERCISE_NAME_COLUMN):
        frame[column] = frame[column].where(frame[column].notna(), "").astype(str)

    row_ids = []
    for value in frame[TRAINING_ROW_ID_COLUMN].tolist():
        row_ids.append(str(value) if not _is_empty(value) else str(uuid4()))
    frame[TRAINING_ROW_ID_COLUMN] = row_ids
    return frame


def _latest_execution_id(history: list[dict]) -> str | None:
    if not history:
        return None
    frame = pd.DataFrame(history).copy()
    if frame.empty or "execution_id" not in frame.columns:
        return None
    frame["execution_id"] = frame["execution_id"].astype(str)
    frame["measurement_date"] = pd.to_datetime(frame["measurement_date"], errors="coerce")
    grouped = frame.groupby("execution_id", dropna=False)["measurement_date"].max()
    if grouped.empty:
        return None
    return str(grouped.idxmax())


def _default_training_plan_id(plans: list[dict]) -> str | None:
    """Workout di default: quello usato piu recentemente, altrimenti il piu recente."""
    active = [plan for plan in (plans or []) if plan.get("archived_at") is None]
    if not active:
        return None

    used = [plan for plan in active if plan.get("last_measurement_at") is not None]
    if used:
        def last_measurement(plan):
            value = pd.to_datetime(plan.get("last_measurement_at"), errors="coerce")
            return value if pd.notna(value) else pd.Timestamp.min
        return str(max(used, key=last_measurement)["id"])

    # get_workout_plans e gia ordinata per created_at DESC: il primo attivo e il
    # fallback piu naturale quando non esiste ancora storico.
    return str(active[0]["id"])


def _latest_session_id_from_history(history: list[dict], sessions: list[dict]) -> str | None:
    """Tipo sessione usato piu recentemente all'interno del workout selezionato."""
    if not sessions:
        return None
    valid_ids = {str(session["id"]) for session in sessions}
    if not history:
        return str(sessions[0]["id"])

    frame = pd.DataFrame(history).copy()
    if frame.empty or "workout_session_id" not in frame.columns:
        return str(sessions[0]["id"])
    frame["workout_session_id"] = frame["workout_session_id"].astype(str)
    frame["measurement_date"] = pd.to_datetime(frame["measurement_date"], errors="coerce")
    frame = frame.loc[frame["workout_session_id"].isin(valid_ids)]
    if frame.empty:
        return str(sessions[0]["id"])
    grouped = frame.groupby("workout_session_id")["measurement_date"].max()
    return str(grouped.idxmax()) if not grouped.empty else str(sessions[0]["id"])


def _build_training_draft(exercises: list[dict], history: list[dict]) -> tuple[pd.DataFrame, bool]:
    """Un solo DataFrame per tutta la sessione allenante.

    Se esiste una precedente esecuzione dello stesso tipo di sessione, vengono
    copiati i valori realmente registrati. In un blocco aggregato il numero di
    serie registrate per esercizio rappresenta anche il numero di round effettivi.
    """
    latest_execution_id = _latest_execution_id(history)
    history_df = pd.DataFrame(history).copy() if history else pd.DataFrame()
    if not history_df.empty:
        history_df["execution_id"] = history_df["execution_id"].astype(str)
        history_df["workout_exercise_id"] = history_df["workout_exercise_id"].astype(str)

    frames = []
    copied_previous = False
    for exercise in sorted(exercises, key=lambda item: int(item.get("exercise_order") or 0)):
        exercise_id = str(exercise["id"])
        previous_rows = pd.DataFrame()
        if latest_execution_id and not history_df.empty:
            previous_rows = history_df.loc[
                (history_df["execution_id"] == latest_execution_id)
                & (history_df["workout_exercise_id"] == exercise_id)
            ].copy()

        if previous_rows.empty:
            frames.append(_blank_training_rows(exercise))
            continue

        copied_previous = True
        rows = []
        previous_rows = previous_rows.sort_values(["set_number", "segment_number"])
        for _, row in previous_rows.iterrows():
            rows.append(
                {
                    "Serie": _to_int_or_none(row.get("set_number")),
                    "Segmento": _to_int_or_none(row.get("segment_number")) or 1,
                    "Reps": _to_int_or_none(row.get("reps_completed")),
                    "Carico kg": _decimal_to_float(row.get("load_kg")),
                    "TUT sec": _to_int_or_none(row.get("tut_seconds")),
                    "Recupero sec": _to_int_or_none(row.get("rest_seconds")),
                    "Durata sec": _to_int_or_none(row.get("duration_seconds")),
                    "RIR": _decimal_to_float(row.get("rir")),
                    "RPE": _decimal_to_float(row.get("rpe")),
                    "Note": row.get("notes") or "",
                    TRAINING_EXERCISE_COLUMN: exercise_id,
                    TRAINING_EXERCISE_NAME_COLUMN: exercise.get("exercise_name") or "",
                    TRAINING_EXERCISE_ORDER_COLUMN: int(exercise.get("exercise_order") or 0),
                    TRAINING_ROW_ID_COLUMN: str(uuid4()),
                }
            )
        frames.append(pd.DataFrame(rows, columns=TRAINING_COLUMNS))

    if not frames:
        return pd.DataFrame(columns=TRAINING_COLUMNS), copied_previous
    return _normalize_training_df(pd.concat(frames, ignore_index=True)), copied_previous


def _merge_training_exercise_into_global(
    global_df: pd.DataFrame,
    exercise: dict,
    edited_df: pd.DataFrame,
) -> pd.DataFrame:
    global_df = _normalize_training_df(global_df)
    edited_df = _normalize_training_df(edited_df)
    exercise_id = str(exercise["id"])

    edited_df[TRAINING_EXERCISE_COLUMN] = exercise_id
    edited_df[TRAINING_EXERCISE_NAME_COLUMN] = exercise.get("exercise_name") or ""
    edited_df[TRAINING_EXERCISE_ORDER_COLUMN] = int(exercise.get("exercise_order") or 0)

    other_df = global_df.loc[
        global_df[TRAINING_EXERCISE_COLUMN].astype(str) != exercise_id
    ].copy()
    merged = pd.concat([other_df, edited_df], ignore_index=True)
    if TRAINING_ROW_ID_COLUMN in merged.columns:
        merged = merged.drop_duplicates(subset=[TRAINING_ROW_ID_COLUMN], keep="last")
    return _normalize_training_df(merged)


def _build_training_steps(exercises: list[dict]) -> list[dict]:
    """Costruisce gli step UX: un esercizio standard o un intero blocco aggregato."""
    grouped: dict[str, dict] = {}
    order_keys = []

    for exercise in sorted(exercises, key=lambda item: int(item.get("exercise_order") or 0)):
        exercise_id = str(exercise["id"])
        block_type = str(exercise.get("block_type") or "STANDARD").strip().upper()
        block_id = str(exercise.get("block_id") or "").strip()

        if block_type in MULTI_EXERCISE_BLOCK_TYPES and block_id:
            key = f"block:{block_id}"
            kind = "multi"
        else:
            key = f"exercise:{exercise_id}"
            kind = "single"

        if key not in grouped:
            grouped[key] = {
                "key": key,
                "kind": kind,
                "block_type": block_type if kind == "multi" else "STANDARD",
                "block_id": block_id or None,
                "order": int(exercise.get("exercise_order") or 0),
                "exercises": [],
            }
            order_keys.append(key)
        grouped[key]["exercises"].append(exercise)

    steps = []
    for key in order_keys:
        step = grouped[key]
        step["exercises"] = sorted(
            step["exercises"],
            key=lambda item: (
                _to_int_or_none(item.get("block_order")) or 999,
                int(item.get("exercise_order") or 0),
            ),
        )
        names = [str(item.get("exercise_name") or "Esercizio") for item in step["exercises"]]

        if step["kind"] == "multi":
            step["label"] = f"{step['block_type'].replace('_', ' ')} · " + " + ".join(names)
            prescribed = [
                _to_int_or_none(item.get("block_rounds"))
                for item in step["exercises"]
                if _to_int_or_none(item.get("block_rounds")) is not None
            ]
            step["prescribed_rounds"] = max(prescribed) if prescribed else None
        else:
            exercise = step["exercises"][0]
            technique = str(exercise.get("technique") or "STANDARD").strip().upper()
            step["technique"] = technique
            prefix = technique.replace("_", " ") if technique != "STANDARD" else ""
            step["label"] = f"{prefix} · {names[0]}" if prefix else names[0]
            step["prescribed_rounds"] = None
        steps.append(step)

    return sorted(steps, key=lambda item: item["order"])


def _current_step_rounds(global_df: pd.DataFrame, step: dict) -> int:
    if step.get("kind") != "multi":
        return 0
    frame = _normalize_training_df(global_df)
    ids = {str(item["id"]) for item in step["exercises"]}
    subset = frame.loc[frame[TRAINING_EXERCISE_COLUMN].astype(str).isin(ids)].copy()
    if subset.empty:
        return int(step.get("prescribed_rounds") or 1)
    values = pd.to_numeric(subset["Serie"], errors="coerce").dropna()
    return max(1, int(values.max())) if not values.empty else int(step.get("prescribed_rounds") or 1)


def _resize_training_step_rounds(
    global_df: pd.DataFrame,
    step: dict,
    new_rounds: int,
) -> pd.DataFrame:
    """Aumenta/riduce i round di un blocco mantenendo allineati tutti gli esercizi."""
    frame = _normalize_training_df(global_df)
    new_rounds = max(1, min(int(new_rounds), 30))

    for exercise in step.get("exercises", []):
        exercise_id = str(exercise["id"])
        current = frame.loc[
            frame[TRAINING_EXERCISE_COLUMN].astype(str) == exercise_id
        ].copy()

        if current.empty:
            updated = _blank_training_rows(exercise, set_count=new_rounds)
        else:
            current["Serie"] = pd.to_numeric(current["Serie"], errors="coerce")
            current = current.loc[current["Serie"].fillna(1) <= new_rounds].copy()
            existing_rounds = {
                int(value)
                for value in current["Serie"].dropna().tolist()
                if int(value) > 0
            }
            additions = []
            for round_number in range(1, new_rounds + 1):
                if round_number in existing_rounds:
                    continue
                new_row = _blank_training_rows(exercise, set_count=1).iloc[0].to_dict()
                new_row["Serie"] = round_number
                new_row[TRAINING_ROW_ID_COLUMN] = str(uuid4())
                additions.append(new_row)
            if additions:
                current = pd.concat([current, pd.DataFrame(additions)], ignore_index=True)
            updated = _normalize_training_df(current)

        frame = _merge_training_exercise_into_global(frame, exercise, updated)

    return _normalize_training_df(frame)


def _exercise_prescription(exercise: dict, *, use_round_label: bool = False) -> str:
    bits = []
    if use_round_label and exercise.get("block_rounds"):
        bits.append(f"{exercise['block_rounds']} round prescritti")
    elif exercise.get("target_sets"):
        bits.append(f"{exercise['target_sets']} serie")

    reps_min = exercise.get("target_reps_min")
    reps_max = exercise.get("target_reps_max")
    if reps_min is not None or reps_max is not None:
        bits.append(f"reps {reps_min or '—'}-{reps_max or '—'}")
    if exercise.get("target_load_kg") is not None:
        bits.append(f"target {exercise['target_load_kg']} kg")
    technique = str(exercise.get("technique") or "STANDARD").strip().upper()
    if technique != "STANDARD":
        bits.append(technique.replace("_", " "))
    return " · ".join(bits)


def _training_row_has_values(row) -> bool:
    values = [
        row.get("Reps"), row.get("Carico kg"), row.get("TUT sec"),
        row.get("Recupero sec"), row.get("Durata sec"), row.get("RIR"),
        row.get("RPE"), row.get("Note"),
    ]
    return any(not _is_empty(value) for value in values)


def _training_draft_to_payload(data: pd.DataFrame, measurement_date: datetime) -> list[dict]:
    frame = _normalize_training_df(data)
    payload = []
    seen_keys = set()

    for row_index, row in frame.iterrows():
        if not _training_row_has_values(row):
            continue

        exercise_id = str(row.get(TRAINING_EXERCISE_COLUMN) or "").strip()
        if not exercise_id:
            raise ValueError(f"Riga {row_index + 1}: exercise_id tecnico mancante.")

        set_number = _to_int_or_none(row.get("Serie")) or 1
        segment_number = _to_int_or_none(row.get("Segmento")) or 1
        if set_number <= 0 or segment_number <= 0:
            raise ValueError("Serie e segmento devono essere maggiori di zero.")

        logical_key = (exercise_id, set_number, segment_number)
        if logical_key in seen_keys:
            raise ValueError(
                f"Duplicata serie {set_number}, segmento {segment_number} "
                f"per {row.get(TRAINING_EXERCISE_NAME_COLUMN) or 'esercizio'}."
            )
        seen_keys.add(logical_key)

        payload.append(
            {
                "workout_exercise_id": exercise_id,
                "measurement_date": measurement_date,
                "set_number": set_number,
                "segment_number": segment_number,
                "reps_completed": _to_int_or_none(row.get("Reps")),
                "load_kg": _to_float_or_none(row.get("Carico kg")),
                "tut_seconds": _to_int_or_none(row.get("TUT sec")),
                "rest_seconds": _to_int_or_none(row.get("Recupero sec")),
                "duration_seconds": _to_int_or_none(row.get("Durata sec")),
                "rir": _to_float_or_none(row.get("RIR")),
                "rpe": _to_float_or_none(row.get("RPE")),
                "notes": None if _is_empty(row.get("Note")) else str(row.get("Note")).strip(),
            }
        )

    if not payload:
        raise ValueError("Inserisci almeno una performance prima di salvare la sessione.")
    return payload


def _history_editor_dataframe(rows: pd.DataFrame) -> pd.DataFrame:
    frame = rows.copy()
    result = pd.DataFrame(
        {
            "Esercizio": frame["exercise_name"],
            "Serie": frame["set_number"],
            "Segmento": frame["segment_number"],
            "Reps": frame["reps_completed"],
            "Carico kg": frame["load_kg"],
            "TUT sec": frame["tut_seconds"],
            "Recupero sec": frame["rest_seconds"],
            "Durata sec": frame["duration_seconds"],
            "RIR": frame["rir"],
            "RPE": frame["rpe"],
            "Note": frame["notes"],
            "__measurement_id": frame["id"].astype(str),
            "__exercise_id": frame["workout_exercise_id"].astype(str),
            "__exercise_order": frame["exercise_order"],
        }
    )
    return result.sort_values(["__exercise_order", "Serie", "Segmento"]).reset_index(drop=True)


def _history_editor_to_payload(data: pd.DataFrame, measurement_date: datetime) -> list[dict]:
    frame = pd.DataFrame(data).copy()
    payload = []
    for row_index, row in frame.iterrows():
        measurement_id = str(row.get("__measurement_id") or "").strip()
        if not measurement_id:
            raise ValueError(f"Riga storico {row_index + 1}: ID misurazione mancante.")
        payload.append(
            {
                "id": measurement_id,
                "measurement_date": measurement_date,
                "set_number": _to_int_or_none(row.get("Serie")) or 1,
                "segment_number": _to_int_or_none(row.get("Segmento")) or 1,
                "reps_completed": _to_int_or_none(row.get("Reps")),
                "load_kg": _to_float_or_none(row.get("Carico kg")),
                "tut_seconds": _to_int_or_none(row.get("TUT sec")),
                "rest_seconds": _to_int_or_none(row.get("Recupero sec")),
                "duration_seconds": _to_int_or_none(row.get("Durata sec")),
                "rir": _to_float_or_none(row.get("RIR")),
                "rpe": _to_float_or_none(row.get("RPE")),
                "notes": None if _is_empty(row.get("Note")) else str(row.get("Note")).strip(),
            }
        )
    return payload


def _clear_training_state(context: str) -> None:
    exact_keys = {
        f"training_active_{context}",
        f"training_execution_id_{context}",
        f"training_draft_{context}",
        f"training_date_{context}",
        f"training_time_{context}",
        f"training_step_index_{context}",
        f"training_exercise_index_{context}",  # cleanup compatibilita versione precedente
        f"training_copied_previous_{context}",
    }
    prefixes = (
        f"training_editor_{context}_",
        f"training_step_form_{context}_",
    )
    for key in list(st.session_state.keys()):
        key_text = str(key)
        if key_text in exact_keys or any(key_text.startswith(prefix) for prefix in prefixes):
            st.session_state.pop(key, None)


# -----------------------------------------------------------------------------
# Pagina / paziente
# -----------------------------------------------------------------------------

st.title("🏋️ Gestione Workout")
st.caption(
    "Crea e modifica schede di allenamento, gestisci blocchi (superset, jump set, circuiti) "
    "e registra la progressione degli esercizi."
)

if "user_id" not in st.session_state:
    st.session_state.user_id = "00000000-0000-0000-0000-000000000000"
user_id = st.session_state.user_id

try:
    patients = get_all_patients(tec_conf, user_id)
except Exception as exc:
    logger.error("Errore nel recupero pazienti", exc_info=True)
    st.error(f"Impossibile caricare i pazienti: {exc}")
    st.stop()

if not patients:
    st.warning("Nessun paziente presente. Crea prima un assistito nella pagina Pazienti.")
    st.stop()

patient_labels = {
    f"{patient.get('cognome', '')} {patient.get('nome', '')} ({patient.get('data_nascita', '')})": str(patient["id"])
    for patient in patients
}
patient_ids = list(patient_labels.values())
current_patient_id = st.session_state.get("current_patient_id")
selected_index = patient_ids.index(str(current_patient_id)) if str(current_patient_id) in patient_ids else 0
selected_patient_label = st.selectbox(
    "Seleziona il paziente",
    options=list(patient_labels.keys()),
    index=selected_index,
)
patient_id = patient_labels[selected_patient_label]
patient = next(item for item in patients if str(item["id"]) == patient_id)
patient_name = f"{patient.get('cognome', '')} {patient.get('nome', '')}".strip()

if st.session_state.get("workout_patient_context") != patient_id:
    st.session_state["workout_patient_context"] = patient_id
    st.session_state["workout_editor_plan_id"] = None
    st.session_state["workout_new_revision"] = int(st.session_state.get("workout_new_revision", 0)) + 1

st.session_state["current_patient_id"] = patient_id
st.session_state["current_patient_name"] = patient_name

nav_target = st.session_state.pop("workout_nav_target", None)
if nav_target:
    st.session_state["workout_section"] = nav_target
if "workout_section" not in st.session_state:
    st.session_state["workout_section"] = LIST_SECTION

section = st.radio(
    "Sezione",
    [LIST_SECTION, EDITOR_SECTION, PROGRESS_SECTION],
    horizontal=True,
    key="workout_section",
    label_visibility="collapsed",
)

flash_message = st.session_state.pop("workout_flash_message", None)
if flash_message:
    st.success(flash_message)


# -----------------------------------------------------------------------------
# LISTING
# -----------------------------------------------------------------------------

if section == LIST_SECTION:
    top_left, top_right = st.columns([3, 1])
    with top_left:
        st.subheader(f"Workout di {patient_name}")
    with top_right:
        if st.button("➕ Nuovo workout", use_container_width=True):
            _navigate(EDITOR_SECTION, reset_new=True)

    # -------------------------------------------------------------------------
    # CTA PRINCIPALE: NUOVA SESSIONE ALLENANTE
    # -------------------------------------------------------------------------
    try:
        active_plans_for_training = get_workout_plans(
            tec_conf, patient_id, include_archived=False
        )
    except Exception as exc:
        logger.error("Errore nel caricamento workout attivi", exc_info=True)
        st.error(f"Impossibile caricare i workout: {exc}")
        active_plans_for_training = []

    default_training_plan_id = _default_training_plan_id(active_plans_for_training)
    if st.button(
        "▶️ Nuova sessione allenante",
        type="primary",
        use_container_width=True,
        disabled=default_training_plan_id is None,
        help=(
            "Apre il workout usato piu recentemente. Se non esiste storico, "
            "usa il workout attivo piu recente."
        ),
    ):
        st.session_state["workout_progress_plan_id"] = default_training_plan_id
        st.session_state.pop(f"workout_progress_plan_select_{patient_id}", None)
        st.session_state["workout_progress_entry_mode"] = "new_training"
        _navigate(PROGRESS_SECTION)

    if default_training_plan_id is not None:
        default_plan = next(
            (
                plan for plan in active_plans_for_training
                if str(plan["id"]) == str(default_training_plan_id)
            ),
            None,
        )
        if default_plan:
            source_label = (
                "ultimo workout usato"
                if default_plan.get("last_measurement_at") is not None
                else "workout attivo piu recente"
            )
            st.caption(
                f"Default nuova sessione: **{default_plan.get('workout_name', 'Workout')}** "
                f"({source_label})."
            )

    st.divider()

    show_archived = st.toggle("Mostra workout archiviati", value=False)
    if show_archived:
        try:
            plans = get_workout_plans(tec_conf, patient_id, include_archived=True)
        except Exception as exc:
            logger.error("Errore nel caricamento workout", exc_info=True)
            st.error(f"Impossibile caricare i workout: {exc}")
            plans = []
    else:
        plans = active_plans_for_training

    if not plans:
        st.info("Nessun workout presente per questo assistito.")
    else:
        st.caption(f"{len(plans)} workout trovati.")

    for plan in plans:
        plan_id = str(plan["id"])
        archived = plan.get("archived_at") is not None
        title = f"{plan.get('workout_name', 'Workout')}"
        if archived:
            title += " · ARCHIVIATO"

        expander_bits = [title, _format_plan_period(plan)]
        if plan.get("objective"):
            expander_bits.append(str(plan["objective"]))
        with st.expander(" · ".join(expander_bits), expanded=False):
            header_col, status_col = st.columns([4, 1])
            with header_col:
                details = []
                if plan.get("objective"):
                    details.append(f"Obiettivo: **{plan['objective']}**")
                details.append(_format_plan_period(plan))
                st.caption(" · ".join(details))
                if plan.get("description"):
                    st.write(plan["description"])

            with status_col:
                if archived:
                    st.warning("Archiviato")
                else:
                    st.success("Attivo")

            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Sessioni", int(plan.get("session_count") or 0))
            m2.metric("Esercizi", int(plan.get("exercise_count") or 0))
            m3.metric("Rilevazioni", int(plan.get("measurement_count") or 0))
            last_measurement = plan.get("last_measurement_at")
            m4.metric(
                "Ultima rilevazione",
                last_measurement.strftime("%d/%m/%Y") if hasattr(last_measurement, "strftime") else "—",
            )

            action_edit, action_progress, action_archive = st.columns(3)
            with action_edit:
                if st.button(
                    "✏️ Modifica",
                    key=f"edit_workout_{plan_id}",
                    disabled=archived,
                    use_container_width=True,
                ):
                    _navigate(EDITOR_SECTION, editor_plan_id=plan_id)
            with action_progress:
                if st.button(
                    "📈 Progressi / storico",
                    key=f"progress_workout_{plan_id}",
                    disabled=archived,
                    use_container_width=True,
                ):
                    st.session_state["workout_progress_plan_id"] = plan_id
                    st.session_state.pop(
                        f"workout_progress_plan_select_{patient_id}", None
                    )
                    _navigate(PROGRESS_SECTION)
            with action_archive:
                if archived:
                    if st.button(
                        "♻️ Ripristina",
                        key=f"restore_workout_{plan_id}",
                        use_container_width=True,
                    ):
                        try:
                            restore_workout_plan(tec_conf, patient_id, plan_id)
                            st.session_state["workout_flash_message"] = (
                                f"Workout '{plan.get('workout_name')}' ripristinato."
                            )
                            st.rerun()
                        except Exception as exc:
                            st.error(f"Impossibile ripristinare il workout: {exc}")
                else:
                    if st.button(
                        "🗑️ Elimina",
                        key=f"archive_workout_{plan_id}",
                        use_container_width=True,
                    ):
                        st.session_state["workout_archive_pending"] = plan_id

            if st.session_state.get("workout_archive_pending") == plan_id:
                st.warning(
                    "Il workout verra rimosso dai piani attivi ma mantenuto nello storico "
                    "per preservare eventuali misurazioni associate."
                )
                cancel_col, confirm_col = st.columns(2)
                with cancel_col:
                    if st.button(
                        "Annulla",
                        key=f"cancel_archive_workout_{plan_id}",
                        use_container_width=True,
                    ):
                        st.session_state.pop("workout_archive_pending", None)
                        st.rerun()
                with confirm_col:
                    if st.button(
                        "Conferma eliminazione",
                        key=f"confirm_archive_workout_{plan_id}",
                        type="primary",
                        use_container_width=True,
                    ):
                        try:
                            archive_workout_plan(tec_conf, patient_id, plan_id)
                            st.session_state.pop("workout_archive_pending", None)
                            st.session_state["workout_flash_message"] = (
                                f"Workout '{plan.get('workout_name')}' archiviato."
                            )
                            st.rerun()
                        except Exception as exc:
                            st.error(f"Impossibile archiviare il workout: {exc}")


# -----------------------------------------------------------------------------
# EDITOR
# -----------------------------------------------------------------------------

elif section == EDITOR_SECTION:
    editor_plan_id = st.session_state.get("workout_editor_plan_id")
    is_edit = bool(editor_plan_id)

    plan = None
    if is_edit:
        try:
            plan = get_workout_plan(tec_conf, patient_id, editor_plan_id)
        except Exception as exc:
            logger.error("Errore nel caricamento del workout", exc_info=True)
            st.error(f"Impossibile caricare il workout: {exc}")
            st.stop()
        if not plan:
            st.error("Workout non trovato per il paziente selezionato.")
            st.stop()

    revision = int(st.session_state.get("workout_new_revision", 0))
    token = str(editor_plan_id) if is_edit else f"new_{patient_id}_{revision}"

    header_col, action_col = st.columns([4, 1])
    with header_col:
        st.subheader("Modifica workout" if is_edit else "Nuovo workout")
        st.caption(
            "Il workout e il programma complessivo. Ogni tab rappresenta una sessione/giornata "
            "indipendente: A/B, Push/Pull/Legs, Upper/Lower, A/B/C/D, ecc."
        )
        if is_edit:
            st.caption(
                "Sessioni ed esercizi rimossi vengono archiviati: gli UUID gia usati dalle "
                "misurazioni restano validi e lo storico non viene perso."
            )
    with action_col:
        if st.button("↩️ Torna alla lista", use_container_width=True):
            _navigate(LIST_SECTION)

    name_key = f"workout_name_{token}"
    objective_key = f"workout_objective_{token}"
    description_key = f"workout_description_{token}"
    period_key = f"workout_has_period_{token}"
    start_key = f"workout_start_date_{token}"
    end_key = f"workout_end_date_{token}"
    sessions_key = f"workout_sessions_{token}"
    global_editor_keys = _global_editor_keys(token)
    exercise_draft_key = global_editor_keys["draft"]

    if name_key not in st.session_state:
        st.session_state[name_key] = plan.get("workout_name", "") if plan else ""
        st.session_state[objective_key] = plan.get("objective", "") if plan else ""
        st.session_state[description_key] = plan.get("description", "") if plan else ""
        has_period_initial = bool(plan and (plan.get("start_date") or plan.get("end_date")))
        st.session_state[period_key] = has_period_initial
        st.session_state[start_key] = (
            plan.get("start_date") if plan and plan.get("start_date") else date.today()
        )
        st.session_state[end_key] = (
            plan.get("end_date") if plan and plan.get("end_date") else date.today()
        )

    sessions_meta = _initialize_sessions_editor(token, plan)

    col1, col2 = st.columns(2)
    with col1:
        workout_name = st.text_input("Nome workout *", key=name_key)
        objective = st.text_input(
            "Obiettivo",
            key=objective_key,
            placeholder="Es. Ipertrofia, forza, ricondizionamento...",
        )
    with col2:
        has_period = st.checkbox("Definisci periodo del piano", key=period_key)
        if has_period:
            date_col1, date_col2 = st.columns(2)
            with date_col1:
                start_date = st.date_input("Data inizio", key=start_key)
            with date_col2:
                end_date = st.date_input("Data fine", key=end_key)
        else:
            start_date = None
            end_date = None

    description = st.text_area(
        "Descrizione / note generali",
        key=description_key,
        height=90,
    )

    st.divider()

    # -------------------------------------------------------------------------
    # SESSIONI WORKOUT
    # -------------------------------------------------------------------------
    session_select_key = f"workout_session_select_{token}"
    session_nav_target_key = f"workout_session_nav_target_{token}"
    session_last_rendered_key = f"workout_session_last_rendered_{token}"

    # Prima di cambiare sessione, consolida gli ultimi valori dei widget della
    # sessione renderizzata nel rerun precedente. In questo momento le chiavi
    # widget sono ancora disponibili anche se nel rerun corrente verra scelta
    # un'altra sessione.
    last_rendered_local_id = st.session_state.get(session_last_rendered_key)
    if last_rendered_local_id:
        _capture_session_meta_from_widget_state(token, str(last_rendered_local_id))

    # Applica un eventuale target di navigazione generato da un'azione del rerun
    # precedente (aggiunta, spostamento o rimozione sessione).
    pending_target = st.session_state.pop(session_nav_target_key, None)
    if pending_target:
        st.session_state[session_select_key] = pending_target

    session_title_col, add_session_col = st.columns([4, 1])

    with session_title_col:
        st.markdown("### Sessioni del workout")
        st.caption(
            "Crea una sessione per ogni giornata/seduta prescritta. "
            "I blocchi (superset, circuiti, ecc.) sono locali alla sessione corrente."
        )

    # Il click viene solo registrato qui. La nuova sessione viene creata dopo
    # il rendering della AG Grid corrente, cosi il draft della sessione attiva
    # viene consolidato prima del rerun.
    with add_session_col:
        add_session_requested = st.button(
            "➕ Aggiungi sessione",
            key=f"add_workout_session_{token}",
            use_container_width=True,
        )

    # -------------------------------------------------------------------------
    # SELETTORE SESSIONE
    # -------------------------------------------------------------------------
    session_ids = [meta["local_id"] for meta in sessions_meta]
    if st.session_state.get(session_select_key) not in session_ids:
        st.session_state[session_select_key] = session_ids[0]

    session_meta_df = _normalize_session_meta_df(
        st.session_state.get(global_editor_keys["session_meta"])
    )
    label_by_local_id = {}
    for index, session_meta in enumerate(sessions_meta, start=1):
        local_id = str(session_meta["local_id"])
        meta_mask = session_meta_df["__session_local_id"].astype(str) == local_id
        current_name = (
            str(session_meta_df.loc[meta_mask, "session_name"].iloc[0]).strip()
            if meta_mask.any()
            else ""
        )
        label_by_local_id[local_id] = current_name or f"Sessione {index}"

    selected_local_id = st.radio(
        "Sessione",
        options=session_ids,
        key=session_select_key,
        horizontal=True,
        format_func=lambda local_id: label_by_local_id.get(local_id, "Sessione"),
        help="Viene caricata una sola grid alla volta per mantenere l'editor reattivo.",
    )
    st.session_state[session_last_rendered_key] = selected_local_id

    selected_index = session_ids.index(selected_local_id)
    meta = sessions_meta[selected_index]
    selected_meta = _get_session_meta(token, meta["local_id"])

    # -------------------------------------------------------------------------
    # EDITOR SESSIONE CORRENTE
    # -------------------------------------------------------------------------
    pending_action = None

    with st.container(border=True):
        meta_col1, meta_col2 = st.columns(2)

        # Questi widget sono solo una VISTA della riga corrente del DataFrame
        # workout_session_meta_<token>. Il valore persistente UI non vive piu
        # nelle chiavi widget, quindi il cambio sessione non puo cancellarlo.
        with meta_col1:
            session_name_value = st.text_input(
                "Nome sessione *",
                value=str(selected_meta.get("session_name") or ""),
                key=f"workout_session_name_widget_{token}_{meta['local_id']}",
                placeholder="Es. A, Push, Pull, Upper 1...",
            )

        with meta_col2:
            session_description_value = st.text_input(
                "Descrizione breve",
                value=str(selected_meta.get("description") or ""),
                key=f"workout_session_description_widget_{token}_{meta['local_id']}",
                placeholder="Es. Spinta + quadricipiti + MetCon",
            )

        session_notes_value = st.text_area(
            "Note sessione",
            value=str(selected_meta.get("notes") or ""),
            key=f"workout_session_notes_widget_{token}_{meta['local_id']}",
            height=70,
            placeholder="Indicazioni generali valide per questa sessione...",
        )

        # Copia immediatamente i valori della sessione visibile nel DataFrame
        # globale dei metadati PRIMA di qualsiasi add/move/delete/rerun.
        _update_session_meta(
            token,
            meta["local_id"],
            session_name=session_name_value,
            description=session_description_value,
            notes=session_notes_value,
        )

        st.markdown("#### Esercizi")
        st.caption(
            "Per collegare piu esercizi usa la stessa etichetta in **Blocco** (es. A) e "
            "scegli SUPERSET, JUMP_SET, TRI_SET, GIANT_SET o CIRCUIT."
        )

        # La grid visualizza solo la slice della sessione corrente, ma ogni
        # modifica viene reintegrata nell'unico DataFrame globale.
        _render_workout_exercise_grid(
            exercise_draft_key,
            meta["local_id"],
            token,
        )

        with st.expander("ℹ️ Blocchi e tecniche della sessione"):
            st.markdown(
                """
- **Blocco** identifica un gruppo di esercizi *dentro questa sessione*.
- **Tipo blocco** descrive la relazione: SUPERSET, JUMP_SET, TRI_SET, GIANT_SET o CIRCUIT.
- **Ordine blocco** definisce la sequenza interna del gruppo.
- **Round blocco** indica quante volte ripetere l'intero gruppo.
- **Tecnica** riguarda invece il singolo esercizio (DROP_SET, REST_PAUSE, CLUSTER, ecc.).
- **Progressione** accetta piu metriche, ad esempio `LOAD,VOLUME`.
                """
            )

        move_left_col, move_right_col, delete_col = st.columns([1, 1, 2])

        with move_left_col:
            if st.button(
                "⬅️ Sposta",
                key=f"move_session_left_{token}_{meta['local_id']}",
                disabled=selected_index == 0,
                use_container_width=True,
            ):
                pending_action = ("left", selected_index)

        with move_right_col:
            if st.button(
                "➡️ Sposta",
                key=f"move_session_right_{token}_{meta['local_id']}",
                disabled=selected_index == len(sessions_meta) - 1,
                use_container_width=True,
            ):
                pending_action = ("right", selected_index)

        with delete_col:
            if st.button(
                "🗑️ Rimuovi sessione",
                key=f"remove_session_{token}_{meta['local_id']}",
                disabled=len(sessions_meta) <= 1,
                use_container_width=True,
            ):
                pending_action = ("delete", selected_index)

    # -------------------------------------------------------------------------
    # AGGIUNTA SESSIONE
    # -------------------------------------------------------------------------
    if add_session_requested:
        # Non riscriviamo il draft qui: la grid lo ha gia sincronizzato.
        sessions_meta = list(
            st.session_state.get(
                sessions_key,
                sessions_meta,
            )
        )

        new_meta = _new_session_meta(len(sessions_meta) + 1)
        sessions_meta.append(new_meta)

        # I metadati della sessione restano separati, ma gli esercizi vengono
        # aggiunti in append all'UNICO DataFrame globale tramite la colonna
        # tecnica __session_local_id.
        _append_session_meta(token, new_meta)
        _append_new_session_to_global_dataframe(token, new_meta["local_id"])

        st.session_state[sessions_key] = sessions_meta
        st.session_state[session_nav_target_key] = new_meta["local_id"]
        st.rerun()

    # -------------------------------------------------------------------------
    # SPOSTAMENTO / RIMOZIONE SESSIONE
    # -------------------------------------------------------------------------
    if pending_action:
        action, index = pending_action
        sessions_meta = list(st.session_state[sessions_key])
        target_local_id = sessions_meta[index]["local_id"]

        if action == "left" and index > 0:
            sessions_meta[index - 1], sessions_meta[index] = (
                sessions_meta[index],
                sessions_meta[index - 1],
            )
        elif action == "right" and index < len(sessions_meta) - 1:
            sessions_meta[index + 1], sessions_meta[index] = (
                sessions_meta[index],
                sessions_meta[index + 1],
            )
        elif action == "delete" and len(sessions_meta) > 1:
            removed = sessions_meta.pop(index)
            _remove_session_from_global_dataframe(token, removed["local_id"])
            _remove_session_meta(token, removed["local_id"])
            _clear_single_session_state(token, removed)
            new_index = min(index, len(sessions_meta) - 1)
            target_local_id = sessions_meta[new_index]["local_id"]

        st.session_state[sessions_key] = sessions_meta
        st.session_state[session_nav_target_key] = target_local_id
        st.rerun()

    st.caption(
        "Usa **+** e **×** nella grid per aggiungere/rimuovere esercizi. "
        "Tutte le sessioni condividono un unico DataFrame; la colonna tecnica nascosta "
        "`__session_local_id` determina quali righe mostrare nella sessione selezionata."
    )

    save_col, reset_col = st.columns([3, 1])
    with save_col:
        save_clicked = st.button(
            "💾 Salva modifiche" if is_edit else "💾 Crea workout",
            type="primary",
            use_container_width=True,
        )
    with reset_col:
        if st.button("🔄 Reimposta", use_container_width=True):
            _clear_editor_token(token)
            st.rerun()

    if save_clicked:
        try:
            workout_name = str(workout_name or "").strip()
            if not workout_name:
                raise ValueError("Il nome del workout e obbligatorio.")
            if has_period and start_date and end_date and end_date < start_date:
                raise ValueError("La data fine non puo precedere la data inizio.")

            exists = workout_name_exists(
                tec_conf,
                patient_id,
                workout_name,
                exclude_workout_id=editor_plan_id if is_edit else None,
            )
            if exists:
                raise ValueError(
                    "Esiste gia un workout attivo con questo nome per il paziente selezionato."
                )

            sessions_payload = _collect_sessions_payload(
                token,
                st.session_state.get(sessions_key, []),
            )

            workout_data = {
                "workout_name": workout_name,
                "patient_id": patient_id,
                "user_id": user_id,
                "objective": str(objective or "").strip() or None,
                "description": str(description or "").strip() or None,
                "start_date": start_date if has_period else None,
                "end_date": end_date if has_period else None,
            }

            if is_edit:
                saved_id = update_workout_plan(
                    tec_conf,
                    editor_plan_id,
                    workout_data,
                    sessions_payload,
                )
                message = f"Workout '{workout_name}' aggiornato con successo."
            else:
                saved_id = add_workout_plan(
                    tec_conf,
                    workout_data,
                    sessions_payload,
                )
                message = f"Workout '{workout_name}' creato con successo."

            _clear_editor_token(token)
            st.session_state["workout_editor_plan_id"] = str(saved_id)
            st.session_state["workout_flash_message"] = message
            _navigate(LIST_SECTION)
        except Exception as exc:
            logger.error("Errore nel salvataggio workout", exc_info=True)
            st.error(f"Impossibile salvare il workout: {exc}")


# -----------------------------------------------------------------------------
# PROGRESSI / MISURAZIONI
# -----------------------------------------------------------------------------

elif section == PROGRESS_SECTION:
    st.subheader(f"Allenamenti · {patient_name}")
    st.caption(
        "Registra la seduta per blocchi reali di lavoro: gli esercizi di superset, "
        "jump-set, tri-set, giant-set e circuiti vengono mostrati insieme."
    )

    try:
        active_plans = get_workout_plans(tec_conf, patient_id, include_archived=False)
    except Exception as exc:
        st.error(f"Impossibile caricare i workout: {exc}")
        active_plans = []

    if not active_plans:
        st.info("Non ci sono workout attivi per questo assistito.")
        if st.button("➕ Crea un workout"):
            _navigate(EDITOR_SECTION, reset_new=True)
        st.stop()

    plan_by_label = {
        f"{plan['workout_name']} · {_format_plan_period(plan)}": str(plan["id"])
        for plan in active_plans
    }
    plan_ids = list(plan_by_label.values())
    requested_plan_id = str(st.session_state.get("workout_progress_plan_id") or "")
    if requested_plan_id not in plan_ids:
        requested_plan_id = str(_default_training_plan_id(active_plans) or plan_ids[0])
        st.session_state["workout_progress_plan_id"] = requested_plan_id

    plan_index = plan_ids.index(requested_plan_id)
    selected_plan_label = st.selectbox(
        "Workout",
        options=list(plan_by_label.keys()),
        index=plan_index,
        key=f"workout_progress_plan_select_{patient_id}",
    )
    selected_plan_id = plan_by_label[selected_plan_label]
    st.session_state["workout_progress_plan_id"] = selected_plan_id

    try:
        selected_plan = get_workout_plan(tec_conf, patient_id, selected_plan_id)
        workout_history = get_workout_measurements(
            tec_conf,
            patient_id,
            workout_id=selected_plan_id,
        )
    except Exception as exc:
        st.error(f"Impossibile caricare il dettaglio del workout: {exc}")
        st.stop()

    sessions = selected_plan.get("sessions", []) if selected_plan else []
    if not sessions:
        st.info("Il workout selezionato non contiene sessioni attive.")
        st.stop()

    session_labels = [
        f"{int(session.get('session_order') or 0)}. {session.get('session_name')}"
        for session in sessions
    ]
    session_ids = [str(session["id"]) for session in sessions]
    default_session_id = _latest_session_id_from_history(workout_history, sessions)
    default_session_index = (
        session_ids.index(default_session_id)
        if default_session_id in session_ids
        else 0
    )

    selected_session_label = st.selectbox(
        "Tipo di sessione",
        options=session_labels,
        index=default_session_index,
        key=f"workout_progress_session_select_{selected_plan_id}",
    )
    selected_session = sessions[session_labels.index(selected_session_label)]
    workout_session_id = str(selected_session["id"])

    exercises = sorted(
        selected_session.get("exercises", []),
        key=lambda item: int(item.get("exercise_order") or 0),
    )
    if not exercises:
        st.info("La sessione selezionata non contiene esercizi attivi.")
        st.stop()

    session_history = [
        row for row in workout_history
        if str(row.get("workout_session_id")) == workout_session_id
    ]

    context = f"{patient_id}_{selected_plan_id}_{workout_session_id}"
    active_key = f"training_active_{context}"
    execution_key = f"training_execution_id_{context}"
    draft_key = f"training_draft_{context}"
    date_key = f"training_date_{context}"
    time_key = f"training_time_{context}"
    step_index_key = f"training_step_index_{context}"
    copied_previous_key = f"training_copied_previous_{context}"

    # Data/ora sempre visibili subito dopo il tipo di sessione.
    now = datetime.now().replace(microsecond=0)
    if date_key not in st.session_state:
        st.session_state[date_key] = now.date()
    if time_key not in st.session_state:
        st.session_state[time_key] = now.time()

    date_col, time_col = st.columns(2)
    with date_col:
        measure_date = st.date_input("Data allenamento", key=date_key)
    with time_col:
        measure_time = st.time_input("Ora allenamento", key=time_key)
    measurement_datetime = datetime.combine(measure_date, measure_time)

    # -------------------------------------------------------------------------
    # AVVIO NUOVA SESSIONE ALLENANTE
    # -------------------------------------------------------------------------
    if not st.session_state.get(active_key, False):
        latest_execution = _latest_execution_id(session_history)
        if latest_execution:
            history_df = pd.DataFrame(session_history)
            history_df["execution_id"] = history_df["execution_id"].astype(str)
            latest_rows = history_df.loc[history_df["execution_id"] == latest_execution]
            latest_date = pd.to_datetime(latest_rows["measurement_date"], errors="coerce").max()
            if pd.notna(latest_date):
                st.caption(
                    f"Ultima sessione di questo tipo: {latest_date.strftime('%d/%m/%Y %H:%M')} · "
                    "i valori verranno usati come default."
                )
        else:
            st.caption("Prima sessione di questo tipo: i valori performance partiranno vuoti.")

        if st.button(
            "▶️ Inizia sessione",
            type="primary",
            use_container_width=True,
            key=f"start_training_{context}",
        ):
            draft, copied_previous = _build_training_draft(exercises, session_history)
            st.session_state[active_key] = True
            st.session_state[execution_key] = str(uuid4())
            st.session_state[draft_key] = draft
            st.session_state[step_index_key] = 0
            st.session_state[copied_previous_key] = copied_previous
            st.rerun()
    else:
        execution_id = str(st.session_state[execution_key])
        global_training_df = _normalize_training_df(st.session_state.get(draft_key))
        copied_previous = bool(st.session_state.get(copied_previous_key, False))
        steps = _build_training_steps(exercises)

        if copied_previous:
            st.info("Valori precompilati dall'ultima sessione dello stesso tipo.")

        current_index = int(st.session_state.get(step_index_key, 0) or 0)
        current_index = max(0, min(current_index, len(steps) - 1))
        step = steps[current_index]
        safe_step_key = step["key"].replace(":", "_")

        st.progress((current_index + 1) / len(steps))
        st.markdown(f"### {current_index + 1}/{len(steps)} · {step['label']}")

        if step.get("kind") == "multi":
            actual_rounds = _current_step_rounds(global_training_df, step)
            prescribed_rounds = step.get("prescribed_rounds")
            round_text = f"Round effettivi: **{actual_rounds}**"
            if prescribed_rounds:
                round_text += f" · prescritti: **{prescribed_rounds}**"
            st.caption(round_text)
            st.caption(
                "Il blocco viene registrato come un'unica unita: una `Serie` corrisponde "
                "al round del blocco. Usa −/+ Round per modificare insieme tutti gli esercizi."
            )
        elif step.get("technique") in SEGMENTED_TECHNIQUES:
            st.caption(
                "Tecnica intra-esercizio: usa `Segmento` 1, 2, 3... per drop, mini-set o cluster "
                "all'interno della stessa serie."
            )

        # Form = una pressione sulla freccia committa editor + azione di navigazione.
        with st.form(key=f"training_step_form_{context}_{safe_step_key}", clear_on_submit=False):
            edited_slices = []

            for exercise in step["exercises"]:
                exercise_id = str(exercise["id"])
                if len(step["exercises"]) > 1:
                    block_order = _to_int_or_none(exercise.get("block_order"))
                    order_label = f"{block_order}. " if block_order is not None else ""
                    st.markdown(f"#### {order_label}{exercise.get('exercise_name', 'Esercizio')}")
                else:
                    st.markdown(f"#### {exercise.get('exercise_name', 'Esercizio')}")

                prescription = _exercise_prescription(
                    exercise,
                    use_round_label=step.get("kind") == "multi",
                )
                if prescription:
                    st.caption("Prescrizione: " + prescription)

                exercise_slice = global_training_df.loc[
                    global_training_df[TRAINING_EXERCISE_COLUMN].astype(str) == exercise_id
                ].copy()
                if exercise_slice.empty:
                    exercise_slice = _blank_training_rows(exercise)

                # Nei blocchi multi-esercizio i round vengono gestiti in modo coordinato
                # con i bottoni +/-; per gli esercizi singoli lasciamo le righe dinamiche.
                row_mode = "fixed" if step.get("kind") == "multi" else "dynamic"
                edited_slice = st.data_editor(
                    exercise_slice,
                    key=f"training_editor_{context}_{safe_step_key}_{exercise_id}",
                    num_rows=row_mode,
                    hide_index=True,
                    use_container_width=True,
                    column_config={
                        "Serie": st.column_config.NumberColumn(
                            "Round" if step.get("kind") == "multi" else "Serie",
                            min_value=1,
                            step=1,
                            required=True,
                        ),
                        "Segmento": st.column_config.NumberColumn(
                            "Segmento", min_value=1, step=1, required=True,
                            help="Usa 2, 3... per drop set, rest-pause, myo-reps o cluster.",
                        ),
                        "Reps": st.column_config.NumberColumn("Reps", min_value=0, step=1),
                        "Carico kg": st.column_config.NumberColumn(
                            "Carico kg", min_value=0.0, step=0.5, format="%.2f"
                        ),
                        "TUT sec": st.column_config.NumberColumn("TUT sec", min_value=0, step=1),
                        "Recupero sec": st.column_config.NumberColumn(
                            "Recupero sec", min_value=0, step=5
                        ),
                        "Durata sec": st.column_config.NumberColumn("Durata sec", min_value=0, step=1),
                        "RIR": st.column_config.NumberColumn(
                            "RIR", min_value=0.0, max_value=10.0, step=0.5
                        ),
                        "RPE": st.column_config.NumberColumn(
                            "RPE", min_value=0.0, max_value=10.0, step=0.5
                        ),
                        "Note": st.column_config.TextColumn("Note", width="large"),
                        TRAINING_EXERCISE_COLUMN: None,
                        TRAINING_EXERCISE_NAME_COLUMN: None,
                        TRAINING_EXERCISE_ORDER_COLUMN: None,
                        TRAINING_ROW_ID_COLUMN: None,
                    },
                )
                edited_slices.append((exercise, edited_slice))

            previous_requested = False
            next_requested = False
            decrease_round_requested = False
            increase_round_requested = False

            if step.get("kind") == "multi":
                nav_left, round_minus, round_center, round_plus, nav_right = st.columns([1, 1, 2, 1, 1])
                with nav_left:
                    previous_requested = st.form_submit_button(
                        "⬅️",
                        disabled=current_index == 0,
                        use_container_width=True,
                        help="Blocco precedente",
                    )
                with round_minus:
                    decrease_round_requested = st.form_submit_button(
                        "➖",
                        disabled=actual_rounds <= 1,
                        use_container_width=True,
                        help="Rimuovi un round",
                    )
                with round_center:
                    st.markdown(f"<div style='text-align:center;padding-top:8px'><b>{actual_rounds} round</b></div>", unsafe_allow_html=True)
                with round_plus:
                    increase_round_requested = st.form_submit_button(
                        "➕",
                        use_container_width=True,
                        help="Aggiungi un round",
                    )
                with nav_right:
                    next_requested = st.form_submit_button(
                        "➡️",
                        disabled=current_index == len(steps) - 1,
                        use_container_width=True,
                        help="Blocco successivo",
                    )
            else:
                nav_left, nav_center, nav_right = st.columns([1, 5, 1])
                with nav_left:
                    previous_requested = st.form_submit_button(
                        "⬅️",
                        disabled=current_index == 0,
                        use_container_width=True,
                        help="Esercizio/blocco precedente",
                    )
                with nav_center:
                    st.caption("Le frecce salvano le modifiche correnti prima di cambiare step.")
                with nav_right:
                    next_requested = st.form_submit_button(
                        "➡️",
                        disabled=current_index == len(steps) - 1,
                        use_container_width=True,
                        help="Esercizio/blocco successivo",
                    )

            st.divider()
            save_training_requested = st.form_submit_button(
                "✅ Salva sessione allenante",
                type="primary",
                use_container_width=True,
            )

        submitted = any(
            (
                previous_requested,
                next_requested,
                decrease_round_requested,
                increase_round_requested,
                save_training_requested,
            )
        )

        if submitted:
            merged_training_df = _normalize_training_df(
                st.session_state.get(draft_key, global_training_df)
            )
            for exercise, edited_slice in edited_slices:
                merged_training_df = _merge_training_exercise_into_global(
                    merged_training_df,
                    exercise,
                    edited_slice,
                )
            st.session_state[draft_key] = merged_training_df

            if decrease_round_requested:
                st.session_state[draft_key] = _resize_training_step_rounds(
                    merged_training_df,
                    step,
                    actual_rounds - 1,
                )
                st.rerun()

            if increase_round_requested:
                st.session_state[draft_key] = _resize_training_step_rounds(
                    merged_training_df,
                    step,
                    actual_rounds + 1,
                )
                st.rerun()

            if previous_requested:
                st.session_state[step_index_key] = current_index - 1
                st.rerun()

            if next_requested:
                st.session_state[step_index_key] = current_index + 1
                st.rerun()

            if save_training_requested:
                try:
                    measurements_payload = _training_draft_to_payload(
                        merged_training_df,
                        measurement_datetime,
                    )
                    add_workout_session_measurements(
                        tec_conf,
                        patient_id,
                        workout_session_id,
                        measurements_payload,
                        execution_id=execution_id,
                    )
                    _clear_training_state(context)
                    st.session_state["workout_flash_message"] = (
                        f"Sessione '{selected_session.get('session_name')}' registrata con successo."
                    )
                    st.rerun()
                except Exception as exc:
                    logger.error("Errore registrazione sessione allenante", exc_info=True)
                    st.error(f"Impossibile registrare la sessione: {exc}")

        if st.button(
            "Annulla sessione in corso",
            key=f"cancel_training_{context}",
            use_container_width=True,
        ):
            _clear_training_state(context)
            st.rerun()

    # -------------------------------------------------------------------------
    # STORICO SESSIONI ALLENANTI - VISUALIZZAZIONE + EDIT
    # -------------------------------------------------------------------------
    st.divider()
    st.markdown("### Storico sessioni allenanti")

    if not session_history:
        st.caption("Nessuna sessione allenante registrata per questo tipo di sessione.")
    else:
        history_df = pd.DataFrame(session_history).copy()
        history_df["execution_id"] = history_df["execution_id"].astype(str)
        history_df["measurement_date"] = pd.to_datetime(
            history_df["measurement_date"], errors="coerce"
        )
        execution_order = (
            history_df.groupby("execution_id")["measurement_date"]
            .max()
            .sort_values(ascending=False)
            .index
            .tolist()
        )

        for execution_history_id in execution_order:
            execution_rows = history_df.loc[
                history_df["execution_id"] == execution_history_id
            ].copy()
            execution_date = execution_rows["measurement_date"].max()
            exercise_count = execution_rows["workout_exercise_id"].astype(str).nunique()
            when_label = (
                execution_date.strftime("%d/%m/%Y %H:%M")
                if pd.notna(execution_date)
                else "Data non disponibile"
            )

            with st.expander(
                f"{when_label} · {exercise_count} esercizi · {execution_history_id[:8]}",
                expanded=False,
            ):
                history_date_key = f"history_date_{execution_history_id}"
                history_time_key = f"history_time_{execution_history_id}"
                if history_date_key not in st.session_state:
                    safe_dt = (
                        execution_date.to_pydatetime()
                        if pd.notna(execution_date)
                        else datetime.now().replace(microsecond=0)
                    )
                    st.session_state[history_date_key] = safe_dt.date()
                    st.session_state[history_time_key] = safe_dt.time().replace(microsecond=0)

                hist_date_col, hist_time_col = st.columns(2)
                with hist_date_col:
                    history_date = st.date_input("Data", key=history_date_key)
                with hist_time_col:
                    history_time = st.time_input("Ora", key=history_time_key)

                history_editor_df = _history_editor_dataframe(execution_rows)
                edited_history_df = st.data_editor(
                    history_editor_df,
                    key=f"history_editor_{execution_history_id}",
                    num_rows="fixed",
                    hide_index=True,
                    use_container_width=True,
                    disabled=["Esercizio"],
                    column_config={
                        "Esercizio": st.column_config.TextColumn("Esercizio", width="medium"),
                        "Serie": st.column_config.NumberColumn("Serie / Round", min_value=1, step=1),
                        "Segmento": st.column_config.NumberColumn("Segmento", min_value=1, step=1),
                        "Reps": st.column_config.NumberColumn("Reps", min_value=0, step=1),
                        "Carico kg": st.column_config.NumberColumn(
                            "Carico kg", min_value=0.0, step=0.5, format="%.2f"
                        ),
                        "TUT sec": st.column_config.NumberColumn("TUT sec", min_value=0, step=1),
                        "Recupero sec": st.column_config.NumberColumn("Recupero sec", min_value=0, step=5),
                        "Durata sec": st.column_config.NumberColumn("Durata sec", min_value=0, step=1),
                        "RIR": st.column_config.NumberColumn("RIR", min_value=0.0, max_value=10.0, step=0.5),
                        "RPE": st.column_config.NumberColumn("RPE", min_value=0.0, max_value=10.0, step=0.5),
                        "Note": st.column_config.TextColumn("Note", width="large"),
                        "__measurement_id": None,
                        "__exercise_id": None,
                        "__exercise_order": None,
                    },
                )

                if st.button(
                    "💾 Salva modifiche storico",
                    key=f"save_history_{execution_history_id}",
                    use_container_width=True,
                ):
                    try:
                        history_datetime = datetime.combine(history_date, history_time)
                        history_payload = _history_editor_to_payload(
                            edited_history_df,
                            history_datetime,
                        )
                        update_workout_measurements_batch(
                            tec_conf,
                            patient_id,
                            history_payload,
                        )
                        st.session_state["workout_flash_message"] = (
                            "Storico allenamento aggiornato."
                        )
                        st.rerun()
                    except Exception as exc:
                        logger.error("Errore aggiornamento storico workout", exc_info=True)
                        st.error(f"Impossibile aggiornare lo storico: {exc}")

