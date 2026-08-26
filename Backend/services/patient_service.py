import logging
from psycopg2.extras import RealDictCursor
from Common.functions import connect, disconnect


def get_all_patients(conf, user_id: str) -> list:
    """
    Recupera tutti i pazienti del nutrizionista includendo l'ultimo peso disponibile.
    """
    conn = connect(conf)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            query = """
                SELECT
                    p.*,
                    latest_bio.peso_kg AS peso_corrente_kg
                FROM patients p
                LEFT JOIN LATERAL (
                    SELECT b.peso_kg
                    FROM biometrics b
                    WHERE b.patient_id = p.id
                      AND b.peso_kg IS NOT NULL
                    ORDER BY b.insert_date DESC, b.id DESC
                    LIMIT 1
                ) latest_bio ON TRUE
                WHERE p.user_id = %s
                ORDER BY p.cognome, p.nome;
            """
            cur.execute(query, (user_id,))
            return cur.fetchall()
    except Exception as e:
        logging.error(f"Errore in get_all_patients: {e}")
        raise
    finally:
        disconnect(conn)


def get_patient_by_id(conf, patient_id: str) -> dict:
    """
    Recupera i dettagli del paziente includendo l'ultimo peso disponibile.
    """
    conn = connect(conf)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            query = """
                SELECT
                    p.*,
                    latest_bio.peso_kg AS peso_corrente_kg
                FROM patients p
                LEFT JOIN LATERAL (
                    SELECT b.peso_kg
                    FROM biometrics b
                    WHERE b.patient_id = p.id
                      AND b.peso_kg IS NOT NULL
                    ORDER BY b.insert_date DESC, b.id DESC
                    LIMIT 1
                ) latest_bio ON TRUE
                WHERE p.id = %s;
            """
            cur.execute(query, (patient_id,))
            return cur.fetchone()
    except Exception as e:
        logging.error(f"Errore in get_patient_by_id: {e}")
        raise
    finally:
        disconnect(conn)


def create_patient(conf, patient_data: dict, peso_kg=None) -> str:
    """
    Crea un nuovo paziente, salva opzionalmente il peso iniziale e il TDEE
    calcolato e restituisce il suo UUID.
    """
    conn = connect(conf)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            data = dict(patient_data)
            data.setdefault("tdee_kcal", None)

            query = """
                INSERT INTO patients (
                    user_id, nome, cognome, data_nascita, altezza_cm, sesso,
                    stile_vita, categoria_energetica_professione,
                    descrizione_storia, patologie, tdee_kcal, tdee_updated_at
                ) VALUES (
                    %(user_id)s, %(nome)s, %(cognome)s, %(data_nascita)s, %(altezza_cm)s, %(sesso)s,
                    %(stile_vita)s, %(categoria_energetica_professione)s,
                    %(descrizione_storia)s, %(patologie)s, %(tdee_kcal)s,
                    CASE WHEN %(tdee_kcal)s IS NULL THEN NULL ELSE CURRENT_TIMESTAMP END
                ) RETURNING id;
            """
            cur.execute(query, data)
            new_id = cur.fetchone()['id']

            if peso_kg is not None and peso_kg > 0:
                cur.execute(
                    """
                    INSERT INTO biometrics (patient_id, peso_kg)
                    VALUES (%s, %s);
                    """,
                    (new_id, peso_kg)
                )

            conn.commit()
            logging.info(
                f"Paziente {data.get('nome')} {data.get('cognome')} creato con successo."
            )
            return new_id
    except Exception as e:
        conn.rollback()
        logging.error(f"Errore in create_patient: {e}")
        raise
    finally:
        disconnect(conn)


def update_patient(
    conf,
    patient_id: str,
    patient_data: dict,
    peso_kg=None,
    tdee_kcal=None,
    aggiorna_tdee: bool = False,
) -> bool:
    """
    Aggiorna i dati del paziente.

    Se aggiorna_tdee=True:
    - aggiorna tdee_kcal e tdee_updated_at;
    - registra il peso in biometrics solo se diverso dall'ultimo peso salvato.
    """
    conn = connect(conf)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            data = dict(patient_data)
            data['id'] = patient_id
            data['aggiorna_tdee'] = aggiorna_tdee
            data['tdee_kcal'] = tdee_kcal

            query = """
                UPDATE patients SET
                    nome = %(nome)s,
                    cognome = %(cognome)s,
                    data_nascita = %(data_nascita)s,
                    altezza_cm = %(altezza_cm)s,
                    sesso = %(sesso)s,
                    stile_vita = %(stile_vita)s,
                    categoria_energetica_professione = %(categoria_energetica_professione)s,
                    descrizione_storia = %(descrizione_storia)s,
                    patologie = %(patologie)s,
                    tdee_kcal = CASE
                        WHEN %(aggiorna_tdee)s THEN %(tdee_kcal)s
                        ELSE tdee_kcal
                    END,
                    tdee_updated_at = CASE
                        WHEN %(aggiorna_tdee)s THEN CURRENT_TIMESTAMP
                        ELSE tdee_updated_at
                    END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %(id)s;
            """
            cur.execute(query, data)

            if aggiorna_tdee and peso_kg is not None and peso_kg > 0:
                cur.execute(
                    """
                    SELECT peso_kg
                    FROM biometrics
                    WHERE patient_id = %s
                      AND peso_kg IS NOT NULL
                    ORDER BY insert_date DESC, id DESC
                    LIMIT 1;
                    """,
                    (patient_id,)
                )
                latest = cur.fetchone()
                latest_weight = float(latest['peso_kg']) if latest and latest['peso_kg'] is not None else None

                if latest_weight is None or abs(latest_weight - float(peso_kg)) > 0.01:
                    cur.execute(
                        """
                        INSERT INTO biometrics (patient_id, peso_kg)
                        VALUES (%s, %s);
                        """,
                        (patient_id, peso_kg)
                    )

            conn.commit()
            return True
    except Exception as e:
        conn.rollback()
        logging.error(f"Errore in update_patient: {e}")
        raise
    finally:
        disconnect(conn)


def get_anamnesis_template(conf, user_id: str) -> list:
    """
    Recupera le domande preimpostate configurate dal nutrizionista.
    """
    conn = connect(conf)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            query = """
                SELECT id, question_text, question_type, order_index
                FROM anamnesis_questions
                WHERE user_id = %s
                ORDER BY order_index;
            """
            cur.execute(query, (user_id,))
            return cur.fetchall()
    except Exception as e:
        logging.error(f"Errore in get_anamnesis_template: {e}")
        raise
    finally:
        disconnect(conn)


def save_anamnesis_answers(conf, patient_id: str, answers: list) -> bool:
    """
    Salva le risposte al questionario di anamnesi.
    'answers' si aspetta una lista di dizionari:
    [{'question_id': '...', 'answer_text': '...'}, ...]
    """
    if not answers:
        return True

    conn = connect(conf)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM anamnesis_answers WHERE patient_id = %s;",
                (patient_id,)
            )

            query = """
                INSERT INTO anamnesis_answers (patient_id, question_id, answer_text)
                VALUES (%s, %s, %s);
            """
            for ans in answers:
                cur.execute(query, (patient_id, ans['question_id'], ans['answer_text']))

            conn.commit()
            return True
    except Exception as e:
        conn.rollback()
        logging.error(f"Errore in save_anamnesis_answers: {e}")
        raise
    finally:
        disconnect(conn)
