import logging
from decimal import Decimal
from psycopg2.extras import RealDictCursor
from Common.functions import connect, disconnect


def _safe_positive_decimal(value, field_name: str) -> Decimal:
    """Converte un valore numerico e verifica che sia strettamente positivo."""
    try:
        result = Decimal(str(value))
    except Exception as exc:
        raise ValueError(f"{field_name} non valido: {value!r}") from exc
    if result <= 0:
        raise ValueError(f"{field_name} deve essere maggiore di zero.")
    return result


def _normalize_item_for_db(item: dict, recipe_ref_map=None) -> dict:
    """Converte il modello UI nelle colonne persistite in diet_meal_items.

    Una riga rappresenta in modo esclusivo un alimento oppure una ricetta:
    - food_id valorizzato e recipe_id NULL;
    - recipe_id valorizzato e food_id NULL.

    recipe_ref e una chiave client-side usata solo durante la transazione per
    risolvere l'UUID di ricette appena create. Non viene persistita.
    """
    recipe_ref_map = recipe_ref_map or {}

    food_id = item.get("food_id")
    recipe_id = item.get("recipe_id")
    recipe_ref = item.get("recipe_ref") or item.get("recipe_client_key")

    # La chiave client ha precedenza: e fondamentale quando si salva come nuovo
    # un piano importato, perche gli ID delle ricette originali non vanno riusati.
    if recipe_ref is not None and str(recipe_ref) in recipe_ref_map:
        recipe_id = recipe_ref_map[str(recipe_ref)]
    elif recipe_id is not None and str(recipe_id) in recipe_ref_map:
        recipe_id = recipe_ref_map[str(recipe_id)]

    is_food = bool(food_id)
    is_recipe = bool(recipe_id)
    if is_food == is_recipe:
        raise ValueError(
            "Ogni diet_meal_item deve valorizzare uno e un solo riferimento tra food_id e recipe_id."
        )

    grams = _safe_positive_decimal(item.get("grams", 0), "grams")

    food_name = item.get("food_name")
    if is_food:
        food_name = str(food_name or "").strip()
        if not food_name:
            raise ValueError("food_name mancante per un diet_meal_item di tipo food.")
    else:
        # Per le ricette il nome e normalizzato in recipes.name e non viene duplicato.
        food_name = None

    return {
        "food_id": food_id if is_food else None,
        "recipe_id": recipe_id if is_recipe else None,
        "giorno_settimana": item.get("giorno_settimana"),
        "meal_type": item.get("meal_type"),
        "food_name": food_name,
        "grams": grams,
        "kcal_calculated": item.get("kcal_calculated", item.get("kcal")),
        "prot_calculated": item.get("prot_calculated", item.get("prot")),
        "carbs_calculated": item.get("carbs_calculated", item.get("carbs")),
        "fats_calculated": item.get("fats_calculated", item.get("fats")),
    }


def _insert_diet_items(
    cur,
    diet_id,
    items_data: list,
    recipe_ref_map=None,
    allowed_recipe_ids=None,
) -> None:
    """Inserisce gli item del piano supportando food_id XOR recipe_id."""
    query = """
        INSERT INTO diet_meal_items (
            diet_plan_id, food_id, recipe_id, giorno_settimana, meal_type, food_name, grams,
            kcal_calculated, prot_calculated, carbs_calculated, fats_calculated
        )
        VALUES (
            %(diet_plan_id)s, %(food_id)s, %(recipe_id)s, %(giorno_settimana)s,
            %(meal_type)s, %(food_name)s, %(grams)s, %(kcal_calculated)s,
            %(prot_calculated)s, %(carbs_calculated)s, %(fats_calculated)s
        );
    """

    allowed_recipe_ids = (
        {str(value) for value in allowed_recipe_ids}
        if allowed_recipe_ids is not None
        else None
    )

    for raw_item in items_data or []:
        item = _normalize_item_for_db(raw_item, recipe_ref_map=recipe_ref_map)
        item["diet_plan_id"] = diet_id

        if item.get("recipe_id") is not None and allowed_recipe_ids is not None:
            if str(item["recipe_id"]) not in allowed_recipe_ids:
                raise ValueError(
                    f"La ricetta {item['recipe_id']} non appartiene al set di ricette del piano corrente."
                )

        cur.execute(query, item)


