import streamlit as st
from datetime import date
from Backend.services.patient_service import create_patient, get_all_patients, update_patient

# Placeholder per la configurazione del DB
CONF = st.session_state.get("db_conf", {})
tec_conf = st.session_state.get("tec_conf", {})
st.title("Gestione Pazienti 👥")

if "user_id" not in st.session_state:
    st.session_state.user_id = "00000000-0000-0000-0000-000000000000"

user_id = st.session_state.user_id

tab_lista, tab_nuovo = st.tabs(["Lista Pazienti", "Nuovo Paziente"])

opzioni_stile_vita = {
    "Sedentario": "Lavoro d'ufficio, assenza di esercizio fisico strutturato, spostamenti prevalentemente in auto.",
    "Leggermente attivo": "Lavoro sedentario ma con presenza di camminata quotidiana o attività fisica leggera 1-2 volte a settimana.",
    "Moderatamente attivo": "Attività lavorativa che richiede movimento o allenamento strutturato di media intensità (3-4 volte a settimana).",
    "Molto attivo": "Lavoro pesante o allenamento intenso frequente (5-6 volte a settimana).",
    "Estremamente attivo": "Lavoratori manuali pesanti uniti a regime di allenamento agonistico o quotidiano molto intenso.",
}
opzioni_stile_vita_formattate = [f"{k} — {v}" for k, v in opzioni_stile_vita.items()]

# Fattori PAL usati per la stima del TDEE
fattori_pal = {
    "Sedentario": 1.20,
    "Leggermente attivo": 1.30,
    "Moderatamente attivo": 1.425,
    "Molto attivo": 1.575,
    "Estremamente attivo": 1.725,
}


def calcola_tdee(
    data_nascita,
    sesso,
    altezza_cm,
    peso_kg,
    stile_vita,
    correzione_percentuale=0.0,
):
    """Calcola BMR, TDEE stimato e TDEE adottato in kcal/giorno."""
    if not isinstance(data_nascita, date):
        raise ValueError("Data di nascita non valida.")

    oggi = date.today()
    eta = oggi.year - data_nascita.year - (
        (oggi.month, oggi.day) < (data_nascita.month, data_nascita.day)
    )

    if sesso == "Maschio":
        costante_sesso = 5
    elif sesso == "Femmina":
        costante_sesso = -161
    else:
        raise ValueError("Per il calcolo TDEE seleziona Maschio o Femmina.")

    if altezza_cm is None or altezza_cm <= 0:
        raise ValueError("Inserisci un'altezza valida per calcolare il TDEE.")

    if peso_kg is None or peso_kg <= 0:
        raise ValueError("Inserisci un peso valido per calcolare il TDEE.")

    categoria_pal = stile_vita.split(" — ", 1)[0] if stile_vita else None
    fattore_pal = fattori_pal.get(categoria_pal)
    if fattore_pal is None:
        raise ValueError("Livello di attività fisica non valido.")

    bmr = (10 * peso_kg) + (6.25 * altezza_cm) - (5 * eta) + costante_sesso
    tdee_stimato = bmr * fattore_pal
    fattore_correzione = 1 + (float(correzione_percentuale) / 100.0)
    tdee_adottato = tdee_stimato * fattore_correzione

    return (
        round(bmr),
        round(tdee_stimato),
        round(tdee_adottato),
        eta,
        fattore_pal,
        fattore_correzione,
    )


