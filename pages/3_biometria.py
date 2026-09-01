import logging
from datetime import date, datetime

import pandas as pd
import streamlit as st

from Backend.services.patient_service import get_all_patients
from Backend.services.biometrics_service import (
    add_biometric_record,
    get_biometrics_history,
    calcola_bf_jackson_pollock_7,
    calcola_proporzioni_auree_bodybuilding,
)
from Backend.services.diet_service import get_diet_plans
from Backend.services.workout_service import get_workout_plans


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("BiometriaApp")

tec_conf = st.session_state.get("tec_conf", {})

st.title("Biometria 📈")
st.caption(
    "Ogni misurazione descrive anche il periodo trascorso dalla precedente: "
    "alimentazione seguita e, quando presente, workout svolto."
)
logger.info("Avvio modulo Biometria - Pagina caricata.")

if "user_id" not in st.session_state:
    st.session_state.user_id = "00000000-0000-0000-0000-000000000000"
    logger.warning("user_id non trovato in session_state. Applicato ID di default predefinito.")

user_id = st.session_state.user_id


NUTRITION_LABELS = {
    "INITIAL": "Prima misurazione / baseline",
    "DIET_PLAN": "Piano alimentare",
    "FREE_DIET": "Dieta libera",
    "STOP": "Periodo di stop",
}

WORKOUT_LABELS = {
    "INITIAL": "Prima misurazione / baseline",
    "WORKOUT_PLAN": "Piano workout",
    "NO_WORKOUT": "Nessun workout",
    "STOP": "Periodo di stop",
}


FORMAL_MEASUREMENT_FIELDS = (
    "bf_percent",
    "circ_vita_cm",
    "circ_fianchi_cm",
    "circ_torace_cm",
    "circ_spalle_cm",
    "circ_collo_cm",
    "circ_polsi_cm",
    "circ_braccia_cm",
    "circ_avambracci_cm",
    "circ_coscia_cm",
    "circ_polpacci_cm",
    "pliche_petto_mm",
    "pliche_addome_mm",
    "pliche_coscia_mm",
    "pliche_tricipite_mm",
    "pliche_sovrascapolare_mm",
    "pliche_sovrailiaca_mm",
    "pliche_ascellare_mm",
)


def _as_date(value):
    if value is None or value == "":
        return None
    try:
        return pd.to_datetime(value).date()
    except Exception:
        return None


def _is_formal_measurement(row: dict) -> bool:
    """
    Distingue una vera visita biometrica dai record peso-only creati da altri flussi
    (es. creazione assistito/ricalcolo TDEE).
    """
    if row.get("nutrition_context") or row.get("workout_context"):
        return True
    return any(row.get(field) is not None for field in FORMAL_MEASUREMENT_FIELDS)


def _previous_formal_measurement(history: list[dict], current_date: date):
    candidates = []
    for row in history or []:
        row_date = _as_date(row.get("insert_date"))
        if row_date and row_date < current_date and _is_formal_measurement(row):
            candidates.append((row_date, row))
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def _nutrition_context_label(row: dict) -> str:
    context = row.get("nutrition_context")
    if context == "DIET_PLAN":
        return row.get("diet_plan_name") or "Piano alimentare (non disponibile)"
    return NUTRITION_LABELS.get(context, "Non specificato")


def _workout_context_label(row: dict) -> str:
    context = row.get("workout_context")
    if context == "WORKOUT_PLAN":
        return row.get("workout_plan_name") or "Piano workout (non disponibile)"
    return WORKOUT_LABELS.get(context, "Non specificato")


def _format_period(row: dict) -> str:
    start = _as_date(row.get("context_start_date"))
    end = _as_date(row.get("insert_date"))
    if row.get("nutrition_context") == "INITIAL":
        return "Baseline iniziale"
    if start and end:
        return f"{start.strftime('%d/%m/%Y')} → {end.strftime('%d/%m/%Y')}"
    return "Non Specificato"


try:
    logger.info("Recupero lista pazienti per l'utente ID: %s", user_id)
    pazienti = get_all_patients(tec_conf, user_id)
except Exception as exc:
    logger.error("Errore nel recupero della lista pazienti: %s", exc, exc_info=True)
    st.error(f"Errore nel recupero della lista pazienti: {exc}")
    pazienti = []