def _normalize_recipe_payload(recipe: dict) -> dict:
    """Valida e normalizza una ricetta ricevuta dalla UI."""
    if not isinstance(recipe, dict):
        raise ValueError("Payload ricetta non valido.")

    name = str(recipe.get("name") or "").strip()
    if not name:
        raise ValueError("Ogni ricetta deve avere un nome.")

    try:
        portions = int(recipe.get("portions") or 1)
    except Exception as exc:
        raise ValueError(f"Numero porzioni non valido per la ricetta '{name}'.") from exc
    if portions <= 0:
        raise ValueError(f"Il numero di porzioni della ricetta '{name}' deve essere maggiore di zero.")

    # Aggrega eventuali duplicati dello stesso food_id.
    ingredients_by_food = {}
    for raw in recipe.get("ingredients", []) or []:
        if not isinstance(raw, dict):
            continue
        food_id = raw.get("food_id")
        if not food_id:
            raise ValueError(f"Ingrediente senza food_id nella ricetta '{name}'.")
        grams = _safe_positive_decimal(raw.get("grams", 0), f"grams ingrediente di '{name}'")
        key = str(food_id)
        if key not in ingredients_by_food:
            ingredients_by_food[key] = {
                "food_id": food_id,
                "food_name": str(raw.get("food_name") or "").strip() or None,
                "grams": Decimal("0"),
            }
        ingredients_by_food[key]["grams"] += grams

    ingredients = list(ingredients_by_food.values())
    if not ingredients:
        raise ValueError(f"La ricetta '{name}' deve contenere almeno un ingrediente.")

    recipe_id = recipe.get("id") or recipe.get("recipe_id")
    client_key = recipe.get("client_key") or recipe_id

    return {
        "id": recipe_id,
        "client_key": str(client_key) if client_key is not None else None,
        "name": name,
        "portions": portions,
        "ingredients": ingredients,
    }


def _normalize_recipes_payload(recipes_data: list) -> list:
    recipes = [_normalize_recipe_payload(recipe) for recipe in (recipes_data or [])]
    seen_names = set()
    for recipe in recipes:
        key = recipe["name"].casefold()
        if key in seen_names:
            raise ValueError(f"Nome ricetta duplicato nel piano: '{recipe['name']}'.")
        seen_names.add(key)
    return recipes


def _replace_recipe_ingredients(cur, recipe_id, ingredients: list) -> None:
    """Replace atomico degli ingredienti della singola ricetta."""
    cur.execute("DELETE FROM recipe_ingredients WHERE recipe_id = %s;", (recipe_id,))
    query = """
        INSERT INTO recipe_ingredients (recipe_id, food_id, grams)
        VALUES (%s, %s, %s);
    """
    for ingredient in ingredients:
        cur.execute(
            query,
            (recipe_id, ingredient["food_id"], ingredient["grams"]),
        )


def _insert_recipes_for_new_plan(cur, diet_id, recipes_data: list):
    """Crea nuove ricette per un nuovo piano e restituisce la mappa client_key -> UUID."""
    recipes = _normalize_recipes_payload(recipes_data)
    recipe_ref_map = {}
    created_ids = set()

    for recipe in recipes:
        cur.execute(
            """
            INSERT INTO recipes (name, diet_plan_id, portions)
            VALUES (%s, %s, %s)
            RETURNING id;
            """,
            (recipe["name"], diet_id, recipe["portions"]),
        )
        recipe_id = cur.fetchone()["id"]
        created_ids.add(str(recipe_id))

        # Mappa sia client_key sia l'eventuale vecchio ID. Quest'ultimo serve
        # quando un piano esistente viene importato e poi salvato come nuovo.
        if recipe.get("client_key"):
            recipe_ref_map[str(recipe["client_key"])] = recipe_id
        if recipe.get("id"):
            recipe_ref_map[str(recipe["id"])] = recipe_id
        recipe_ref_map[str(recipe_id)] = recipe_id

        _replace_recipe_ingredients(cur, recipe_id, recipe["ingredients"])

    return recipe_ref_map, created_ids


