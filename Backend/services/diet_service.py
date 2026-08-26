import logging
from decimal import Decimal
from psycopg2.extras import RealDictCursor
from Common.functions import connect, disconnect


def _normalize_item_for_db(item: dict) -> dict:
    """Converte il modello usato dalla UI nelle colonne persistite in diet_meal_items."""
    return {
        "food_id": item.get("food_id"),
        "giorno_settimana": item.get("giorno_settimana"),
        "meal_type": item.get("meal_type"),
        "food_name": item.get("food_name"),
        "grams": item.get("grams", 0),
        "kcal_calculated": item.get("kcal_calculated", item.get("kcal", 0)),
        "prot_calculated": item.get("prot_calculated", item.get("prot", 0)),
        "carbs_calculated": item.get("carbs_calculated", item.get("carbs", 0)),
        "fats_calculated": item.get("fats_calculated", item.get("fats", 0)),
    }


def _insert_diet_items(cur, diet_id, items_data: list) -> None:
    """Inserisce gli item di un piano usando un set di colonne esplicito e stabile."""
    query = """
        INSERT INTO diet_meal_items (
            diet_plan_id, food_id, giorno_settimana, meal_type, food_name, grams,
            kcal_calculated, prot_calculated, carbs_calculated, fats_calculated
        )
        VALUES (
            %(diet_plan_id)s, %(food_id)s, %(giorno_settimana)s, %(meal_type)s, %(food_name)s, %(grams)s,
            %(kcal_calculated)s, %(prot_calculated)s, %(carbs_calculated)s, %(fats_calculated)s
        );
    """
    for raw_item in items_data:
        item = _normalize_item_for_db(raw_item)
        item["diet_plan_id"] = diet_id
        if not item.get("food_id"):
            raise ValueError(
                f"food_id mancante per l'alimento '{item.get('food_name')}'. "
                "L'alimento deve essere presente nel catalogo foods prima del salvataggio."
            )
        cur.execute(query, item)


def get_diet_plans(conf, patient_id: str) -> list:
    """Recupera i piani alimentari di un assistito raggruppando i relativi item."""
    conn = connect(conf)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    dp.id,
                    dp.diet_name,
                    dp.descrizione,
                    dp.warnings,
                    dmi.food_id,
                    dmi.giorno_settimana,
                    dmi.meal_type,
                    dmi.food_name,
                    dmi.grams,
                    dmi.kcal_calculated,
                    dmi.prot_calculated,
                    dmi.carbs_calculated,
                    dmi.fats_calculated
                FROM diet_plans dp
                LEFT JOIN diet_meal_items dmi ON dp.id = dmi.diet_plan_id
                WHERE dp.patient_id = %s
                ORDER BY dp.diet_name, dmi.giorno_settimana, dmi.meal_type, dmi.food_name;
                """,
                (patient_id,),
            )
            rows = cur.fetchall()

        plans_by_id = {}
        for row in rows:
            diet_id = row["id"]
            if diet_id not in plans_by_id:
                plans_by_id[diet_id] = {
                    "id": diet_id,
                    "diet_name": row["diet_name"],
                    "descrizione": row.get("descrizione"),
                    "warnings": row.get("warnings"),
                    "items": [],
                }

            # LEFT JOIN: un piano senza item deve comunque essere restituito.
            if row.get("giorno_settimana") is not None:
                plans_by_id[diet_id]["items"].append({
                    "food_id": row["food_id"],
                    "giorno_settimana": row["giorno_settimana"],
                    "meal_type": row["meal_type"],
                    "food_name": row["food_name"],
                    "grams": row["grams"],
                    "kcal_calculated": row["kcal_calculated"],
                    "prot_calculated": row["prot_calculated"],
                    "carbs_calculated": row["carbs_calculated"],
                    "fats_calculated": row["fats_calculated"],
                })

        return list(plans_by_id.values())
    except Exception as e:
        logging.error(f"Errore in get_diet_plans: {e}")
        raise
    finally:
        disconnect(conn)


def diet_name_exists(conf, patient_id: str, diet_name: str, exclude_diet_id=None) -> bool:
    """Verifica l'unicita del nome per assistito, ignorando maiuscole e spazi esterni."""
    conn = connect(conf)
    try:
        with conn.cursor() as cur:
            params = [patient_id, diet_name.strip()]
            query = """
                SELECT 1
                FROM diet_plans
                WHERE patient_id = %s
                  AND LOWER(TRIM(diet_name)) = LOWER(TRIM(%s))
            """
            if exclude_diet_id is not None:
                query += " AND id <> %s"
                params.append(exclude_diet_id)
            query += " LIMIT 1;"
            cur.execute(query, tuple(params))
            return cur.fetchone() is not None
    except Exception as e:
        logging.error(f"Errore nel controllo unicita nome piano: {e}")
        raise
    finally:
        disconnect(conn)