if not pazienti:
    st.warning("Nessun paziente presente nel database. Aggiungi prima un paziente nella sezione 'Pazienti'.")
    st.stop()

pazienti_dict = {
    f"{p['cognome']} {p['nome']} ({p.get('data_nascita') or 'data N/D'})": p["id"]
    for p in pazienti
}

paziente_selezionato_label = st.selectbox(
    "Seleziona il paziente per la misurazione o lo storico",
    options=list(pazienti_dict.keys()),
)

current_patient_id = pazienti_dict[paziente_selezionato_label]
paziente_obj = next((p for p in pazienti if p["id"] == current_patient_id), {})
logger.info("Paziente selezionato: %s [ID: %s]", paziente_selezionato_label, current_patient_id)

# Carichiamo una volta i dati necessari alla pagina.
try:
    biometrics_history = get_biometrics_history(tec_conf, current_patient_id)
except Exception as exc:
    logger.error("Errore nel recupero dello storico biometrico: %s", exc, exc_info=True)
    biometrics_history = []
    st.error(f"Errore nel recupero dello storico biometrico: {exc}")

try:
    diet_plans = get_diet_plans(tec_conf, current_patient_id)
except Exception as exc:
    logger.error("Errore nel recupero dei piani alimentari: %s", exc, exc_info=True)
    diet_plans = []
    st.warning(f"Impossibile caricare i piani alimentari: {exc}")

try:
    # Include gli archiviati: una misurazione può riferirsi a un workout appena concluso.
    workout_plans = get_workout_plans(tec_conf, current_patient_id, include_archived=True)
except Exception as exc:
    logger.error("Errore nel recupero dei workout: %s", exc, exc_info=True)
    workout_plans = []
    st.warning(f"Impossibile caricare i workout: {exc}")

st.divider()

# -----------------------------------------------------------------------------
# RECAP ULTIMA MISURAZIONE STRUTTURATA
# -----------------------------------------------------------------------------
st.subheader("📋 Ultima Misurazione Registrata")
formal_measurements = [row for row in biometrics_history if _is_formal_measurement(row)]
formal_measurements.sort(
    key=lambda row: (_as_date(row.get("insert_date")) or date.min, str(row.get("id") or "")),
    reverse=True,
)
ultima_misurazione = formal_measurements[0] if formal_measurements else None

if ultima_misurazione:
    timestamp_misurazione = ultima_misurazione.get("insert_date", "Data non disponibile")
    st.caption(f"Registrata in data: {timestamp_misurazione}")

    col_m1, col_m2, col_m3, col_m4, col_m5 = st.columns(5)
    with col_m1:
        peso_prec = ultima_misurazione.get("peso_kg")
        st.metric("Peso", f"{peso_prec} kg" if peso_prec is not None else "N/D")
    with col_m2:
        bf_prec = ultima_misurazione.get("bf_percent")
        st.metric("Massa Grassa (BF)", f"{bf_prec}%" if bf_prec is not None else "N/D")
    with col_m3:
        vita_prec = ultima_misurazione.get("circ_vita_cm")
        st.metric("Circonferenza Vita", f"{vita_prec} cm" if vita_prec is not None else "N/D")
    with col_m4:
        torace_prec = ultima_misurazione.get("circ_torace_cm")
        st.metric("Circonferenza Torace", f"{torace_prec} cm" if torace_prec is not None else "N/D")
    with col_m5:
        spalle_prec = ultima_misurazione.get("circ_spalle_cm")
        st.metric("Circonferenza Spalle", f"{spalle_prec} cm" if spalle_prec is not None else "N/D")

    st.markdown("#### Contesto del periodo precedente")
    ctx1, ctx2, ctx3 = st.columns(3)
    ctx1.metric("Periodo", _format_period(ultima_misurazione))
    ctx2.metric("Alimentazione", _nutrition_context_label(ultima_misurazione))
    ctx3.metric("Workout", _workout_context_label(ultima_misurazione))

    with st.expander("Visualizza dettaglio completo dell'ultima misurazione"):
        dettagli_filtrati = {
            k: str(v)
            for k, v in ultima_misurazione.items()
            if v is not None
            and k not in {
                "id",
                "patient_id",
                "diet_plan_id",
                "workout_plan_id",
                "created_at",
            }
        }
        if dettagli_filtrati:
            st.dataframe(
                pd.DataFrame(list(dettagli_filtrati.items()), columns=["Parametro", "Valore"]),
                use_container_width=True,
                hide_index=True,
            )
