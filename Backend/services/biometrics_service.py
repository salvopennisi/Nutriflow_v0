import logging
from datetime import date

from psycopg2.extras import RealDictCursor

from Common.functions import connect, disconnect


ALLOWED_NUTRITION_CONTEXTS = {"INITIAL", "DIET_PLAN", "FREE_DIET", "STOP"}
ALLOWED_WORKOUT_CONTEXTS = {"INITIAL", "WORKOUT_PLAN", "NO_WORKOUT", "STOP"}


def _validate_period_context(cur, biometric_data: dict) -> None:
    """
    Valida, quando presenti, i riferimenti al periodo precedente alla misurazione.

    La validazione resta retrocompatibile con i flussi legacy che inseriscono
    record peso-only senza contesto (es. creazione assistito / ricalcolo TDEE).
    """
    nutrition_context = biometric_data.get("nutrition_context")
    workout_context = biometric_data.get("workout_context")

    # Flusso legacy: nessun contesto fornito.
    if nutrition_context is None and workout_context is None:
        return

    patient_id = biometric_data.get("patient_id")
    if not patient_id:
        raise ValueError("patient_id obbligatorio.")

    if nutrition_context not in ALLOWED_NUTRITION_CONTEXTS:
        raise ValueError(
            "nutrition_context non valido. Valori ammessi: "
            + ", ".join(sorted(ALLOWED_NUTRITION_CONTEXTS))
        )

    if workout_context not in ALLOWED_WORKOUT_CONTEXTS:
        raise ValueError(
            "workout_context non valido. Valori ammessi: "
            + ", ".join(sorted(ALLOWED_WORKOUT_CONTEXTS))
        )

    diet_plan_id = biometric_data.get("diet_plan_id")
    workout_plan_id = biometric_data.get("workout_plan_id")

    if nutrition_context == "DIET_PLAN":
        if not diet_plan_id:
            raise ValueError("diet_plan_id obbligatorio quando nutrition_context = DIET_PLAN.")
        cur.execute(
            "SELECT 1 FROM diet_plans WHERE id = %s AND patient_id = %s;",
            (diet_plan_id, patient_id),
        )
        if cur.fetchone() is None:
            raise ValueError("Il piano alimentare selezionato non appartiene all'assistito.")
    elif diet_plan_id:
        raise ValueError(
            "diet_plan_id deve essere NULL per Prima misurazione, Dieta libera o Stop."
        )

    if workout_context == "WORKOUT_PLAN":
        if not workout_plan_id:
            raise ValueError("workout_plan_id obbligatorio quando workout_context = WORKOUT_PLAN.")
        cur.execute(
            "SELECT 1 FROM workout_plans WHERE id = %s AND patient_id = %s;",
            (workout_plan_id, patient_id),
        )
        if cur.fetchone() is None:
            raise ValueError("Il workout selezionato non appartiene all'assistito.")
    elif workout_plan_id:
        raise ValueError(
            "workout_plan_id deve essere NULL per Prima misurazione, Nessun workout o Stop."
        )

    context_start_date = biometric_data.get("context_start_date")
    insert_date = biometric_data.get("insert_date") or date.today()

    if nutrition_context == "INITIAL" or workout_context == "INITIAL":
        if nutrition_context != "INITIAL" or workout_context != "INITIAL":
            raise ValueError(
                "Per una baseline iniziale nutrition_context e workout_context devono entrambi essere INITIAL."
            )
        if context_start_date is not None:
            raise ValueError("context_start_date deve essere NULL per la prima misurazione.")
    else:
        if context_start_date is None:
            raise ValueError("context_start_date obbligatoria per una misurazione successiva alla baseline.")
        if context_start_date >= insert_date:
            raise ValueError("context_start_date deve precedere insert_date.")


def add_biometric_record(conf, biometric_data: dict) -> str:
    """
    Aggiunge una nuova misurazione biometrica per un assistito.

    Se il payload contiene nutrition_context/workout_context, valida anche che
    gli eventuali piani referenziati appartengano allo stesso assistito.
    """
    conn = connect(conf)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            _validate_period_context(cur, biometric_data)

            keys = list(biometric_data.keys())
            columns = ", ".join(keys)
            placeholders = ", ".join([f"%({key})s" for key in keys])

            query = f"""
                INSERT INTO biometrics ({columns})
                VALUES ({placeholders})
                RETURNING id;
            """
            cur.execute(query, biometric_data)
            new_id = cur.fetchone()["id"]
            conn.commit()
            logging.info(
                "Misurazione biometrica inserita per il paziente %s",
                biometric_data.get("patient_id"),
            )
            return str(new_id)
    except Exception as exc:
        conn.rollback()
        logging.error("Errore in add_biometric_record: %s", exc)
        raise
    finally:
        disconnect(conn)