def add_diet_plan(conf, diet_data: dict, items_data: list) -> str:
    """Inserisce un nuovo piano alimentare con i rispettivi elementi nel database."""
    conn = connect(conf)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO diet_plans (diet_name, patient_id, user_id, descrizione, warnings)
                VALUES (%(diet_name)s, %(patient_id)s, %(user_id)s, %(descrizione)s, %(warnings)s)
                RETURNING id;
                """,
                diet_data,
            )
            diet_id = cur.fetchone()["id"]
            _insert_diet_items(cur, diet_id, items_data)

        conn.commit()
        logging.info(f"Piano alimentare {diet_id} inserito con successo.")
        return str(diet_id)
    except Exception as e:
        conn.rollback()
        logging.error(f"Errore in add_diet_plan: {e}")
        raise
    finally:
        disconnect(conn)


def update_diet_plan(conf, diet_id, diet_data: dict, items_data: list) -> str:
    """Aggiorna testata e dettaglio di un piano esistente in un'unica transazione."""
    conn = connect(conf)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE diet_plans
                SET diet_name = %(diet_name)s,
                    descrizione = %(descrizione)s,
                    warnings = %(warnings)s
                WHERE id = %(diet_id)s
                  AND patient_id = %(patient_id)s;
                """,
                {
                    **diet_data,
                    "diet_id": diet_id,
                },
            )
            if cur.rowcount != 1:
                raise ValueError("Piano alimentare non trovato per l'assistito selezionato.")

            # Replace atomico del dettaglio: o viene aggiornato tutto, o viene fatto rollback.
            cur.execute("DELETE FROM diet_meal_items WHERE diet_plan_id = %s;", (diet_id,))
            _insert_diet_items(cur, diet_id, items_data)

        conn.commit()
        logging.info(f"Piano alimentare {diet_id} aggiornato con successo.")
        return str(diet_id)
    except Exception as e:
        conn.rollback()
        logging.error(f"Errore in update_diet_plan: {e}")
        raise
    finally:
        disconnect(conn)


def delete_diet_plan(conf, patient_id: str, diet_id) -> None:
    """Elimina un piano alimentare dell'assistito e i relativi item in modo atomico."""
    conn = connect(conf)
    try:
        with conn.cursor() as cur:
            # Verifica ownership prima di eliminare il dettaglio.
            cur.execute(
                """
                SELECT 1
                FROM diet_plans
                WHERE id = %s
                  AND patient_id = %s
                FOR UPDATE;
                """,
                (diet_id, patient_id),
            )
            if cur.fetchone() is None:
                raise ValueError("Piano alimentare non trovato per l'assistito selezionato.")

            # Cancellazione esplicita del dettaglio: non dipende dalla presenza di ON DELETE CASCADE.
            cur.execute("DELETE FROM diet_meal_items WHERE diet_plan_id = %s;", (diet_id,))
            cur.execute(
                "DELETE FROM diet_plans WHERE id = %s AND patient_id = %s;",
                (diet_id, patient_id),
            )

            if cur.rowcount != 1:
                raise ValueError("Impossibile eliminare il piano alimentare selezionato.")

        conn.commit()
        logging.info(f"Piano alimentare {diet_id} eliminato con successo.")
    except Exception as e:
        conn.rollback()
        logging.error(f"Errore in delete_diet_plan: {e}")
        raise
    finally:
        disconnect(conn)


def calculate_nutrients_proportional(food_obj: dict, grams: Decimal) -> dict:
    """
    Calcola i macronutrienti in modo proporzionale in base ai grammi inseriti,
    partendo dai valori nutrizionali di riferimento per 100g presenti in food_obj.
    """
    ratio = grams / Decimal("100.0")

    def get_safe_decimal(val):
        if val is None:
            return Decimal("0.0")
        return Decimal(str(val))

    kcal_100g = get_safe_decimal(food_obj.get("kcal"))
    carbs_100g = get_safe_decimal(food_obj.get("carbs_g"))
    fats_100g = get_safe_decimal(food_obj.get("fats_g"))
    prots_100g = get_safe_decimal(food_obj.get("prots_g"))

    return {
        "kcal": kcal_100g * ratio,
        "carbs": carbs_100g * ratio,
        "fats": fats_100g * ratio,
        "prot": prots_100g * ratio,
    }
