import logging
from uuid import uuid4

from psycopg2.extras import Json, RealDictCursor

from Common.functions import connect, disconnect


ALLOWED_BLOCK_TYPES = {
    "STANDARD",
    "SUPERSET",
    "JUMP_SET",
    "TRI_SET",
    "GIANT_SET",
    "CIRCUIT",
}

ALLOWED_TECHNIQUES = {
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
}

ALLOWED_PROGRESSION_METRICS = {
    "LOAD",
    "VOLUME",
    "TUT",
    "DENSITY",
    "REPS",
}


def _normalize_progression_metrics(value) -> list[str]:
    """Normalizza e valida le metriche persistite come TEXT[]."""
    if value is None:
        metrics = ["LOAD"]
    elif isinstance(value, str):
        metrics = [value]
    else:
        metrics = list(value)

    normalized = []
    for metric in metrics:
        metric_normalized = str(metric or "").strip().upper()
        if not metric_normalized:
            continue
        if metric_normalized not in ALLOWED_PROGRESSION_METRICS:
            raise ValueError(
                f"Metrica di progressione non valida: {metric}. "
                f"Valori ammessi: {', '.join(sorted(ALLOWED_PROGRESSION_METRICS))}."
            )
        if metric_normalized not in normalized:
            normalized.append(metric_normalized)

    if not normalized:
        raise ValueError("Selezionare almeno una metrica di progressione.")
    return normalized


def _normalize_exercise_for_db(exercise: dict) -> dict:
    block_type = str(exercise.get("block_type") or "STANDARD").strip().upper()
    technique = str(exercise.get("technique") or "STANDARD").strip().upper()

    if block_type not in ALLOWED_BLOCK_TYPES:
        raise ValueError(
            f"block_type non valido: {block_type}. "
            f"Valori ammessi: {', '.join(sorted(ALLOWED_BLOCK_TYPES))}."
        )
    if technique not in ALLOWED_TECHNIQUES:
        raise ValueError(
            f"technique non valida: {technique}. "
            f"Valori ammessi: {', '.join(sorted(ALLOWED_TECHNIQUES))}."
        )

    block_id = exercise.get("block_id")
    if block_type != "STANDARD" and not block_id:
        raise ValueError(
            f"L'esercizio '{exercise.get('exercise_name')}' appartiene a un blocco "
            f"{block_type} ma non ha block_id."
        )

    technique_params = exercise.get("technique_params") or {}
    if not isinstance(technique_params, dict):
        raise ValueError("technique_params deve essere un dizionario/oggetto JSON.")

    return {
        "id": exercise.get("id"),
        "exercise_name": str(exercise.get("exercise_name") or "").strip(),
        "exercise_order": int(exercise.get("exercise_order") or 0),
        "block_id": block_id,
        "block_type": block_type,
        "block_order": exercise.get("block_order"),
        "block_rounds": exercise.get("block_rounds"),
        "target_sets": exercise.get("target_sets"),
        "target_reps_min": exercise.get("target_reps_min"),
        "target_reps_max": exercise.get("target_reps_max"),
        "target_load_kg": exercise.get("target_load_kg"),
        "target_tut_seconds": exercise.get("target_tut_seconds"),
        "target_rest_seconds": exercise.get("target_rest_seconds"),
        "target_rir": exercise.get("target_rir"),
        "target_rpe": exercise.get("target_rpe"),
        "technique": technique,
        "technique_params": Json(technique_params),
        "progression_metrics": _normalize_progression_metrics(
            exercise.get("progression_metrics")
        ),
        "notes": exercise.get("notes"),
    }


def _validate_exercises_payload(exercises_data: list, session_name: str) -> list[dict]:
    normalized = [_normalize_exercise_for_db(item) for item in (exercises_data or [])]
    if not normalized:
        raise ValueError(f"La sessione '{session_name}' deve contenere almeno un esercizio.")

    orders = []
    for exercise in normalized:
        if not exercise["exercise_name"]:
            raise ValueError(
                f"exercise_name e obbligatorio per tutti gli esercizi della sessione '{session_name}'."
            )
        if exercise["exercise_order"] <= 0:
            raise ValueError("exercise_order deve essere maggiore di zero.")
        orders.append(exercise["exercise_order"])

    if len(orders) != len(set(orders)):
        raise ValueError(
            f"exercise_order deve essere univoco nella sessione '{session_name}'."
        )
    return normalized