with tab_nuovo:
    st.subheader("Inserisci un nuovo paziente")
    with st.form("new_patient_form"):
        col1, col2 = st.columns(2)
        with col1:
            nome = st.text_input("Nome *")
            cognome = st.text_input("Cognome *")
            data_nascita = st.date_input(
                "Data di Nascita",
                value=date(1990, 1, 1),
                min_value=date(1900, 1, 1),
                max_value=date.today(),
            )
            altezza_cm = st.number_input(
                "Altezza (cm)", min_value=0.0, max_value=250.0, step=0.5
            )
            peso_kg = st.number_input(
                "Peso iniziale (kg)", min_value=0.0, max_value=400.0, step=0.1
            )

        with col2:
            sesso = st.selectbox("Sesso", ["", "Maschio", "Femmina", "Altro"])
            stile_vita = st.selectbox(
                "Stile di vita / Livello di Attività Fisica (PAL)",
                options=opzioni_stile_vita_formattate,
                index=0,
            )
            professione = st.text_input("Professione")
            correzione_tdee = st.number_input(
                "Correzione professionista TDEE (%)",
                min_value=-30.0,
                max_value=30.0,
                value=0.0,
                step=1.0,
                help=(
                    "Correzione applicata al TDEE stimato. Esempio: -5% equivale "
                    "a un fattore correttivo 0,95."
                ),
            )

        descrizione_storia = st.text_area("Descrizione Storia / Anamnesi")
        patologie = st.text_area("Patologie / Note Cliniche")

        col_calc, col_save = st.columns(2)
        with col_calc:
            calculate_tdee = st.form_submit_button(
                "🔥 Calcola TDEE", use_container_width=True
            )
        with col_save:
            submitted = st.form_submit_button(
                "Salva Paziente", use_container_width=True
            )

        if calculate_tdee:
            try:
                (
                    bmr,
                    tdee_stimato,
                    tdee_adottato,
                    eta,
                    fattore_pal,
                    fattore_correzione,
                ) = calcola_tdee(
                    data_nascita,
                    sesso,
                    altezza_cm,
                    peso_kg,
                    stile_vita,
                    correzione_tdee,
                )
                st.success(f"TDEE adottato: {tdee_adottato} kcal/giorno")
                st.caption(
                    f"TDEE stimato: {tdee_stimato} kcal/giorno · BMR: {bmr} kcal/giorno · "
                    f"Età: {eta} anni · PAL: {fattore_pal} · "
                    f"Correzione: {correzione_tdee:+.0f}% (FC {fattore_correzione:.2f})"
                )
            except ValueError as e:
                st.warning(str(e))

        if submitted:
            if not nome or not cognome:
                st.error("Nome e Cognome sono obbligatori.")
            else:
                # Se tutti i dati necessari sono disponibili, il TDEE viene
                # calcolato e salvato insieme al nuovo paziente.
                tdee_da_salvare = None
                if altezza_cm > 0 and peso_kg > 0 and sesso in ("Maschio", "Femmina"):
                    try:
                        _, _, tdee_da_salvare, _, _, _ = calcola_tdee(
                            data_nascita,
                            sesso,
                            altezza_cm,
                            peso_kg,
                            stile_vita,
                            correzione_tdee,
                        )
                    except ValueError:
                        tdee_da_salvare = None

                patient_data = {
                    "user_id": user_id,
                    "nome": nome,
                    "cognome": cognome,
                    "data_nascita": data_nascita,
                    "altezza_cm": altezza_cm if altezza_cm > 0 else None,
                    "sesso": sesso if sesso else None,
                    "stile_vita": stile_vita if stile_vita else None,
                    "categoria_energetica_professione": professione if professione else None,
                    "descrizione_storia": descrizione_storia if descrizione_storia else None,
                    "patologie": patologie if patologie else None,
                    "tdee_kcal": tdee_da_salvare,
                }

                try:
                    nuovo_id = create_patient(
                        tec_conf,
                        patient_data,
                        peso_kg=peso_kg if peso_kg > 0 else None,
                    )
                    msg = f"Paziente {nome} {cognome} creato con successo! (ID: {nuovo_id})"
                    if tdee_da_salvare is not None:
                        msg += f" · TDEE: {tdee_da_salvare} kcal/giorno"
                    st.success(msg)
                except Exception as e:
                    st.error(f"Errore durante il salvataggio del paziente: {e}")