def _sync_recipes_for_existing_plan(cur, diet_id, recipes_data: list):
    """Sincronizza le ricette del piano, mantenendo gli UUID esistenti quando possibile.

    Restituisce:
    - mapping client_key/id -> recipe_id effettivo;
    - set degli ID da mantenere;
    - set degli ID da eliminare dopo il replace dei meal item.
    """
    recipes = _normalize_recipes_payload(recipes_data)

    cur.execute(
        "SELECT id FROM recipes WHERE diet_plan_id = %s FOR UPDATE;",
        (diet_id,),
    )
    existing_ids = {str(row["id"]) for row in cur.fetchall()}

    recipe_ref_map = {}
    keep_ids = set()

    for recipe in recipes:
        requested_id = str(recipe["id"]) if recipe.get("id") else None
        if requested_id:
            if requested_id not in existing_ids:
                raise ValueError(
                    f"La ricetta {requested_id} non appartiene al piano alimentare {diet_id}."
                )
            recipe_id = recipe["id"]
            cur.execute(
                """
                UPDATE recipes
                SET name = %s, portions = %s
                WHERE id = %s AND diet_plan_id = %s;
                """,
                (recipe["name"], recipe["portions"], recipe_id, diet_id),
            )
            if cur.rowcount != 1:
                raise ValueError(f"Impossibile aggiornare la ricetta {requested_id}.")
        else:
            cur.execute(
                """
                INSERT INTO recipes (name, diet_plan_id, portions)
                VALUES (%s, %s, %s)
                RETURNING id;
                """,
                (recipe["name"], diet_id, recipe["portions"]),
            )
            recipe_id = cur.fetchone()["id"]

        keep_ids.add(str(recipe_id))
        recipe_ref_map[str(recipe_id)] = recipe_id
        if recipe.get("client_key"):
            recipe_ref_map[str(recipe["client_key"])] = recipe_id
        if recipe.get("id"):
            recipe_ref_map[str(recipe["id"])] = recipe_id

        _replace_recipe_ingredients(cur, recipe_id, recipe["ingredients"])

    removed_ids = existing_ids - keep_ids
    return recipe_ref_map, keep_ids, removed_ids


def get_recipes_for_diet(conf, diet_plan_id) -> list:
    """Recupera le ricette del piano con i relativi ingredienti e nomi food."""
    conn = connect(conf)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    r.id AS recipe_id,
                    r.diet_plan_id,
                    r.name,
                    r.portions,
                    ri.id AS ingredient_id,
                    ri.food_id,
                    ri.grams,
                    f.item_name AS food_name
                FROM recipes r
                LEFT JOIN recipe_ingredients ri ON ri.recipe_id = r.id
                LEFT JOIN foods f ON f.id = ri.food_id
                WHERE r.diet_plan_id = %s
                ORDER BY LOWER(r.name), f.item_name;
                """,
                (diet_plan_id,),
            )
            rows = cur.fetchall()

        by_id = {}
        for row in rows:
            recipe_id = row["recipe_id"]
            key = str(recipe_id)
            if key not in by_id:
                by_id[key] = {
                    "id": recipe_id,
                    "recipe_id": recipe_id,
                    "diet_plan_id": row["diet_plan_id"],
                    "name": row["name"],
                    "portions": row["portions"],
                    "ingredients": [],
                }
            if row.get("ingredient_id") is not None:
                by_id[key]["ingredients"].append({
                    "id": row["ingredient_id"],
                    "food_id": row["food_id"],
                    "food_name": row.get("food_name"),
                    "grams": row["grams"],
                })

        return list(by_id.values())
    except Exception as e:
        logging.error(f"Errore in get_recipes_for_diet: {e}")
        raise
    finally:
        disconnect(conn)


def get_diet_plans(conf, patient_id: str) -> list:
    """Recupera i piani alimentari con meal item di tipo food o recipe."""
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
                    dmi.recipe_id,
                    r.name AS recipe_name,
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
                LEFT JOIN recipes r ON r.id = dmi.recipe_id
                WHERE dp.patient_id = %s
                ORDER BY
                    dp.diet_name,
                    dmi.giorno_settimana,
                    dmi.meal_type,
                    COALESCE(dmi.food_name, r.name);
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
                    "food_id": row.get("food_id"),
                    "recipe_id": row.get("recipe_id"),
                    "recipe_name": row.get("recipe_name"),
                    "giorno_settimana": row["giorno_settimana"],
                    "meal_type": row["meal_type"],
                    "food_name": row.get("food_name"),
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


def add_diet_plan_with_recipes(
    conf,
    diet_data: dict,
    items_data: list,
    recipes_data: list,
) -> str:
    """Inserisce piano, ricette, ingredienti e distribuzione in un'unica transazione."""
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

            recipe_ref_map, created_recipe_ids = _insert_recipes_for_new_plan(
                cur, diet_id, recipes_data
            )
            _insert_diet_items(
                cur,
                diet_id,
                items_data,
                recipe_ref_map=recipe_ref_map,
                allowed_recipe_ids=created_recipe_ids,
            )

        conn.commit()
        logging.info(f"Piano alimentare {diet_id} con ricette inserito con successo.")
        return str(diet_id)
    except Exception as e:
        conn.rollback()
        logging.error(f"Errore in add_diet_plan_with_recipes: {e}")
        raise
    finally:
        disconnect(conn)