def _normalize_sessions_payload(sessions_data: list) -> list[dict]:
    """Valida le sessioni. Supporta anche il vecchio payload flat per compatibilita."""
    sessions_data = list(sessions_data or [])
    if sessions_data and "exercise_name" in sessions_data[0] and "session_name" not in sessions_data[0]:
        sessions_data = [
            {
                "session_name": "Sessione 1",
                "session_order": 1,
                "description": "Sessione creata automaticamente dal formato workout precedente.",
                "notes": None,
                "exercises": sessions_data,
            }
        ]

    if not sessions_data:
        raise ValueError("Inserire almeno una sessione nel workout.")

    normalized = []
    orders = []
    names = []
    for index, raw_session in enumerate(sessions_data, start=1):
        session_name = str(raw_session.get("session_name") or "").strip()
        if not session_name:
            raise ValueError(f"Sessione {index}: il nome e obbligatorio.")

        session_order = int(raw_session.get("session_order") or index)
        if session_order <= 0:
            raise ValueError(f"'{session_name}': session_order deve essere maggiore di zero.")

        name_key = session_name.casefold()
        if name_key in names:
            raise ValueError(f"Nome sessione duplicato nel workout: '{session_name}'.")
        if session_order in orders:
            raise ValueError(f"Ordine sessione duplicato nel workout: {session_order}.")
        names.append(name_key)
        orders.append(session_order)

        normalized.append(
            {
                "id": raw_session.get("id"),
                "session_name": session_name,
                "session_order": session_order,
                "description": raw_session.get("description"),
                "notes": raw_session.get("notes"),
                "exercises": _validate_exercises_payload(
                    raw_session.get("exercises") or [], session_name
                ),
            }
        )

    normalized.sort(key=lambda item: item["session_order"])
    return normalized


def _insert_workout_session(cur, workout_plan_id, session: dict) -> str:
    cur.execute(
        """
        INSERT INTO workout_sessions (
            workout_plan_id,
            session_name,
            session_order,
            description,
            notes
        ) VALUES (
            %(workout_plan_id)s,
            %(session_name)s,
            %(session_order)s,
            %(description)s,
            %(notes)s
        )
        RETURNING id;
        """,
        {**session, "workout_plan_id": workout_plan_id},
    )
    return str(cur.fetchone()["id"])


def _update_workout_session(cur, workout_plan_id, session: dict) -> None:
    cur.execute(
        """
        UPDATE workout_sessions
        SET session_name = %(session_name)s,
            session_order = %(session_order)s,
            description = %(description)s,
            notes = %(notes)s,
            archived_at = NULL,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = %(id)s
          AND workout_plan_id = %(workout_plan_id)s;
        """,
        {**session, "workout_plan_id": workout_plan_id},
    )
    if cur.rowcount != 1:
        raise ValueError(f"Sessione {session.get('id')} non trovata nel workout selezionato.")


def _insert_workout_exercise(cur, workout_session_id, exercise: dict) -> str:
    cur.execute(
        """
        INSERT INTO workout_exercises (
            workout_session_id,
            exercise_name,
            exercise_order,
            block_id,
            block_type,
            block_order,
            block_rounds,
            target_sets,
            target_reps_min,
            target_reps_max,
            target_load_kg,
            target_tut_seconds,
            target_rest_seconds,
            target_rir,
            target_rpe,
            technique,
            technique_params,
            progression_metrics,
            notes
        ) VALUES (
            %(workout_session_id)s,
            %(exercise_name)s,
            %(exercise_order)s,
            %(block_id)s,
            %(block_type)s,
            %(block_order)s,
            %(block_rounds)s,
            %(target_sets)s,
            %(target_reps_min)s,
            %(target_reps_max)s,
            %(target_load_kg)s,
            %(target_tut_seconds)s,
            %(target_rest_seconds)s,
            %(target_rir)s,
            %(target_rpe)s,
            %(technique)s,
            %(technique_params)s,
            %(progression_metrics)s,
            %(notes)s
        )
        RETURNING id;
        """,
        {**exercise, "workout_session_id": workout_session_id},
    )
    return str(cur.fetchone()["id"])


def _update_workout_exercise(cur, workout_session_id, exercise: dict) -> None:
    cur.execute(
        """
        UPDATE workout_exercises
        SET exercise_name = %(exercise_name)s,
            exercise_order = %(exercise_order)s,
            block_id = %(block_id)s,
            block_type = %(block_type)s,
            block_order = %(block_order)s,
            block_rounds = %(block_rounds)s,
            target_sets = %(target_sets)s,
            target_reps_min = %(target_reps_min)s,
            target_reps_max = %(target_reps_max)s,
            target_load_kg = %(target_load_kg)s,
            target_tut_seconds = %(target_tut_seconds)s,
            target_rest_seconds = %(target_rest_seconds)s,
            target_rir = %(target_rir)s,
            target_rpe = %(target_rpe)s,
            technique = %(technique)s,
            technique_params = %(technique_params)s,
            progression_metrics = %(progression_metrics)s,
            notes = %(notes)s,
            updated_at = CURRENT_TIMESTAMP,
            archived_at = NULL
        WHERE id = %(id)s
          AND workout_session_id = %(workout_session_id)s;
        """,
        {**exercise, "workout_session_id": workout_session_id},
    )
    if cur.rowcount != 1:
        raise ValueError(
            f"Esercizio {exercise.get('id')} non trovato nella sessione selezionata. "
            "Un esercizio gia storicizzato non puo essere spostato tra sessioni mantenendo lo stesso UUID."
        )