else:
    st.info("Nessuna misurazione precedente trovata per questo paziente.")

st.divider()

tab_nuova, tab_proporzioni, tab_storico = st.tabs(
    ["Nuova Misurazione", "Proporzioni Classiche & Auree", "Storico e Trend"]
)

# -----------------------------------------------------------------------------
# NUOVA MISURAZIONE
# -----------------------------------------------------------------------------
with tab_nuova:
    st.subheader(f"Registra misurazione per: {paziente_selezionato_label}")

    data_rif = st.date_input(
        label="Data Riferimento",
        max_value=date.today(),
        value=date.today(),
        key="biometrics_reference_date",
    )

    previous_measurement = _previous_formal_measurement(biometrics_history, data_rif)
    is_initial_measurement = previous_measurement is None
    previous_date = _as_date(previous_measurement.get("insert_date")) if previous_measurement else None

    if is_initial_measurement:
        st.info(
            "Questa è la prima misurazione biometrica strutturata precedente alla data selezionata. "
            "Verrà registrata come **baseline iniziale**, senza riferimenti obbligatori a dieta o workout."
        )
    else:
        st.info(
            "La dieta e il workout selezionati devono descrivere ciò che l'assistito ha effettivamente "
            f"seguito nel periodo **{previous_date.strftime('%d/%m/%Y')} → {data_rif.strftime('%d/%m/%Y')}**."
        )

    eta_paziente = 30
    if paziente_obj.get("data_nascita"):
        try:
            nascita = pd.to_datetime(paziente_obj["data_nascita"])
            eta_paziente = int((datetime.combine(data_rif, datetime.min.time()) - nascita).days // 365.25)
        except Exception as exc:
            logger.warning("Impossibile calcolare l'età: %s", exc)

    # FIX: il sesso va letto dall'oggetto paziente, non dal dizionario label->id.
    sesso_paziente = paziente_obj.get("sesso") or "Maschio"

    diet_options = {str(plan["id"]): plan["diet_name"] for plan in diet_plans}
    workout_options = {}
    for plan in workout_plans:
        label = plan["workout_name"]
        if plan.get("archived_at"):
            label += " [archiviato]"
        workout_options[str(plan["id"])] = label

    with st.form("new_biometrics_form"):
        st.write("### Periodo precedente alla misurazione")

        if is_initial_measurement:
            nutrition_context = "INITIAL"
            workout_context = "INITIAL"
            selected_diet_plan_id = None
            selected_workout_plan_id = None

            cctx1, cctx2 = st.columns(2)
            cctx1.text_input("Alimentazione", value=NUTRITION_LABELS["INITIAL"], disabled=True)
            cctx2.text_input("Workout", value=WORKOUT_LABELS["INITIAL"], disabled=True)
        else:
            cctx1, cctx2 = st.columns(2)

            with cctx1:
                nutrition_choice = st.selectbox(
                    "Alimentazione seguita *",
                    options=["DIET_PLAN", "FREE_DIET", "STOP"],
                    format_func=lambda value: NUTRITION_LABELS[value],
                    help=(
                        "Scegli il piano effettivamente seguito nel periodo precedente. "
                        "Usa Dieta libera o Stop quando non era attivo un piano alimentare strutturato."
                    ),
                )
                nutrition_context = nutrition_choice
                selected_diet_plan_id = None

                if nutrition_context == "DIET_PLAN":
                    if diet_options:
                        selected_diet_plan_id = st.selectbox(
                            "Piano alimentare *",
                            options=list(diet_options.keys()),
                            format_func=lambda value: diet_options[value],
                        )
                    else:
                        st.warning(
                            "Non risultano piani alimentari per questo assistito. "
                            "Se il periodo non prevedeva una dieta strutturata seleziona Dieta libera o Stop."
                        )

            with cctx2:
                workout_choice = st.selectbox(
                    "Workout seguito",
                    options=["NO_WORKOUT", "WORKOUT_PLAN", "STOP"],
                    format_func=lambda value: WORKOUT_LABELS[value],
                    help="Il workout è opzionale. Distingui Nessun workout da un vero Periodo di stop.",
                )
                workout_context = workout_choice
                selected_workout_plan_id = None

                if workout_context == "WORKOUT_PLAN":
                    if workout_options:
                        selected_workout_plan_id = st.selectbox(
                            "Piano workout *",
                            options=list(workout_options.keys()),
                            format_func=lambda value: workout_options[value],
                        )
                    else:
                        st.warning(
                            "Non risultano workout per questo assistito. "
                            "Se non si è allenato seleziona Nessun workout o Periodo di stop."
                        )

        period_context_notes = st.text_area(
            "Note sul periodo",
            placeholder=(
                "Facoltativo: aderenza, giorni di stop, ferie, dieta libera parziale, "
                "variazioni non formalizzate nel piano, ecc."
            ),
        )

        st.divider()
        st.write("### Parametri Principali")
        col1, col2 = st.columns(2)
        with col1:
            peso = st.number_input("Peso (kg)", min_value=0.0, max_value=250.0, step=0.1, format="%.1f", key="form_peso")
            circ_vita = st.number_input("Circonferenza Vita (cm)", min_value=0.0, max_value=200.0, step=0.5, format="%.1f", key="form_circ_vita")
            circ_fianchi = st.number_input("Fianchi (cm)", min_value=0.0, step=0.5, format="%.1f", key="form_circ_fianchi")
            circ_torace = st.number_input("Torace (cm)", min_value=0.0, step=0.5, format="%.1f", key="form_circ_torace")
            circ_collo = st.number_input("Collo (cm)", min_value=0.0, step=0.5, format="%.1f", key="form_circ_collo")
            circ_polsi = st.number_input("Polsi (cm)", min_value=0.0, step=0.5, format="%.1f", key="form_circ_polsi")
        with col2:
            circ_spalle = st.number_input("Spalle (cm)", min_value=0.0, step=0.5, format="%.1f", key="form_circ_spalle")
            circ_braccia = st.number_input("Braccia (cm)", min_value=0.0, step=0.5, format="%.1f", key="form_circ_braccia")
            circ_coscia = st.number_input("Coscia (cm)", min_value=0.0, step=0.5, format="%.1f", key="form_circ_coscia")
            circ_polpacci = st.number_input("Polpacci (cm)", min_value=0.0, step=0.5, format="%.1f", key="form_circ_polpacci")
            circ_avambracci = st.number_input("Avambracci (cm)", min_value=0.0, step=0.5, format="%.1f", key="form_circ_avambracci")

        with st.expander("Calcolo BF % (Jackson-Pollock 7 Pliche)"):
            st.info(f"Età assistito alla data della misurazione: **{eta_paziente} anni**. Sesso: **{sesso_paziente}**.")
            p7_1, p7_2, p7_3 = st.columns(3)
            with p7_1:
                p_petto = st.number_input("Petto (mm)", min_value=0.0, step=0.5, format="%.1f")
                p_addome = st.number_input("Addome (mm)", min_value=0.0, step=0.5, format="%.1f")
                p_coscia = st.number_input("Coscia (mm)", min_value=0.0, step=0.5, format="%.1f")
            with p7_2:
                p_tricipite = st.number_input("Tricipite (mm)", min_value=0.0, step=0.5, format="%.1f")
                p_sovrascapolare = st.number_input("Sovrascapolare (mm)", min_value=0.0, step=0.5, format="%.1f")
            with p7_3:
                p_sovrailiaca = st.number_input("Sovrailiaca (mm)", min_value=0.0, step=0.5, format="%.1f")
                p_ascellare = st.number_input("Ascellare (mm)", min_value=0.0, step=0.5, format="%.1f")

            pliche_dict = {
                "pliche_petto_mm": p_petto,
                "pliche_addome_mm": p_addome,
                "pliche_coscia_mm": p_coscia,
                "pliche_tricipite_mm": p_tricipite,
                "pliche_sovrascapolare_mm": p_sovrascapolare,
                "pliche_sovrailiaca_mm": p_sovrailiaca,
                "pliche_ascellare_mm": p_ascellare,
            }
            try:
                bf_calcolato = calcola_bf_jackson_pollock_7(
                    sesso_paziente,
                    eta_paziente,
                    pliche_dict,
                )
                if bf_calcolato is not None:
                    st.success(f"BF Calcolato: **{bf_calcolato}%**")
                else:
                    st.warning("Compila tutte le 7 pliche con valori maggiori di zero.")
            except Exception as exc:
                logger.error("Errore nel calcolo BF: %s", exc, exc_info=True)
                bf_calcolato = None
                st.error(f"Errore nel calcolo della BF: {exc}")

        submitted = st.form_submit_button("Registra Valori", type="primary")

        if submitted:
            validation_errors = []

            if bf_calcolato is None:
                validation_errors.append("Compila tutte le 7 pliche per calcolare la BF prima del salvataggio.")

            if not is_initial_measurement:
                if nutrition_context == "DIET_PLAN" and not selected_diet_plan_id:
                    validation_errors.append("Seleziona il piano alimentare seguito oppure indica Dieta libera/Stop.")
                if workout_context == "WORKOUT_PLAN" and not selected_workout_plan_id:
                    validation_errors.append("Seleziona il workout seguito oppure indica Nessun workout/Stop.")

            if validation_errors:
                for error in validation_errors:
                    st.error(error)
            else:
                biometric_data = {
                    "patient_id": current_patient_id,
                    "insert_date": data_rif,
                    "context_start_date": previous_date,
                    "nutrition_context": nutrition_context,
                    "diet_plan_id": selected_diet_plan_id,
                    "workout_context": workout_context,
                    "workout_plan_id": selected_workout_plan_id,
                    "period_context_notes": period_context_notes.strip() or None,
                    "peso_kg": peso if peso > 0 else None,
                    "circ_vita_cm": circ_vita if circ_vita > 0 else None,
                    "circ_fianchi_cm": circ_fianchi if circ_fianchi > 0 else None,
                    "circ_torace_cm": circ_torace if circ_torace > 0 else None,
                    "circ_spalle_cm": circ_spalle if circ_spalle > 0 else None,
                    "circ_collo_cm": circ_collo if circ_collo > 0 else None,
                    # Naming riallineato al DDL.
                    "circ_polsi_cm": circ_polsi if circ_polsi > 0 else None,
                    "circ_braccia_cm": circ_braccia if circ_braccia > 0 else None,
                    "circ_avambracci_cm": circ_avambracci if circ_avambracci > 0 else None,
                    "circ_coscia_cm": circ_coscia if circ_coscia > 0 else None,
                    "circ_polpacci_cm": circ_polpacci if circ_polpacci > 0 else None,
                    "bf_percent": bf_calcolato,
                    "algoritmo_bf_usato": "Jackson-Pollock 7 + Siri",
                    "pliche_petto_mm": p_petto if p_petto > 0 else None,
                    "pliche_addome_mm": p_addome if p_addome > 0 else None,
                    "pliche_coscia_mm": p_coscia if p_coscia > 0 else None,
                    "pliche_tricipite_mm": p_tricipite if p_tricipite > 0 else None,
                    "pliche_sovrascapolare_mm": p_sovrascapolare if p_sovrascapolare > 0 else None,
                    "pliche_sovrailiaca_mm": p_sovrailiaca if p_sovrailiaca > 0 else None,
                    "pliche_ascellare_mm": p_ascellare if p_ascellare > 0 else None,
                }
                biometric_data = {k: v for k, v in biometric_data.items() if v is not None}

                try:
                    add_biometric_record(tec_conf, biometric_data)
                    st.success("Misurazione e contesto del periodo registrati con successo!")
                    st.rerun()
                except Exception as exc:
                    logger.error("Errore durante il salvataggio della misurazione: %s", exc, exc_info=True)
                    st.error(f"Errore durante il salvataggio della misurazione: {exc}")

with tab_proporzioni:
    st.subheader("🏛️ Analisi Comparativa Proporzioni Classiche & Auree (Multi-Metodo & Storico)")
    st.write("Filtra per periodo e confronta i valori attuali con la misurazione iniziale e i target ideali.")
    
    try:
        logger.info(f"Recupero storico proporzioni per paziente ID: {current_patient_id}")
        history_proporzioni = [
            row
            for row in get_biometrics_history(tec_conf, current_patient_id)
            if _is_formal_measurement(row)
        ]
    except Exception as e:
        logger.error(f"Errore nel recupero dello storico proporzioni per paziente ID {current_patient_id}: {e}", exc_info=True)
        history_proporzioni = []
        st.error(f"Errore nel recupero dei dati biometrici: {e}")
        
    if not history_proporzioni:
        st.warning("⚠️ Nessuna misurazione trovata nel database per questo paziente. Registra prima almeno una misurazione nella tab 'Nuova Misurazione'.")
    else:
        df_storico_completo = pd.DataFrame(history_proporzioni)
        
        # 1. Normalizzazione colonna temporale
        date_col = 'insert_date' if 'insert_date' in df_storico_completo.columns else 'timestamp'
        if date_col not in df_storico_completo.columns:
            st.error("Errore: nessuna colonna temporale trovata nel DataFrame.")
            st.stop()
            
        df_storico_completo['data_rif'] = pd.to_datetime(df_storico_completo[date_col], errors='coerce')
        df_valid_dates = df_storico_completo.dropna(subset=['data_rif'])

        min_db_date = df_valid_dates['data_rif'].min().date() if not df_valid_dates.empty else date.today()
        max_db_date = df_valid_dates['data_rif'].max().date() if not df_valid_dates.empty else date.today()

        st.write("### 📅 Filtro Periodo")
        col_d1, col_d2 = st.columns(2)
        with col_d1:
            data_inizio_filtro = st.date_input("Data Inizio", value=min_db_date, key="filtro_data_inizio")
        with col_d2:
            data_fine_filtro = st.date_input("Data Fine", value=max_db_date, key="filtro_data_fine")
            
        mask = (
            df_storico_completo['data_rif'].notna() & 
            (df_storico_completo['data_rif'].dt.date >= data_inizio_filtro) & 
            (df_storico_completo['data_rif'].dt.date <= data_fine_filtro)
        )
        df_storico_filtrato = df_storico_completo.loc[mask].copy()
        
        if df_storico_filtrato.empty:
            st.warning("⚠️ Nessuna misurazione trovata nell'intervallo di date selezionato.")
        else:
            df_storico_crescente = df_storico_filtrato.sort_values('data_rif', ascending=True).reset_index(drop=True)
            df_storico_decrescente = df_storico_filtrato.sort_values('data_rif', ascending=False).reset_index(drop=True)
            
            opzioni_date = [
                f"Del {row['data_rif'].strftime('%d/%m/%Y %H:%M')} (ID: {row.get('id', i)})" 
                for i, row in df_storico_decrescente.iterrows()
            ]
            
            scelta_misura = st.selectbox("Seleziona la rilevazione corrente da analizzare (nel periodo)", options=opzioni_date, key="seleziona_rilevazione_proporzioni")
            
            indice_scelto_decresc = opzioni_date.index(scelta_misura)
            riga_corrente_originale = df_storico_decrescente.iloc[indice_scelto_decresc]

            riga_iniziale = df_storico_crescente.iloc[0].to_dict()
            ultima_rilevazione = riga_corrente_originale.to_dict()
            
            st.caption(f"📅 Rilevazione analizzata: **{ultima_rilevazione.get('data_rif', 'N/D')}** | "
                       f"Iniziale (periodo): **{riga_iniziale.get('data_rif', 'N/D')}**")
            
            valore_altezza = paziente_obj.get('altezza_cm') if paziente_obj else None
            if valore_altezza:
                st.write(f"Altezza assistito: **{valore_altezza} cm**")

            # Mappatura flessibile con varianti di nome colonna DB comuni
            mappatura_chiavi_db = {
                "Polso": ["circ_polsi_cm", "circ_polso_cm", "polso"],
                "Vita": ["circ_vita_cm", "vita"],
                "Torace": ["circ_torace_cm", "torace"],
                "Fianchi / Bacino": ["circ_fianchi_cm", "fianchi", "bacino"],
                "Coscia": ["circ_coscia_cm", "coscia"],
                "Braccio": ["circ_braccia_cm", "circ_braccio_cm", "braccio"],
                "Avambraccio": ["circ_avambracci_cm", "circ_avambraccio_cm", "avambraccio"],
                "Polpaccio": ["circ_polpacci_cm", "circ_polpaccio_cm", "polpaccio"],
                "Collo": ["circ_collo_cm", "collo"],
                "Spalle": ["circ_spalle_cm", "spalle"]
            }

            def estrai_valore(dizionario_dati, lista_chiavi):
                if not dizionario_dati:
                    return None
                for k in lista_chiavi:
                    val = dizionario_dati.get(k)
                    if val is not None and pd.notna(val):
                        try:
                            v_float = float(val)
                            if v_float > 0:
                                return v_float
                        except ValueError:
                            continue
                return None

            valori_correnti = {}
            misurazioni_per_backend = {}
            for etichetta, varianti in mappatura_chiavi_db.items():
                val = estrai_valore(ultima_rilevazione, varianti)
                valori_correnti[etichetta] = val
                misurazioni_per_backend[varianti[0]] = val

            alt_param = float(valore_altezza) if valore_altezza and float(valore_altezza) > 0 else None
            st.divider()

            # --- FUNZIONE UNIVERSALE PER LA RENDERIZZAZIONE TABELLA (Solo Attuale & Iniziale) ---
            def renderizza_tabella_comparativa(risultati_backend, riga_corrente_dict, riga_iniziale_dict, valori_correnti_map, mappatura_chiavi_db):
                righe_tabella = []
                comparazione = risultati_backend.get('comparazione', [])
                
                # 1. RIGA INTESTAZIONE DATE
                fmt_data = lambda d: pd.to_datetime(d.get('data_rif')).strftime('%d/%m/%Y') if d and d.get('data_rif') and pd.notna(d.get('data_rif')) else "N/D"
                
                data_attuale_str = fmt_data(riga_corrente_dict)
                data_iniziale_str = fmt_data(riga_iniziale_dict) if riga_iniziale_dict else data_attuale_str
                
                # Colonne: Parametro, Target Ideale, Attuale, Iniziale
                righe_tabella.append({
                    "Parametro": "📅 Data Misurazione",
                    "Target Ideale": "-",
                    "Attuale": data_attuale_str,
                    "Iniziale": data_iniziale_str
                })
                
                # 2. CICLO SUI PARAMETRI DELLA COMPARAZIONE
                for item in comparazione:
                    nome_parametro = str(item.get('Distretto', 'Sconosciuto'))
                    val_target = item.get('Target Ideale (cm)')
                    val_attuale = item.get('Misura Reale (cm)')
                    
                    if val_attuale is None:
                        for etichetta, varianti in mappatura_chiavi_db.items():
                            if etichetta.lower() in nome_parametro.lower():
                                val_attuale = valori_correnti_map.get(etichetta)
                                break

                    chiavi_db_associate = []
                    for etichetta, varianti in mappatura_chiavi_db.items():
                        if etichetta.lower() in nome_parametro.lower() or any(v.lower() in nome_parametro.lower() for v in varianti):
                            chiavi_db_associate = varianti
                            break

                    val_iniziale = estrai_valore(riga_iniziale_dict, chiavi_db_associate) if (riga_iniziale_dict and chiavi_db_associate) else None

                    if val_iniziale is None and val_attuale is not None:
                        val_iniziale = val_attuale

                    fmt_val = lambda v: f"{float(v):.1f}" if (v is not None and pd.notna(v) and str(v) != "-") else "-"
                    
                    righe_tabella.append({
                        "Parametro": nome_parametro,
                        "Target Ideale": fmt_val(val_target),
                        "Attuale": fmt_val(val_attuale),
                        "Iniziale": fmt_val(val_iniziale)
                    })
                    
                st.dataframe(pd.DataFrame(righe_tabella), use_container_width=True, hide_index=True)

            # --- METODO POLSO ---
            st.markdown("### 🔵 Modello Basato sul Polso")
            campi_richiesti_polso = ["Polso", "Torace", "Fianchi / Bacino", "Coscia", "Braccio", "Avambraccio", "Polpaccio", "Collo"]
            mancanti_polso = [c for c in campi_richiesti_polso if valori_correnti.get(c) is None]

            if mancanti_polso:
                st.error(f"❌ Impossibile calcolare il modello a causa di dati mancanti nella misurazione scelta: **{', '.join(mancanti_polso)}**")
            else:
                try:
                    ris_polso = calcola_proporzioni_auree_bodybuilding(misurazioni=misurazioni_per_backend, tipo_riferimento="polso", altezza_cm=alt_param)
                    st.caption(f"Valore base (Polso): **{ris_polso.get('valore_base', '-')} cm**")
                    renderizza_tabella_comparativa(
                        risultati_backend=ris_polso,
                        riga_corrente_dict=ultima_rilevazione,
                        riga_iniziale_dict=riga_iniziale,
                        valori_correnti_map=valori_correnti,
                        mappatura_chiavi_db=mappatura_chiavi_db
                    )
                except Exception as err:
                    logger.error(f"Errore nel calcolo delle proporzioni (Polso): {err}", exc_info=True)
                    st.error(f"Errore nel calcolo (Polso): {err}")

            st.divider()

            # --- METODO VITA ---
            st.markdown("### 🟢 Modello Basato sulla Vita")
            campi_richiesti_vita = ["Vita", "Torace", "Spalle", "Coscia"]
            mancanti_vita = [c for c in campi_richiesti_vita if valori_correnti.get(c) is None]

            if mancanti_vita:
                st.error(f"❌ Impossibile calcolare il modello a causa di dati mancanti nella misurazione scelta: **{', '.join(mancanti_vita)}**")
            else:
                try:
                    ris_vita = calcola_proporzioni_auree_bodybuilding(misurazioni=misurazioni_per_backend, tipo_riferimento="vita", altezza_cm=alt_param)
                    st.caption(f"Valore base (Vita): **{ris_vita.get('valore_base', '-')} cm**")
                    renderizza_tabella_comparativa(
                        risultati_backend=ris_vita,
                        riga_corrente_dict=ultima_rilevazione,
                        riga_iniziale_dict=riga_iniziale,
                        valori_correnti_map=valori_correnti,
                        mappatura_chiavi_db=mappatura_chiavi_db
                    )
                except Exception as err:
                    logger.error(f"Errore nel calcolo delle proporzioni (Vita): {err}", exc_info=True)
                    st.error(f"Errore nel calcolo (Vita): {err}")

with tab_storico:
    st.subheader(f"Storico per: {paziente_selezionato_label.split(' (ID:')[0]}")
    
    try:
        logger.info(f"Caricamento completo storico grafico e tabellare per paziente ID: {current_patient_id}")
        history = get_biometrics_history(tec_conf, current_patient_id)
    except Exception as e:
        logger.error(f"Errore nel recupero dello storico completo per paziente ID {current_patient_id}: {e}", exc_info=True)
        st.error(f"Errore nel recupero dello storico: {e}")
        history = []
    
    if history:
        df = pd.DataFrame(history)
        
        date_col = 'insert_date' if 'insert_date' in df.columns else ('timestamp' if 'timestamp' in df.columns else None)
        if date_col:
            df[date_col] = pd.to_datetime(df[date_col], errors='coerce')
            df = df.sort_values(date_col)
            df_chart = df.dropna(subset=[date_col]).set_index(date_col)

            if 'peso_kg' in df_chart.columns and df_chart['peso_kg'].notna().any():
                st.write("### Andamento Peso (kg)")
                st.line_chart(df_chart['peso_kg'])

            if 'bf_percent' in df_chart.columns and df_chart['bf_percent'].notna().any():
                st.write("### Andamento Massa Grassa (BF %)")
                st.line_chart(df_chart['bf_percent'])
        
        st.write("### Dettaglio Tabellare")

        df_display = df.copy()
        if not df_display.empty:
            df_display["Periodo precedente"] = [
                _format_period(row) for row in df_display.to_dict("records")
            ]
            df_display["Alimentazione"] = [
                _nutrition_context_label(row) for row in df_display.to_dict("records")
            ]
            df_display["Workout"] = [
                _workout_context_label(row) for row in df_display.to_dict("records")
            ]

            preferred_columns = [
                "insert_date",
                "Periodo precedente",
                "Alimentazione",
                "Workout",
                "peso_kg",
                "bf_percent",
                "circ_vita_cm",
                "circ_fianchi_cm",
                "circ_torace_cm",
                "circ_spalle_cm",
                "period_context_notes",
            ]
            visible_columns = [column for column in preferred_columns if column in df_display.columns]
            st.dataframe(
                df_display[visible_columns],
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("Nessuna rilevazione da mostrare.")
    else:
        logger.info(f"Nessuna misurazione presente nello storico tabellare per paziente ID: {current_patient_id}")
        st.info("Nessuna misurazione presente per questo paziente.")