def add_diet_plan(conf, diet_data: dict, items_data: list) -> str:
    """Compatibilita: inserisce un piano senza ricette."""
    return add_diet_plan_with_recipes(conf, diet_data, items_data, [])


def update_diet_plan_with_recipes(
    conf,
    diet_id,
    diet_data: dict,
    items_data: list,
    recipes_data: list,
) -> str:
    """Aggiorna testata, ricette e distribuzione come unico replace transazionale."""
    conn = connect(conf)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                UPDATE diet_plans
                SET diet_name = %(diet_name)s,
                    descrizione = %(descrizione)s,
                    warnings = %(warnings)s
                WHERE id = %(diet_id)s
                  AND patient_id = %(patient_id)s;
                """,
                {**diet_data, "diet_id": diet_id},
            )
            if cur.rowcount != 1:
                raise ValueError("Piano alimentare non trovato per l'assistito selezionato.")

            # Elimina prima le allocazioni: recipe_id usa ON DELETE RESTRICT.
            cur.execute("DELETE FROM diet_meal_items WHERE diet_plan_id = %s;", (diet_id,))

            recipe_ref_map, keep_recipe_ids, removed_recipe_ids = _sync_recipes_for_existing_plan(
                cur, diet_id, recipes_data
            )

            _insert_diet_items(
                cur,
                diet_id,
                items_data,
                recipe_ref_map=recipe_ref_map,
                allowed_recipe_ids=keep_recipe_ids,
            )

            # Le ricette rimosse dall'editor vengono eliminate solo dopo il replace
            # dei meal item, cosi nessun FK recipe_id puo ancora referenziarle.
            if removed_recipe_ids:
                cur.execute(
                    "DELETE FROM recipes WHERE diet_plan_id = %s AND id = ANY(%s::uuid[]);",
                    (diet_id, list(removed_recipe_ids)),
                )

        conn.commit()
        logging.info(f"Piano alimentare {diet_id} con ricette aggiornato con successo.")
        return str(diet_id)
    except Exception as e:
        conn.rollback()
        logging.error(f"Errore in update_diet_plan_with_recipes: {e}")
        raise
    finally:
        disconnect(conn)


def update_diet_plan(conf, diet_id, diet_data: dict, items_data: list) -> str:
    """Compatibilita: full replace di un piano senza ricette."""
    return update_diet_plan_with_recipes(conf, diet_id, diet_data, items_data, [])


def delete_diet_plan(conf, patient_id: str, diet_id) -> None:
    """Elimina piano, distribuzione e ricette in modo atomico."""
    conn = connect(conf)
    try:
        with conn.cursor() as cur:
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

            # Ordine necessario con diet_meal_items.recipe_id ON DELETE RESTRICT.
            cur.execute("DELETE FROM diet_meal_items WHERE diet_plan_id = %s;", (diet_id,))
            # recipe_ingredients viene eliminata da ON DELETE CASCADE su recipe_id.
            cur.execute("DELETE FROM recipes WHERE diet_plan_id = %s;", (diet_id,))
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


# Colonne micronutrienti presenti in foods. I valori sono riferiti a 100 g.
# Il collegamento con micronutrients_quantities e esclusivamente logico/applicativo:
# micronutrients_name identifica la riga tipologica, senza FK o relazioni DB.
#
# supported_reference_bases rende esplicito quando il dato presente in foods e
# semanticamente confrontabile con la base scientifica del riferimento. Quando
# la base non e verificabile (es. vitamina A generica vs µg RE/RAE), l'overview
# mostra il valore ma NON genera falsi alert su min/max.
MICRONUTRIENT_DEFINITIONS = {
    "vitamina_a_mcg": {
        "name": "Vitamina A", "food_unit": "µg", "supported_reference_bases": (),
    },
    "vitamina_d_mcg": {
        "name": "Vitamina D", "food_unit": "µg", "supported_reference_bases": ("µg", "µg VDE"),
    },
    "vitamina_e_mg": {
        "name": "Vitamina E", "food_unit": "mg", "supported_reference_bases": (),
    },
    "vitamina_k_mcg": {
        "name": "Vitamina K", "food_unit": "µg", "supported_reference_bases": (),
    },
    "vitamina_c_mg": {
        "name": "Vitamina C", "food_unit": "mg", "supported_reference_bases": ("mg",),
    },
    "tiamina_b1_mg": {
        "name": "Tiamina B1", "food_unit": "mg", "supported_reference_bases": ("mg/MJ",),
    },
    "riboflavina_b2_mg": {
        "name": "Riboflavina B2", "food_unit": "mg", "supported_reference_bases": ("mg",),
    },
    "niacina_b3_mg": {
        "name": "Niacina B3", "food_unit": "mg", "supported_reference_bases": (),
    },
    "vitamina_b6_mg": {
        "name": "Vitamina B6", "food_unit": "mg", "supported_reference_bases": ("mg",),
    },
    "folato_b9_mcg": {
        "name": "Folato B9", "food_unit": "µg", "supported_reference_bases": (),
    },
    "vitamina_b12_mcg": {
        "name": "Vitamina B12", "food_unit": "µg", "supported_reference_bases": ("µg",),
    },
    "biotina_b7_mcg": {
        "name": "Biotina B7", "food_unit": "µg", "supported_reference_bases": ("µg",),
    },
    "acido_pantotenico_b5_mg": {
        "name": "Acido pantotenico B5", "food_unit": "mg", "supported_reference_bases": ("mg",),
    },
    "calcio_mg": {
        "name": "Calcio", "food_unit": "mg", "supported_reference_bases": ("mg",),
    },
    "ferro_mg": {
        "name": "Ferro", "food_unit": "mg", "supported_reference_bases": ("mg",),
    },
    "magnesio_mg": {
        "name": "Magnesio", "food_unit": "mg", "supported_reference_bases": ("mg",),
    },
    "zinco_mg": {
        "name": "Zinco", "food_unit": "mg", "supported_reference_bases": ("mg",),
    },
    "rame_mg": {
        "name": "Rame", "food_unit": "mg", "supported_reference_bases": ("mg",),
    },
    "manganese_mg": {
        "name": "Manganese", "food_unit": "mg", "supported_reference_bases": ("mg",),
    },
    "selenio_mcg": {
        "name": "Selenio", "food_unit": "µg", "supported_reference_bases": ("µg",),
    },
    "iodio_mcg": {
        "name": "Iodio", "food_unit": "µg", "supported_reference_bases": ("µg",),
    },
    "potassio_mg": {
        "name": "Potassio", "food_unit": "mg", "supported_reference_bases": ("mg",),
    },
    "sodio_mg": {
        "name": "Sodio", "food_unit": "mg", "supported_reference_bases": ("mg",),
    },
    "omega3_mg": {
        "name": "Omega 3", "food_unit": "mg", "supported_reference_bases": (),
    },
    "omega6_mg": {
        "name": "Omega 6", "food_unit": "mg", "supported_reference_bases": (),
    },
}


def _safe_decimal(value) -> Decimal:
    if value is None or value == "":
        return Decimal("0")
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


def _normalize_micronutrient_name(value) -> str:
    """Normalizza il nome della riga tipologica per il lookup applicativo."""
    import re

    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().casefold())


def _normalize_reference_basis(value) -> str:
    """Normalizza solo la forma testuale dell'unita/base, non effettua conversioni."""
    import re

    normalized = str(value or "").strip().casefold()
    normalized = normalized.replace("μ", "u").replace("µ", "u")
    normalized = normalized.replace("α", "alpha")
    return re.sub(r"[^a-z0-9]+", "", normalized)