def get_workout_plans(conf, patient_id: str, include_archived: bool = False) -> list:
    """Listing dei workout con conteggio sessioni, esercizi e rilevazioni."""
    conn = connect(conf)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    wp.id,
                    wp.workout_name,
                    wp.patient_id,
                    wp.user_id,
                    wp.objective,
                    wp.description,
                    wp.start_date,
                    wp.end_date,
                    wp.created_at,
                    wp.updated_at,
                    wp.archived_at,
                    COUNT(DISTINCT ws.id) FILTER (
                        WHERE ws.archived_at IS NULL
                    ) AS session_count,
                    COUNT(DISTINCT we.id) FILTER (
                        WHERE ws.archived_at IS NULL AND we.archived_at IS NULL
                    ) AS exercise_count,
                    COUNT(DISTINCT wm.id) AS measurement_count,
                    MAX(wm.measurement_date) AS last_measurement_at
                FROM workout_plans wp
                LEFT JOIN workout_sessions ws
                    ON wp.id = ws.workout_plan_id
                LEFT JOIN workout_exercises we
                    ON ws.id = we.workout_session_id
                LEFT JOIN workout_measurements wm
                    ON we.id = wm.workout_exercise_id
                WHERE wp.patient_id = %s
                  AND (%s OR wp.archived_at IS NULL)
                GROUP BY wp.id
                ORDER BY
                    CASE WHEN wp.archived_at IS NULL THEN 0 ELSE 1 END,
                    wp.created_at DESC,
                    wp.workout_name;
                """,
                (patient_id, include_archived),
            )
            return cur.fetchall()
    except Exception as exc:
        logging.error("Errore in get_workout_plans: %s", exc)
        raise
    finally:
        disconnect(conn)


def get_workout_plan(
    conf,
    patient_id: str,
    workout_id,
    include_archived_exercises: bool = False,
    include_archived_sessions: bool = False,
) -> dict | None:
    """Recupera il workout con sessioni annidate e una vista flat degli esercizi."""
    conn = connect(conf)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, workout_name, patient_id, user_id, objective, description,
                       start_date, end_date, created_at, updated_at, archived_at
                FROM workout_plans
                WHERE id = %s AND patient_id = %s;
                """,
                (workout_id, patient_id),
            )
            plan = cur.fetchone()
            if plan is None:
                return None

            cur.execute(
                """
                SELECT id, workout_plan_id, session_name, session_order,
                       description, notes, created_at, updated_at, archived_at
                FROM workout_sessions
                WHERE workout_plan_id = %s
                  AND (%s OR archived_at IS NULL)
                ORDER BY session_order, created_at, id;
                """,
                (workout_id, include_archived_sessions),
            )
            sessions = cur.fetchall()

            flat_exercises = []
            for session in sessions:
                cur.execute(
                    """
                    SELECT
                        id,
                        workout_session_id,
                        exercise_name,
                        exercise_order,
                        block_id,
                        block_type,
                        block_order,
                        block_rounds,
                        target_sets,
                        target_reps_min,
                        target_reps_max,
                        target_load_kg,
                        target_tut_seconds,
                        target_rest_seconds,
                        target_rir,
                        target_rpe,
                        technique,
                        technique_params,
                        progression_metrics,
                        notes,
                        created_at,
                        updated_at,
                        archived_at
                    FROM workout_exercises
                    WHERE workout_session_id = %s
                      AND (%s OR archived_at IS NULL)
                    ORDER BY exercise_order, block_order NULLS FIRST, id;
                    """,
                    (session["id"], include_archived_exercises),
                )
                exercises = cur.fetchall()
                for exercise in exercises:
                    exercise["session_name"] = session["session_name"]
                    exercise["session_order"] = session["session_order"]
                    flat_exercises.append(exercise)
                session["exercises"] = exercises

            plan["sessions"] = sessions
            plan["exercises"] = flat_exercises
            return plan
    except Exception as exc:
        logging.error("Errore in get_workout_plan: %s", exc)
        raise
    finally:
        disconnect(conn)


def get_workout_sessions(
    conf,
    patient_id: str,
    workout_id,
    include_archived: bool = False,
) -> list:
    """Restituisce le sessioni di un workout verificandone l'ownership."""
    plan = get_workout_plan(
        conf,
        patient_id,
        workout_id,
        include_archived_sessions=include_archived,
    )
    return plan.get("sessions", []) if plan else []


def workout_name_exists(
    conf,
    patient_id: str,
    workout_name: str,
    exclude_workout_id=None,
) -> bool:
    conn = connect(conf)
    try:
        with conn.cursor() as cur:
            params = [patient_id, workout_name.strip()]
            query = """
                SELECT 1
                FROM workout_plans
                WHERE patient_id = %s
                  AND archived_at IS NULL
                  AND LOWER(TRIM(workout_name)) = LOWER(TRIM(%s))
            """
            if exclude_workout_id is not None:
                query += " AND id <> %s"
                params.append(exclude_workout_id)
            query += " LIMIT 1;"
            cur.execute(query, tuple(params))
            return cur.fetchone() is not None
    except Exception as exc:
        logging.error("Errore nel controllo unicita nome workout: %s", exc)
        raise
    finally:
        disconnect(conn)