def get_biometrics_history(conf, patient_id: str) -> list:
    """Recupera lo storico biometrico con i nomi dei piani referenziati."""
    conn = connect(conf)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    b.*,
                    dp.diet_name AS diet_plan_name,
                    wp.workout_name AS workout_plan_name
                FROM biometrics b
                LEFT JOIN diet_plans dp
                    ON b.diet_plan_id = dp.id
                LEFT JOIN workout_plans wp
                    ON b.workout_plan_id = wp.id
                WHERE b.patient_id = %s
                ORDER BY b.insert_date DESC, b.id DESC;
                """,
                (patient_id,),
            )
            return cur.fetchall()
    except Exception as exc:
        logging.error("Errore in get_biometrics_history: %s", exc)
        raise
    finally:
        disconnect(conn)


def get_latest_biometrics(conf, patient_id: str) -> dict | None:
    """Recupera l'ultima misurazione e il contesto dieta/workout associato."""
    conn = connect(conf)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    b.*,
                    dp.diet_name AS diet_plan_name,
                    wp.workout_name AS workout_plan_name
                FROM biometrics b
                LEFT JOIN diet_plans dp
                    ON b.diet_plan_id = dp.id
                LEFT JOIN workout_plans wp
                    ON b.workout_plan_id = wp.id
                WHERE b.patient_id = %s
                ORDER BY b.insert_date DESC, b.id DESC
                LIMIT 1;
                """,
                (patient_id,),
            )
            return cur.fetchone()
    except Exception as exc:
        logging.error("Errore in get_latest_biometrics: %s", exc)
        raise
    finally:
        disconnect(conn)


def delete_biometric_record(conf, record_id: str) -> bool:
    """Elimina una specifica misurazione inserita per errore."""
    conn = connect(conf)
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM biometrics WHERE id = %s;", (record_id,))
            conn.commit()
            return True
    except Exception as exc:
        conn.rollback()
        logging.error("Errore in delete_biometric_record: %s", exc)
        raise
    finally:
        disconnect(conn)


def calcola_bf_jackson_pollock_7(sesso: str, eta: int, pliche: dict) -> float:
    """
    Calcola la percentuale di massa grassa (BF %) utilizzando l'equazione di Jackson-Pollock a 7 pliche.
    
    Parametri:
    - sesso (str): "Maschio" o "Femmina"
    - eta (int): Età del paziente in anni
    - pliche (dict): Dizionario contenente i valori in mm delle 7 pliche:
        'pliche_petto_mm', 'pliche_addome_mm', 'pliche_coscia_mm', 
        'pliche_tricipite_mm', 'pliche_sovrascapolare_mm', 
        'pliche_sovrailiaca_mm', 'pliche_ascellare_mm'
    
    Ritorna:
    - float: Percentuale di massa grassa arrotondata a 2 decimali, oppure None in caso di dati incompleti.
    """
    # Chiavi richieste per il calcolo
    chiavi_richieste = [
        'pliche_petto_mm', 'pliche_addome_mm', 'pliche_coscia_mm',
        'pliche_tricipite_mm', 'pliche_sovrascapolare_mm',
        'pliche_sovrailiaca_mm', 'pliche_ascellare_mm'
    ]
    
    # Verifica che tutte le pliche siano presenti e valide (> 0)
    if not all(pliche.get(k) is not None and pliche.get(k) > 0 for k in chiavi_richieste):
        return None
    
    # Somma delle 7 pliche in mm
    somma_pliche = sum(pliche[k] for k in chiavi_richieste)
    
    if sesso.lower() in ["maschio", "m", "uomo"]:
        # Formula Uomini (Jackson & Pollock, 1978)
        # Densità Corporea (Db)
        db = 1.11200000 \
             - (0.00043499 * somma_pliche) \
             + (0.00000055 * (somma_pliche ** 2)) \
             - (0.00028826  * eta)
             
        # Formula di Siri (1961) per convertire la densità in percentuale di massa grassa (BF)
        bf = (495 / db) - 450
        
    elif sesso.lower() in ["femmina", "f", "donna"]:
        # Formula Donne (Jackson, Pollock & Ward, 1980)
        # Densità Corporea (Db)
        db = 1.0970 \
             - (0.00046971 * somma_pliche) \
             + (0.00000056 * (somma_pliche ** 2)) \
             - (0.00012828 * eta)
             
        # Formula di Siri (1961)
        bf = (495 / db) - 450
    else:
        raise ValueError("Sesso non riconosciuto. Specificare 'Maschio' o 'Femmina'.")
        
    return round(float(bf), 2)

def calcola_proporzioni_auree_bodybuilding(misurazioni: dict, tipo_riferimento: str = "polso", altezza_cm: float = None) -> dict:
    """
    Confronta le misurazioni reali dell'assistito con i valori target ideali 
    del bodybuilding classico (Golden Ratio / McCallum), includendo avambraccio e altezza.
    
    Parametri:
    - misurazioni (dict): Dizionario contenente le circonferenze registrate 
      (es. 'circ_polsi_cm', 'circ_vita_cm', 'circ_torace_cm', 'circ_avambraccio_cm', ecc.)
    - tipo_riferimento (str): "polso" (metodo McCallum) o "vita" (rapporto aureo).
    - altezza_cm (float, opzionale): Altezza della persona in cm (utile per il target di vita/altezza).
    
    Ritorna:
    - dict: Un dizionario riepilogativo contenente il confronto dettagliato.
    """
    PHI = 1.618033988749895
    tipo_riferimento = tipo_riferimento.strip().lower()
    
    comparazioni = []
    
    if tipo_riferimento == "polso":
        polso = misurazioni.get('circ_polsi_cm')
        if not polso or float(polso) <= 0:
            raise ValueError("Circonferenza del polso mancante o non valida per il calcolo basato sul polso.")
            
        polso = float(polso)
        torace_ideale = polso * 6.5
        braccio_ideale = polso * 2.57
        
        target_map = {
            "Torace": {"ideale": torace_ideale, "reale": misurazioni.get('circ_torace_cm')},
            "Bacino/Fianchi": {"ideale": torace_ideale * 0.85, "reale": misurazioni.get('circ_fianchi_cm')},
            "Coscia": {"ideale": torace_ideale * 0.53, "reale": misurazioni.get('circ_coscia_cm')},
            "Braccio": {"ideale": braccio_ideale, "reale": misurazioni.get('circ_braccia_cm')},
            "Avambraccio": {"ideale": braccio_ideale * 0.80, "reale": misurazioni.get('circ_avambracci_cm') or misurazioni.get('circ_avambraccio_cm')},
            "Polpaccio": {"ideale": polso * 2.34, "reale": misurazioni.get('circ_polpacci_cm')},
            "Collo": {"ideale": torace_ideale * 0.37, "reale": misurazioni.get('circ_collo_cm')}
        }
        
        valore_base = polso
        
    elif tipo_riferimento == "vita":
        vita = misurazioni.get('circ_vita_cm')
        if not vita or float(vita) <= 0:
            raise ValueError("Circonferenza della vita mancante o non valida per il calcolo aureo.")
            
        vita = float(vita)
        target_map = {
            "Vita (Base)": {"ideale": vita, "reale": vita},
            "Spalle / Torace Aureo": {"ideale": vita * PHI, "reale": misurazioni.get('circ_torace_cm') or misurazioni.get('circ_spalle_cm')},
            "Coscia Aurea": {"ideale": vita / PHI, "reale": misurazioni.get('circ_coscia_cm')}
        }
        
        # Se viene passata l'altezza, aggiungiamo il target di riferimento strutturale (WHR ottimale ~44%)
        if altezza_cm and float(altezza_cm) > 0:
            target_map["Vita su Altezza (44%)"] = {"ideale": float(altezza_cm) * 0.44, "reale": vita}
            
        valore_base = vita
    else:
        raise ValueError("Tipo riferimento non valido. Scegli tra 'polso' o 'vita'.")
        
    # Costruiamo l'array comparativo
    for parte, dati in target_map.items():
        ideale = round(dati["ideale"], 2)
        reale_raw = dati["reale"]
        
        if reale_raw is not None and str(reale_raw).strip() != "":
            reale = round(float(reale_raw), 2)
            diff = round(reale - ideale, 2)
            perc_scostamento = round((diff / ideale) * 100, 1)
            stato = f"+{diff} cm" if diff > 0 else f"{diff} cm"
            stato_perc = f"+{perc_scostamento}%" if perc_scostamento > 0 else f"{perc_scostamento}%"
        else:
            reale = None
            diff = None
            stato = "Non misurato"
            stato_perc = "N/D"
            
        comparazioni.append({
            "Distretto": parte,
            "Misura Reale (cm)": reale,
            "Target Ideale (cm)": ideale,
            "Differenza": stato,
            "Scostamento (%)": stato_perc
        })
        
    return {
        "riferimento": tipo_riferimento.capitalize(),
        "valore_base": valore_base,
        "altezza_considerata": altezza_cm,
        "comparazione": comparazioni
    }