def _reference_basis_is_supported(definition: dict, reference_basis: str) -> bool:
    supported = definition.get("supported_reference_bases") or ()
    normalized_reference = _normalize_reference_basis(reference_basis)
    return any(
        normalized_reference == _normalize_reference_basis(candidate)
        for candidate in supported
    )


def _effective_minimum(configured_minimum, reference_type: str, reference_basis: str, daily_energy_mj: Decimal):
    if configured_minimum is None:
        return None

    value = _safe_decimal(configured_minimum)
    ref_type = str(reference_type or "").upper()
    basis = _normalize_reference_basis(reference_basis)

    # EFSA esprime Tiamina e Niacina per MJ di energia introdotta.
    # Il coefficiente configurato viene quindi trasformato nel target effettivo
    # per la dieta corrente usando l'energia media giornaliera calcolata dai foods.
    if ref_type.endswith("_PER_MJ") or basis.endswith("mj"):
        return value * daily_energy_mj

    return value


def _status_for_reference(current: Decimal, minimum, maximum, reference_type: str, maximum_type: str) -> str:
    """Restituisce un codice di stato usato dalla UI per colorare l'intera riga."""
    ref_type = str(reference_type or "").upper()
    max_type = str(maximum_type or "").upper()

    if maximum is not None and current > maximum:
        if max_type == "UL_TOTAL":
            return "HIGH_UL"
        if max_type in {"SAFE_LEVEL", "SAFE_ADEQUATE"}:
            return "HIGH_WARNING"

    if minimum is not None and current < minimum:
        if ref_type in {"PRI", "PRI_PER_MJ"}:
            return "LOW_PRI"
        if ref_type in {"AI", "PRI_ASSUMED"}:
            return "LOW_AI"

    return "OK"