def add_workout_plan(conf, workout_data: dict, sessions_data: list) -> str:
    """Crea piano, sessioni ed esercizi in una singola transazione."""
    workout_name = str(workout_data.get("workout_name") or "").strip()
    if not workout_name:
        raise ValueError("workout_name e obbligatorio.")
    if not workout_data.get("patient_id"):
        raise ValueError("patient_id e obbligatorio.")
    if not workout_data.get("user_id"):
        raise ValueError("user_id e obbligatorio.")

    sessions = _normalize_sessions_payload(sessions_data)
    conn = connect(conf)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO workout_plans (
                    workout_name, patient_id, user_id, objective, description,
                    start_date, end_date
                ) VALUES (
                    %(workout_name)s, %(patient_id)s, %(user_id)s, %(objective)s,
                    %(description)s, %(start_date)s, %(end_date)s
                )
                RETURNING id;
                """,
                {
                    "workout_name": workout_name,
                    "patient_id": workout_data.get("patient_id"),
                    "user_id": workout_data.get("user_id"),
                    "objective": workout_data.get("objective"),
                    "description": workout_data.get("description"),
                    "start_date": workout_data.get("start_date"),
                    "end_date": workout_data.get("end_date"),
                },
            )
            workout_id = str(cur.fetchone()["id"])

            for session in sessions:
                session_id = _insert_workout_session(cur, workout_id, session)
                for exercise in session["exercises"]:
                    _insert_workout_exercise(cur, session_id, exercise)

        conn.commit()
        logging.info("Workout %s inserito con successo.", workout_id)
        return workout_id
    except Exception as exc:
        conn.rollback()
        logging.error("Errore in add_workout_plan: %s", exc)
        raise
    finally:
        disconnect(conn)


def update_workout_plan(
    conf,
    workout_id,
    workout_data: dict,
    sessions_data: list,
) -> str:
    """Aggiorna piano/sessioni/esercizi preservando gli UUID gia storicizzati."""
    workout_name = str(workout_data.get("workout_name") or "").strip()
    patient_id = workout_data.get("patient_id")
    if not workout_name:
        raise ValueError("workout_name e obbligatorio.")
    if not patient_id:
        raise ValueError("patient_id e obbligatorio.")

    sessions = _normalize_sessions_payload(sessions_data)
    conn = connect(conf)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                UPDATE workout_plans
                SET workout_name = %(workout_name)s,
                    objective = %(objective)s,
                    description = %(description)s,
                    start_date = %(start_date)s,
                    end_date = %(end_date)s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %(workout_id)s
                  AND patient_id = %(patient_id)s
                  AND archived_at IS NULL;
                """,
                {
                    "workout_id": workout_id,
                    "patient_id": patient_id,
                    "workout_name": workout_name,
                    "objective": workout_data.get("objective"),
                    "description": workout_data.get("description"),
                    "start_date": workout_data.get("start_date"),
                    "end_date": workout_data.get("end_date"),
                },
            )
            if cur.rowcount != 1:
                raise ValueError("Workout attivo non trovato per l'assistito selezionato.")

            cur.execute(
                """
                SELECT id
                FROM workout_sessions
                WHERE workout_plan_id = %s
                FOR UPDATE;
                """,
                (workout_id,),
            )
            existing_session_ids = {str(row["id"]) for row in cur.fetchall()}
            incoming_session_ids = {
                str(session["id"])
                for session in sessions
                if session.get("id") is not None
            }
            unknown_sessions = incoming_session_ids - existing_session_ids
            if unknown_sessions:
                raise ValueError(
                    "Una o piu sessioni non appartengono al workout selezionato: "
                    + ", ".join(sorted(unknown_sessions))
                )

            session_ids_to_archive = existing_session_ids - incoming_session_ids
            if session_ids_to_archive:
                # Archivia prima le sessioni rimosse: cosi un nuovo tab puo riutilizzare
                # subito nome/ordine di una sessione eliminata senza collisioni univoche.
                cur.execute(
                    """
                    UPDATE workout_exercises
                    SET archived_at = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE workout_session_id = ANY(%s::uuid[])
                      AND archived_at IS NULL;
                    """,
                    (list(session_ids_to_archive),),
                )
                cur.execute(
                    """
                    UPDATE workout_sessions
                    SET archived_at = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE workout_plan_id = %s
                      AND id = ANY(%s::uuid[])
                      AND archived_at IS NULL;
                    """,
                    (workout_id, list(session_ids_to_archive)),
                )

            # Archivia temporaneamente tutte le sessioni attive.
            # Gli indici univoci considerano solo archived_at IS NULL,
            # quindi possiamo riapplicare direttamente nomi e ordini senza
            # utilizzare offset incompatibili con SMALLINT.
            cur.execute(
                """
                UPDATE workout_sessions
                SET archived_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE workout_plan_id = %s
                AND archived_at IS NULL;
                """,
                (workout_id,),
            )

            for session in sessions:
                if session.get("id") is not None:
                    session_id = str(session["id"])
                    _update_workout_session(cur, workout_id, session)
                else:
                    session_id = _insert_workout_session(cur, workout_id, session)

                cur.execute(
                    """
                    SELECT id
                    FROM workout_exercises
                    WHERE workout_session_id = %s
                    FOR UPDATE;
                    """,
                    (session_id,),
                )
                existing_exercise_ids = {str(row["id"]) for row in cur.fetchall()}
                incoming_exercise_ids = {
                    str(exercise["id"])
                    for exercise in session["exercises"]
                    if exercise.get("id") is not None
                }
                unknown_exercises = incoming_exercise_ids - existing_exercise_ids
                if unknown_exercises:
                    raise ValueError(
                        f"La sessione '{session['session_name']}' contiene esercizi che "
                        "non le appartengono: " + ", ".join(sorted(unknown_exercises))
                    )

                # Archivia temporaneamente tutti gli esercizi della sessione.
                # In questo modo l'indice UNIQUE sull'ordine non genera collisioni
                # durante il riordinamento. Gli esercizi ancora presenti verranno
                # riattivati da _update_workout_exercise().
                cur.execute(
                    """
                    UPDATE workout_exercises
                    SET archived_at = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE workout_session_id = %s
                    AND archived_at IS NULL;
                    """,
                    (session_id,),
                )

                for exercise in session["exercises"]:
                    if exercise.get("id") is not None:
                        _update_workout_exercise(cur, session_id, exercise)
                    else:
                        _insert_workout_exercise(cur, session_id, exercise)

                # exercise_ids_to_archive = existing_exercise_ids - incoming_exercise_ids
                # if exercise_ids_to_archive:
                #     cur.execute(
                #         """
                #         UPDATE workout_exercises
                #         SET archived_at = CURRENT_TIMESTAMP,
                #             updated_at = CURRENT_TIMESTAMP
                #         WHERE workout_session_id = %s
                #           AND id = ANY(%s::uuid[])
                #           AND archived_at IS NULL;
                #         """,
                #         (session_id, list(exercise_ids_to_archive)),
                #     )

        conn.commit()
        logging.info("Workout %s aggiornato con successo.", workout_id)
        return str(workout_id)
    except Exception as exc:
        conn.rollback()
        logging.error("Errore in update_workout_plan: %s", exc)
        raise
    finally:
        disconnect(conn)