with tab_lista:
    st.subheader("Lista e Modifica Pazienti")

    try:
        pazienti = get_all_patients(tec_conf, user_id)
    except Exception as e:
        st.error(f"Impossibile caricare la lista dei pazienti dal database: {e}")
        pazienti = []

    if not pazienti:
        st.info("Nessun paziente trovato. Aggiungine uno nuovo dal tab apposito.")
    else:
        search_query = st.text_input(
            "🔍 Cerca paziente (per nome, cognome o professione)",
            value="",
            placeholder="Digita per filtrare...",
        ).strip().lower()

        if search_query:
            pazienti_filtrati = [
                p
                for p in pazienti
                if search_query in p.get("nome", "").lower()
                or search_query in p.get("cognome", "").lower()
                or search_query
                in str(p.get("categoria_energetica_professione", "")).lower()
            ]
        else:
            pazienti_filtrati = pazienti

        if not pazienti_filtrati:
            st.warning("Nessun paziente corrisponde ai criteri di ricerca.")
        else:
            st.caption(
                f"Visualizzazione di {len(pazienti_filtrati)} su {len(pazienti)} pazienti."
            )

            for p in pazienti_filtrati:
                with st.container(border=True):
                    col1, col2 = st.columns([3, 1])
                    with col1:
                        st.write(f"**{p['cognome']} {p['nome']}**")
                        details = []
                        if p.get("data_nascita"):
                            details.append(f"Data di Nascita: {p['data_nascita']}")
                        if p.get("sesso"):
                            details.append(f"Sesso: {p['sesso']}")
                        if p.get("categoria_energetica_professione"):
                            details.append(
                                f"Professione/Categoria: {p['categoria_energetica_professione']}"
                            )
                        if details:
                            st.caption(" | ".join(details))

                    with col2:
                        if p.get("tdee_kcal") is not None:
                            st.metric(
                                "TDEE corrente",
                                f"{round(float(p['tdee_kcal']))} kcal",
                            )
                        else:
                            st.metric("TDEE corrente", "Non calcolato")

                    with st.expander(f"✏️ Modifica dati di {p['nome']} {p['cognome']}"):
                        if p.get("tdee_kcal") is not None:
                            aggiornato_il = p.get("tdee_updated_at")
                            if aggiornato_il:
                                st.caption(
                                    f"TDEE salvato: {round(float(p['tdee_kcal']))} kcal/giorno · "
                                    f"ultimo aggiornamento: {aggiornato_il}"
                                )
                            else:
                                st.caption(
                                    f"TDEE salvato: {round(float(p['tdee_kcal']))} kcal/giorno"
                                )
                        else:
                            st.caption("TDEE non ancora salvato per questo paziente.")

                        with st.form(key=f"edit_form_{p['id']}"):
                            e_col1, e_col2 = st.columns(2)

                            with e_col1:
                                e_nome = st.text_input(
                                    "Nome *",
                                    value=p.get("nome", ""),
                                    key=f"name_{p['id']}",
                                )
                                e_cognome = st.text_input(
                                    "Cognome *",
                                    value=p.get("cognome", ""),
                                    key=f"surname_{p['id']}",
                                )

                                val_data = p.get("data_nascita")
                                if isinstance(val_data, str):
                                    val_data = date.fromisoformat(val_data)
                                elif not isinstance(val_data, date):
                                    val_data = date(1990, 1, 1)

                                e_data_nascita = st.date_input(
                                    "Data di Nascita",
                                    value=val_data,
                                    min_value=date(1900, 1, 1),
                                    max_value=date.today(),
                                    key=f"date_{p['id']}",
                                )

                                e_altezza = st.number_input(
                                    "Altezza (cm)",
                                    min_value=0.0,
                                    max_value=250.0,
                                    value=float(p.get("altezza_cm") or 0.0),
                                    step=0.5,
                                    key=f"alt_{p['id']}",
                                )

                                e_peso = st.number_input(
                                    "Peso corrente (kg)",
                                    min_value=0.0,
                                    max_value=400.0,
                                    value=float(p.get("peso_corrente_kg") or 0.0),
                                    step=0.1,
                                    key=f"peso_{p['id']}",
                                )

                            with e_col2:
                                sesso_list = ["", "Maschio", "Femmina", "Altro"]
                                s_corrente = p.get("sesso")
                                s_index = (
                                    sesso_list.index(s_corrente)
                                    if s_corrente in sesso_list
                                    else 0
                                )
                                e_sesso = st.selectbox(
                                    "Sesso",
                                    sesso_list,
                                    index=s_index,
                                    key=f"sex_{p['id']}",
                                )

                                st_corrente = p.get("stile_vita")
                                st_index = 0
                                if st_corrente in opzioni_stile_vita_formattate:
                                    st_index = opzioni_stile_vita_formattate.index(
                                        st_corrente
                                    )

                                e_stile_vita = st.selectbox(
                                    "Stile di vita / Livello di Attività Fisica (PAL)",
                                    options=opzioni_stile_vita_formattate,
                                    index=st_index,
                                    key=f"pal_{p['id']}",
                                )

                                e_professione = st.text_input(
                                    "Professione",
                                    value=p.get(
                                        "categoria_energetica_professione", ""
                                    ),
                                    key=f"prof_{p['id']}",
                                )

                            e_descrizione = st.text_area(
                                "Descrizione Storia / Anamnesi",
                                value=p.get("descrizione_storia", ""),
                                key=f"desc_{p['id']}",
                            )
                            e_patologie = st.text_area(
                                "Patologie / Note Cliniche",
                                value=p.get("patologie", ""),
                                key=f"pat_{p['id']}",
                            )

                            st.markdown("### 🔥 Aggiornamento TDEE")
                            st.caption(
                                "Il TDEE può essere ricalcolato con la formula oppure sovrascritto "
                                "manualmente sulla base del mantenimento osservato nel tempo."
                            )

                            tdee_cols = st.columns(2)
                            with tdee_cols[0]:
                                e_correzione_tdee = st.number_input(
                                    "Correzione professionista (%)",
                                    min_value=-30.0,
                                    max_value=30.0,
                                    value=0.0,
                                    step=1.0,
                                    key=f"corr_tdee_{p['id']}",
                                    help="Applicata solo al TDEE ricalcolato con la formula.",
                                )

                            with tdee_cols[1]:
                                tdee_corrente = float(p.get("tdee_kcal") or 0.0)
                                e_tdee_manuale = st.number_input(
                                    "TDEE manuale (kcal/giorno)",
                                    min_value=0.0,
                                    max_value=10000.0,
                                    value=tdee_corrente,
                                    step=25.0,
                                    key=f"manual_tdee_{p['id']}",
                                    help=(
                                        "Inserisci direttamente il fabbisogno di mantenimento osservato. "
                                        "Il valore non dipende dalla formula e non modifica il peso."
                                    ),
                                )

                            aggiorna_tdee_calcolato = st.checkbox(
                                "Aggiorna il TDEE con il valore calcolato quando salvo il paziente",
                                value=False,
                                key=f"update_calc_tdee_{p['id']}",
                                help=(
                                    "Se selezionato, 'Aggiorna Paziente' ricalcola il TDEE usando "
                                    "peso, altezza, sesso, età, PAL e correzione professionista."
                                ),
                            )

                            col_calc, col_manual, col_update = st.columns(3)
                            with col_calc:
                                calculate_tdee_update = st.form_submit_button(
                                    "🔥 Ricalcola TDEE",
                                    use_container_width=True,
                                )
                            with col_manual:
                                save_manual_tdee = st.form_submit_button(
                                    "💾 Salva TDEE manuale",
                                    use_container_width=True,
                                )
                            with col_update:
                                submitted_update = st.form_submit_button(
                                    "Aggiorna Paziente",
                                    use_container_width=True,
                                )

                            updated_data = {
                                "nome": e_nome,
                                "cognome": e_cognome,
                                "data_nascita": e_data_nascita,
                                "altezza_cm": e_altezza if e_altezza > 0 else None,
                                "sesso": e_sesso if e_sesso else None,
                                "stile_vita": e_stile_vita if e_stile_vita else None,
                                "categoria_energetica_professione": e_professione if e_professione else None,
                                "descrizione_storia": e_descrizione if e_descrizione else None,
                                "patologie": e_patologie if e_patologie else None,
                            }

                            if calculate_tdee_update:
                                try:
                                    (
                                        bmr,
                                        tdee_stimato,
                                        tdee_adottato,
                                        eta,
                                        fattore_pal,
                                        fattore_correzione,
                                    ) = calcola_tdee(
                                        e_data_nascita,
                                        e_sesso,
                                        e_altezza,
                                        e_peso,
                                        e_stile_vita,
                                        e_correzione_tdee,
                                    )
                                    st.success(
                                        f"TDEE calcolato da adottare: {tdee_adottato} kcal/giorno"
                                    )
                                    st.caption(
                                        f"TDEE stimato: {tdee_stimato} kcal/giorno · "
                                        f"BMR: {bmr} kcal/giorno · Età: {eta} anni · "
                                        f"PAL: {fattore_pal} · Peso: {e_peso:.1f} kg · "
                                        f"Correzione: {e_correzione_tdee:+.0f}% "
                                        f"(FC {fattore_correzione:.2f})"
                                    )
                                    st.info(
                                        "Questa è solo un'anteprima. Per salvarla, seleziona la checkbox "
                                        "'Aggiorna il TDEE con il valore calcolato...' e premi 'Aggiorna Paziente'."
                                    )
                                except ValueError as e:
                                    st.warning(str(e))

                            if save_manual_tdee:
                                if not e_nome or not e_cognome:
                                    st.error("Nome e Cognome sono obbligatori.")
                                elif e_tdee_manuale <= 0:
                                    st.warning("Inserisci un TDEE manuale maggiore di zero.")
                                else:
                                    try:
                                        tdee_manuale_da_salvare = round(e_tdee_manuale)
                                        update_patient(
                                            tec_conf,
                                            p["id"],
                                            updated_data,
                                            peso_kg=None,
                                            tdee_kcal=tdee_manuale_da_salvare,
                                            aggiorna_tdee=True,
                                        )
                                        st.success(
                                            f"TDEE manuale aggiornato a {tdee_manuale_da_salvare} kcal/giorno."
                                        )
                                        st.rerun()
                                    except Exception as e:
                                        st.error(
                                            f"Errore durante l'aggiornamento manuale del TDEE: {e}"
                                        )

                            if submitted_update:
                                if not e_nome or not e_cognome:
                                    st.error("Nome e Cognome sono obbligatori.")
                                else:
                                    tdee_aggiornato = None
                                    peso_da_salvare = None
                                    errore_tdee = None

                                    if aggiorna_tdee_calcolato:
                                        try:
                                            (
                                                _,
                                                _,
                                                tdee_aggiornato,
                                                _,
                                                _,
                                                _,
                                            ) = calcola_tdee(
                                                e_data_nascita,
                                                e_sesso,
                                                e_altezza,
                                                e_peso,
                                                e_stile_vita,
                                                e_correzione_tdee,
                                            )
                                            peso_da_salvare = e_peso
                                        except ValueError as e:
                                            errore_tdee = str(e)

                                    if errore_tdee:
                                        st.warning(f"Paziente non aggiornato: {errore_tdee}")
                                    else:
                                        try:
                                            update_patient(
                                                tec_conf,
                                                p["id"],
                                                updated_data,
                                                peso_kg=peso_da_salvare,
                                                tdee_kcal=tdee_aggiornato,
                                                aggiorna_tdee=aggiorna_tdee_calcolato,
                                            )

                                            if aggiorna_tdee_calcolato:
                                                st.success(
                                                    f"Paziente aggiornato. Nuovo TDEE calcolato: "
                                                    f"{tdee_aggiornato} kcal/giorno."
                                                )
                                            else:
                                                st.success(
                                                    f"Paziente {e_nome} {e_cognome} aggiornato con successo!"
                                                )
                                            st.rerun()
                                        except Exception as e:
                                            st.error(
                                                f"Errore durante l'aggiornamento del paziente: {e}"
                                            )
