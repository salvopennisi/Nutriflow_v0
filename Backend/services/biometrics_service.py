import logging
import math
from psycopg2.extras import RealDictCursor
from Common.functions import connect, disconnect

def add_biometric_record(conf, biometric_data: dict) -> str:
    """
    Aggiunge una nuova misurazione biometrica per un paziente.
    Ritorna l'UUID del nuovo record inserito.
    """
    conn = connect(conf)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Estraiamo dinamicamente le chiavi e i valori per evitare query chilometriche
            keys = list(biometric_data.keys())
            columns = ", ".join(keys)
            placeholders = ", ".join([f"%({k})s" for k in keys])
            
            query = f"""
                INSERT INTO biometrics ({columns}) 
                VALUES ({placeholders}) 
                RETURNING id;
            """
            cur.execute(query, biometric_data)
            new_id = cur.fetchone()['id']
            conn.commit()
            logging.info(f"Misurazione biometrica inserita per il paziente {biometric_data.get('patient_id')}")
            return new_id
    except Exception as e:
        conn.rollback()
        logging.error(f"Errore in add_biometric_record: {e}")
        raise
    finally:
        disconnect(conn)

def get_biometrics_history(conf, patient_id: str) -> list:
    """
    Recupera l'intero storico delle misurazioni di un paziente.
    Utile per disegnare i grafici di trend.
    """
    conn = connect(conf)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            query = """
                SELECT * FROM biometrics 
                WHERE patient_id = %s 
                ORDER BY insert_date DESC;
            """
            cur.execute(query, (patient_id,))
            return cur.fetchall()
    except Exception as e:
        logging.error(f"Errore in get_biometrics_history: {e}")
        raise
    finally:
        disconnect(conn)

def get_latest_biometrics(conf, patient_id: str) -> dict:
    """
    Recupera solo l'ultima misurazione registrata per un paziente.
    Ritorna None se non ci sono misurazioni.
    """
    conn = connect(conf)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            query = """
                SELECT * FROM biometrics 
                WHERE patient_id = %s 
                ORDER BY insert_date DESC 
                LIMIT 1;
            """
            cur.execute(query, (patient_id,))
            return cur.fetchone()
    except Exception as e:
        logging.error(f"Errore in get_latest_biometrics: {e}")
        raise
    finally:
        disconnect(conn)

def delete_biometric_record(conf, record_id: str) -> bool:
    """
    Elimina una specifica misurazione (in caso di errore di inserimento).
    """
    conn = connect(conf)
    try:
        with conn.cursor() as cur:
            query = "DELETE FROM biometrics WHERE id = %s;"
            cur.execute(query, (record_id,))
            conn.commit()
            return True
    except Exception as e:
        conn.rollback()
        logging.error(f"Errore in delete_biometric_record: {e}")
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
            "Avambraccio": {"ideale": braccio_ideale * 0.80, "reale": misurazioni.get('circ_avambraccio_cm')},
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