def archive_workout_plan(conf, patient_id: str, workout_id) -> None:
    conn = connect(conf)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE workout_plans
                SET archived_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                  AND patient_id = %s
                  AND archived_at IS NULL;
                """,
                (workout_id, patient_id),
            )
            if cur.rowcount != 1:
                raise ValueError("Workout attivo non trovato per l'assistito selezionato.")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        disconnect(conn)


def delete_workout_plan(conf, patient_id: str, workout_id) -> None:
    archive_workout_plan(conf, patient_id, workout_id)


def restore_workout_plan(conf, patient_id: str, workout_id) -> None:
    conn = connect(conf)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE workout_plans
                SET archived_at = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                  AND patient_id = %s
                  AND archived_at IS NOT NULL;
                """,
                (workout_id, patient_id),
            )
            if cur.rowcount != 1:
                raise ValueError("Workout archiviato non trovato per l'assistito selezionato.")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        disconnect(conn)


def add_workout_measurements(
    conf,
    patient_id: str,
    workout_exercise_id,
    measurements_data: list,
    execution_id=None,
) -> str:
    """Registra serie/segmenti di un esercizio per una specifica esecuzione reale."""
    if not measurements_data:
        raise ValueError("Inserire almeno una misurazione.")
    execution_id = str(execution_id or uuid4())

    conn = connect(conf)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT we.id
                FROM workout_exercises we
                JOIN workout_sessions ws ON ws.id = we.workout_session_id
                JOIN workout_plans wp ON wp.id = ws.workout_plan_id
                WHERE we.id = %s
                  AND wp.patient_id = %s
                  AND wp.archived_at IS NULL
                  AND ws.archived_at IS NULL
                  AND we.archived_at IS NULL
                FOR UPDATE;
                """,
                (workout_exercise_id, patient_id),
            )
            if cur.fetchone() is None:
                raise ValueError("Esercizio attivo non trovato per l'assistito selezionato.")

            query = """
                INSERT INTO workout_measurements (
                    workout_exercise_id, execution_id, measurement_date,
                    set_number, segment_number, reps_completed, load_kg,
                    tut_seconds, rest_seconds, duration_seconds, rir, rpe, notes
                ) VALUES (
                    %(workout_exercise_id)s, %(execution_id)s, %(measurement_date)s,
                    %(set_number)s, %(segment_number)s, %(reps_completed)s, %(load_kg)s,
                    %(tut_seconds)s, %(rest_seconds)s, %(duration_seconds)s,
                    %(rir)s, %(rpe)s, %(notes)s
                );
            """
            seen_keys = set()
            for raw_measurement in measurements_data:
                set_number = int(raw_measurement.get("set_number") or 1)
                segment_number = int(raw_measurement.get("segment_number") or 1)
                if set_number <= 0 or segment_number <= 0:
                    raise ValueError("set_number e segment_number devono essere maggiori di zero.")
                logical_key = (set_number, segment_number)
                if logical_key in seen_keys:
                    raise ValueError(
                        f"Misurazione duplicata per set {set_number}, segmento {segment_number}."
                    )
                seen_keys.add(logical_key)
                cur.execute(
                    query,
                    {
                        "workout_exercise_id": workout_exercise_id,
                        "execution_id": execution_id,
                        "measurement_date": raw_measurement.get("measurement_date"),
                        "set_number": set_number,
                        "segment_number": segment_number,
                        "reps_completed": raw_measurement.get("reps_completed"),
                        "load_kg": raw_measurement.get("load_kg"),
                        "tut_seconds": raw_measurement.get("tut_seconds"),
                        "rest_seconds": raw_measurement.get("rest_seconds"),
                        "duration_seconds": raw_measurement.get("duration_seconds"),
                        "rir": raw_measurement.get("rir"),
                        "rpe": raw_measurement.get("rpe"),
                        "notes": raw_measurement.get("notes"),
                    },
                )

        conn.commit()
        return execution_id
    except Exception as exc:
        conn.rollback()
        logging.error("Errore in add_workout_measurements: %s", exc)
        raise
    finally:
        disconnect(conn)


def add_workout_session_measurements(
    conf,
    patient_id: str,
    workout_session_id,
    measurements_data: list,
    execution_id=None,
) -> str:
    """Registra atomically un'intera sessione allenante su piu esercizi."""
    if not measurements_data:
        raise ValueError("Inserire almeno una misurazione.")
    execution_id = str(execution_id or uuid4())

    conn = connect(conf)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT ws.id
                FROM workout_sessions ws
                JOIN workout_plans wp ON wp.id = ws.workout_plan_id
                WHERE ws.id = %s
                  AND wp.patient_id = %s
                  AND wp.archived_at IS NULL
                  AND ws.archived_at IS NULL
                FOR UPDATE;
                """,
                (workout_session_id, patient_id),
            )
            if cur.fetchone() is None:
                raise ValueError("Sessione workout attiva non trovata per l'assistito selezionato.")

            cur.execute(
                """
                SELECT id
                FROM workout_exercises
                WHERE workout_session_id = %s
                  AND archived_at IS NULL
                FOR UPDATE;
                """,
                (workout_session_id,),
            )
            valid_exercise_ids = {str(row["id"]) for row in cur.fetchall()}

            query = """
                INSERT INTO workout_measurements (
                    workout_exercise_id, execution_id, measurement_date,
                    set_number, segment_number, reps_completed, load_kg,
                    tut_seconds, rest_seconds, duration_seconds, rir, rpe, notes
                ) VALUES (
                    %(workout_exercise_id)s, %(execution_id)s, %(measurement_date)s,
                    %(set_number)s, %(segment_number)s, %(reps_completed)s, %(load_kg)s,
                    %(tut_seconds)s, %(rest_seconds)s, %(duration_seconds)s,
                    %(rir)s, %(rpe)s, %(notes)s
                );
            """

            seen_keys = set()
            for raw in measurements_data:
                exercise_id = str(raw.get("workout_exercise_id") or "")
                if exercise_id not in valid_exercise_ids:
                    raise ValueError(
                        f"Esercizio {exercise_id} non appartiene alla sessione selezionata."
                    )

                set_number = int(raw.get("set_number") or 1)
                segment_number = int(raw.get("segment_number") or 1)
                if set_number <= 0 or segment_number <= 0:
                    raise ValueError("set_number e segment_number devono essere maggiori di zero.")

                logical_key = (exercise_id, set_number, segment_number)
                if logical_key in seen_keys:
                    raise ValueError(
                        f"Misurazione duplicata per esercizio {exercise_id}, "
                        f"set {set_number}, segmento {segment_number}."
                    )
                seen_keys.add(logical_key)

                cur.execute(
                    query,
                    {
                        "workout_exercise_id": exercise_id,
                        "execution_id": execution_id,
                        "measurement_date": raw.get("measurement_date"),
                        "set_number": set_number,
                        "segment_number": segment_number,
                        "reps_completed": raw.get("reps_completed"),
                        "load_kg": raw.get("load_kg"),
                        "tut_seconds": raw.get("tut_seconds"),
                        "rest_seconds": raw.get("rest_seconds"),
                        "duration_seconds": raw.get("duration_seconds"),
                        "rir": raw.get("rir"),
                        "rpe": raw.get("rpe"),
                        "notes": raw.get("notes"),
                    },
                )

        conn.commit()
        return execution_id
    except Exception as exc:
        conn.rollback()
        logging.error("Errore in add_workout_session_measurements: %s", exc)
        raise
    finally:
        disconnect(conn)


def update_workout_measurements_batch(
    conf,
    patient_id: str,
    measurements_data: list,
) -> None:
    """Aggiorna piu righe dello storico in un'unica transazione."""
    measurements_data = list(measurements_data or [])
    if not measurements_data:
        raise ValueError("Nessuna misurazione da aggiornare.")

    measurement_ids = [str(item.get("id") or "") for item in measurements_data]
    if any(not item for item in measurement_ids):
        raise ValueError("Tutte le misurazioni devono avere un id.")

    conn = connect(conf)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT wm.id
                FROM workout_measurements wm
                JOIN workout_exercises we ON we.id = wm.workout_exercise_id
                JOIN workout_sessions ws ON ws.id = we.workout_session_id
                JOIN workout_plans wp ON wp.id = ws.workout_plan_id
                WHERE wm.id = ANY(%s::uuid[])
                  AND wp.patient_id = %s
                FOR UPDATE OF wm;
                """,
                (measurement_ids, patient_id),
            )
            owned_ids = {str(row["id"]) for row in cur.fetchall()}
            missing_ids = set(measurement_ids) - owned_ids
            if missing_ids:
                raise ValueError(
                    "Una o piu misurazioni non appartengono all'assistito selezionato: "
                    + ", ".join(sorted(missing_ids))
                )

            query = """
                UPDATE workout_measurements
                SET measurement_date = %(measurement_date)s,
                    set_number = %(set_number)s,
                    segment_number = %(segment_number)s,
                    reps_completed = %(reps_completed)s,
                    load_kg = %(load_kg)s,
                    tut_seconds = %(tut_seconds)s,
                    rest_seconds = %(rest_seconds)s,
                    duration_seconds = %(duration_seconds)s,
                    rir = %(rir)s,
                    rpe = %(rpe)s,
                    notes = %(notes)s
                WHERE id = %(id)s;
            """
            for raw in measurements_data:
                set_number = int(raw.get("set_number") or 1)
                segment_number = int(raw.get("segment_number") or 1)
                if set_number <= 0 or segment_number <= 0:
                    raise ValueError("set_number e segment_number devono essere maggiori di zero.")
                cur.execute(
                    query,
                    {
                        "id": str(raw["id"]),
                        "measurement_date": raw.get("measurement_date"),
                        "set_number": set_number,
                        "segment_number": segment_number,
                        "reps_completed": raw.get("reps_completed"),
                        "load_kg": raw.get("load_kg"),
                        "tut_seconds": raw.get("tut_seconds"),
                        "rest_seconds": raw.get("rest_seconds"),
                        "duration_seconds": raw.get("duration_seconds"),
                        "rir": raw.get("rir"),
                        "rpe": raw.get("rpe"),
                        "notes": raw.get("notes"),
                    },
                )
                if cur.rowcount != 1:
                    raise ValueError(f"Misurazione {raw['id']} non aggiornata.")

        conn.commit()
    except Exception as exc:
        conn.rollback()
        logging.error("Errore in update_workout_measurements_batch: %s", exc)
        raise
    finally:
        disconnect(conn)


def update_workout_measurement(
    conf,
    patient_id: str,
    measurement_id,
    measurement_data: dict,
) -> str:
    conn = connect(conf)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE workout_measurements wm
                SET measurement_date = COALESCE(%(measurement_date)s, wm.measurement_date),
                    set_number = %(set_number)s,
                    segment_number = %(segment_number)s,
                    reps_completed = %(reps_completed)s,
                    load_kg = %(load_kg)s,
                    tut_seconds = %(tut_seconds)s,
                    rest_seconds = %(rest_seconds)s,
                    duration_seconds = %(duration_seconds)s,
                    rir = %(rir)s,
                    rpe = %(rpe)s,
                    notes = %(notes)s
                FROM workout_exercises we
                JOIN workout_sessions ws ON ws.id = we.workout_session_id
                JOIN workout_plans wp ON wp.id = ws.workout_plan_id
                WHERE wm.id = %(measurement_id)s
                  AND wm.workout_exercise_id = we.id
                  AND wp.patient_id = %(patient_id)s;
                """,
                {
                    "measurement_id": measurement_id,
                    "patient_id": patient_id,
                    "measurement_date": measurement_data.get("measurement_date"),
                    "set_number": int(measurement_data.get("set_number") or 1),
                    "segment_number": int(measurement_data.get("segment_number") or 1),
                    "reps_completed": measurement_data.get("reps_completed"),
                    "load_kg": measurement_data.get("load_kg"),
                    "tut_seconds": measurement_data.get("tut_seconds"),
                    "rest_seconds": measurement_data.get("rest_seconds"),
                    "duration_seconds": measurement_data.get("duration_seconds"),
                    "rir": measurement_data.get("rir"),
                    "rpe": measurement_data.get("rpe"),
                    "notes": measurement_data.get("notes"),
                },
            )
            if cur.rowcount != 1:
                raise ValueError("Misurazione workout non trovata per l'assistito selezionato.")
        conn.commit()
        return str(measurement_id)
    except Exception:
        conn.rollback()
        raise
    finally:
        disconnect(conn)