def calculate_diet_micronutrients_overview(conf, items_data: list, days_in_plan: int = 7) -> dict:
    """
    Calcola on-demand l'apporto medio giornaliero dei micronutrienti.

    Regole principali:
    - foods contiene i valori per 100 g;
    - micronutrients_quantities resta una tipologica senza FK verso foods;
    - il confronto viene effettuato solo quando la base del dato foods e
      semanticamente compatibile con reference_basis;
    - i limiti UL riferiti solo a integratori/forme specifiche non vengono
      applicati all'overview di una dieta composta da alimenti;
    - i riferimenti espressi per MJ vengono trasformati usando l'energia media
      giornaliera effettiva della dieta.
    """
    if days_in_plan <= 0:
        raise ValueError("days_in_plan deve essere maggiore di zero.")

    normalized_items = []
    food_ids = set()
    for item in items_data or []:
        food_id = item.get("food_id")
        grams = _safe_decimal(item.get("grams"))
        if not food_id or grams <= 0:
            continue
        food_id_str = str(food_id)
        food_ids.add(food_id_str)
        normalized_items.append((food_id_str, grams))

    if not normalized_items:
        return {
            "days_in_plan": days_in_plan,
            "daily_energy_mj": 0.0,
            "rows": [],
            "missing_rda_names": [definition["name"] for definition in MICRONUTRIENT_DEFINITIONS.values()],
            "missing_food_ids": [],
        }

    conn = connect(conf)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            nutrient_columns = ", ".join(MICRONUTRIENT_DEFINITIONS.keys())
            cur.execute(
                f"""
                SELECT id, kcal, {nutrient_columns}
                FROM foods
                WHERE id = ANY(%s::uuid[]);
                """,
                (list(food_ids),),
            )
            food_rows = cur.fetchall()

            cur.execute(
                """
                SELECT
                    micronutrients_name,
                    minimum_rda_suggested_daily_amount,
                    maximum_rda_suggested_daily_amount,
                    "unità_misura",
                    reference_type,
                    maximum_type,
                    reference_basis,
                    reference_source
                FROM micronutrients_quantities;
                """
            )
            rda_rows = cur.fetchall()
    finally:
        disconnect(conn)

    foods_by_id = {str(row["id"]): row for row in food_rows}
    rda_by_name = {
        _normalize_micronutrient_name(row.get("micronutrients_name")): row
        for row in rda_rows
        if _normalize_micronutrient_name(row.get("micronutrients_name"))
    }

    weekly_totals = {column_name: Decimal("0") for column_name in MICRONUTRIENT_DEFINITIONS}
    weekly_kcal = Decimal("0")
    missing_food_ids = set()

    for food_id, grams in normalized_items:
        food = foods_by_id.get(food_id)
        if food is None:
            missing_food_ids.add(food_id)
            continue

        ratio = grams / Decimal("100")
        weekly_kcal += _safe_decimal(food.get("kcal")) * ratio
        for column_name in MICRONUTRIENT_DEFINITIONS:
            weekly_totals[column_name] += _safe_decimal(food.get(column_name)) * ratio

    divisor = Decimal(str(days_in_plan))
    daily_energy_kcal = weekly_kcal / divisor
    daily_energy_mj = daily_energy_kcal / Decimal("238.83")

    rows = []
    missing_rda_names = []

    for column_name, definition in MICRONUTRIENT_DEFINITIONS.items():
        micronutrient_name = definition["name"]
        food_unit = definition["food_unit"]
        current = weekly_totals[column_name] / divisor
        rda = rda_by_name.get(_normalize_micronutrient_name(micronutrient_name))

        if rda is None:
            missing_rda_names.append(micronutrient_name)
            rows.append({
                "micronutrient": micronutrient_name,
                "unit": food_unit,
                "current_daily_value": float(current),
                "minimum_rda": None,
                "maximum_rda": None,
                "reference_type": None,
                "maximum_type": None,
                "reference_basis": None,
                "reference_source": None,
                "comparison_status": "NO_REFERENCE",
                "comparison_note": "Riferimento non configurato nella tipologica.",
            })
            continue

        reference_type = str(rda.get("reference_type") or "").upper()
        maximum_type = str(rda.get("maximum_type") or "NONE").upper()
        reference_basis = str(rda.get("reference_basis") or rda.get("unità_misura") or "")
        basis_supported = _reference_basis_is_supported(definition, reference_basis)

        configured_minimum = rda.get("minimum_rda_suggested_daily_amount")
        configured_maximum = rda.get("maximum_rda_suggested_daily_amount")

        if basis_supported:
            minimum = _effective_minimum(
                configured_minimum,
                reference_type,
                reference_basis,
                daily_energy_mj,
            )

            # Un limite superiore viene applicato al cibo solo quando riguarda
            # l'assunzione totale oppure e un safe level / safe adequate target.
            if maximum_type in {"UL_TOTAL", "SAFE_LEVEL", "SAFE_ADEQUATE"}:
                maximum = (
                    _safe_decimal(configured_maximum)
                    if configured_maximum is not None
                    else None
                )
            else:
                maximum = None

            status = _status_for_reference(
                current,
                minimum,
                maximum,
                reference_type,
                maximum_type,
            )
            note = None
            if configured_maximum is not None and maximum is None:
                if maximum_type == "UL_SUPPLEMENT":
                    note = "Limite superiore riferito a integratori/forme aggiunte: non applicato agli alimenti."
                elif maximum_type in {"UL_RESTRICTED", "FORM_DEPENDENT"}:
                    note = "Limite superiore valido solo per forme specifiche: non applicato al dato foods generico."
        else:
            minimum = None
            maximum = None
            status = "NOT_COMPARABLE"
            note = (
                f"Dato foods in {food_unit}; riferimento configurato in {reference_basis}. "
                "La base nutrizionale non e verificata, quindi min/max non vengono confrontati."
            )

        rows.append({
            "micronutrient": micronutrient_name,
            "unit": food_unit,
            "current_daily_value": float(current),
            "minimum_rda": float(minimum) if minimum is not None else None,
            "maximum_rda": float(maximum) if maximum is not None else None,
            "configured_minimum_rda": (
                float(configured_minimum) if configured_minimum is not None else None
            ),
            "configured_maximum_rda": (
                float(configured_maximum) if configured_maximum is not None else None
            ),
            "reference_type": reference_type,
            "maximum_type": maximum_type,
            "reference_basis": reference_basis,
            "reference_source": rda.get("reference_source"),
            "comparison_status": status,
            "comparison_note": note,
        })

    return {
        "days_in_plan": days_in_plan,
        "daily_energy_mj": float(daily_energy_mj),
        "rows": rows,
        "missing_rda_names": missing_rda_names,
        "missing_food_ids": sorted(missing_food_ids),
    }

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