def delete_workout_measurement(conf, patient_id: str, measurement_id) -> None:
    conn = connect(conf)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM workout_measurements wm
                USING workout_exercises we, workout_sessions ws, workout_plans wp
                WHERE wm.id = %s
                  AND wm.workout_exercise_id = we.id
                  AND we.workout_session_id = ws.id
                  AND ws.workout_plan_id = wp.id
                  AND wp.patient_id = %s;
                """,
                (measurement_id, patient_id),
            )
            if cur.rowcount != 1:
                raise ValueError("Misurazione workout non trovata per l'assistito selezionato.")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        disconnect(conn)


def get_workout_measurements(
    conf,
    patient_id: str,
    workout_id=None,
    workout_session_id=None,
    workout_exercise_id=None,
    execution_id=None,
) -> list:
    conn = connect(conf)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            filters = ["wp.patient_id = %s"]
            params = [patient_id]
            if workout_id is not None:
                filters.append("wp.id = %s")
                params.append(workout_id)
            if workout_session_id is not None:
                filters.append("ws.id = %s")
                params.append(workout_session_id)
            if workout_exercise_id is not None:
                filters.append("we.id = %s")
                params.append(workout_exercise_id)
            if execution_id is not None:
                filters.append("wm.execution_id = %s")
                params.append(execution_id)

            query = f"""
                SELECT
                    wm.id,
                    wm.workout_exercise_id,
                    we.workout_session_id,
                    ws.workout_plan_id,
                    wp.workout_name,
                    ws.session_name,
                    ws.session_order,
                    we.exercise_name,
                    we.exercise_order,
                    wm.execution_id,
                    wm.measurement_date,
                    wm.set_number,
                    wm.segment_number,
                    wm.reps_completed,
                    wm.load_kg,
                    wm.tut_seconds,
                    wm.rest_seconds,
                    wm.duration_seconds,
                    wm.rir,
                    wm.rpe,
                    wm.notes,
                    wm.created_at
                FROM workout_measurements wm
                JOIN workout_exercises we ON we.id = wm.workout_exercise_id
                JOIN workout_sessions ws ON ws.id = we.workout_session_id
                JOIN workout_plans wp ON wp.id = ws.workout_plan_id
                WHERE {' AND '.join(filters)}
                ORDER BY
                    wm.measurement_date DESC,
                    wm.execution_id,
                    ws.session_order,
                    we.exercise_order,
                    wm.set_number,
                    wm.segment_number;
            """
            cur.execute(query, tuple(params))
            return cur.fetchall()
    except Exception as exc:
        logging.error("Errore in get_workout_measurements: %s", exc)
        raise
    finally:
        disconnect(conn)


def get_exercise_progress(conf, patient_id: str, workout_exercise_id) -> list:
    """Aggrega carico, volume, TUT, densita e percezione per execution_id."""
    conn = connect(conf)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    wm.execution_id,
                    MIN(wm.measurement_date) AS measurement_date,
                    COUNT(DISTINCT wm.set_number) AS sets_completed,
                    SUM(COALESCE(wm.reps_completed, 0)) AS reps_completed,
                    MAX(wm.load_kg) AS peak_load_kg,
                    SUM(COALESCE(wm.reps_completed, 0) * COALESCE(wm.load_kg, 0)) AS volume_kg,
                    SUM(COALESCE(wm.tut_seconds, 0)) AS total_tut_seconds,
                    SUM(COALESCE(wm.rest_seconds, 0)) AS total_rest_seconds,
                    SUM(COALESCE(wm.duration_seconds, 0)) AS total_duration_seconds,
                    CASE
                        WHEN SUM(COALESCE(wm.duration_seconds, 0)) > 0
                        THEN ROUND(
                            SUM(COALESCE(wm.reps_completed, 0) * COALESCE(wm.load_kg, 0))
                            / (SUM(COALESCE(wm.duration_seconds, 0)) / 60.0),
                            2
                        )
                        ELSE NULL
                    END AS density_kg_per_minute,
                    ROUND(AVG(wm.rir), 2) AS avg_rir,
                    ROUND(AVG(wm.rpe), 2) AS avg_rpe
                FROM workout_measurements wm
                JOIN workout_exercises we ON we.id = wm.workout_exercise_id
                JOIN workout_sessions ws ON ws.id = we.workout_session_id
                JOIN workout_plans wp ON wp.id = ws.workout_plan_id
                WHERE wm.workout_exercise_id = %s
                  AND wp.patient_id = %s
                GROUP BY wm.execution_id
                ORDER BY MIN(wm.measurement_date), wm.execution_id;
                """,
                (workout_exercise_id, patient_id),
            )
            return cur.fetchall()
    except Exception as exc:
        logging.error("Errore in get_exercise_progress: %s", exc)
        raise
    finally:
        disconnect(conn)
