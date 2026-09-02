import streamlit as st

from Backend.services.food_service import (
    bulk_import_foods,
    create_food,
    delete_food,
    get_all_food_categories,
    get_all_foods,
    parse_food_import,
    update_food,
)


tec_conf = st.session_state.get("tec_conf", {})
st.title("Catalogo alimenti 🍎")
st.caption(
    "Catalogo condiviso dell'MVP: in questa versione i cibi non sono separati per nutrizionista."
)


MACRO_FIELDS = [
    ("kcal", "Kcal / 100 g", 1.0),
    ("carbs_g", "Carboidrati (g) / 100 g", 0.1),
    ("fats_g", "Grassi (g) / 100 g", 0.1),
    ("prots_g", "Proteine (g) / 100 g", 0.1),
]

MICRO_FIELDS = [
    ("vitamina_a_mcg", "Vitamina A (µg)", 0.1),
    ("vitamina_d_mcg", "Vitamina D (µg)", 0.1),
    ("vitamina_e_mg", "Vitamina E (mg)", 0.1),
    ("vitamina_k_mcg", "Vitamina K (µg)", 0.1),
    ("vitamina_c_mg", "Vitamina C (mg)", 0.1),
    ("tiamina_b1_mg", "Tiamina B1 (mg)", 0.01),
    ("riboflavina_b2_mg", "Riboflavina B2 (mg)", 0.01),
    ("niacina_b3_mg", "Niacina B3 (mg)", 0.1),
    ("vitamina_b6_mg", "Vitamina B6 (mg)", 0.01),
    ("folato_b9_mcg", "Folato B9 (µg)", 0.1),
    ("vitamina_b12_mcg", "Vitamina B12 (µg)", 0.01),
    ("biotina_b7_mcg", "Biotina B7 (µg)", 0.1),
    ("acido_pantotenico_b5_mg", "Acido pantotenico B5 (mg)", 0.1),
    ("calcio_mg", "Calcio (mg)", 0.1),
    ("ferro_mg", "Ferro (mg)", 0.1),
    ("magnesio_mg", "Magnesio (mg)", 0.1),
    ("zinco_mg", "Zinco (mg)", 0.1),
    ("rame_mg", "Rame (mg)", 0.01),
    ("manganese_mg", "Manganese (mg)", 0.01),
    ("selenio_mcg", "Selenio (µg)", 0.1),
    ("iodio_mcg", "Iodio (µg)", 0.1),
    ("potassio_mg", "Potassio (mg)", 0.1),
    ("sodio_mg", "Sodio (mg)", 0.1),
    ("omega3_mg", "Omega-3 (mg)", 0.1),
    ("omega6_mg", "Omega-6 (mg)", 0.1),
]


def _to_float_or_none(value):
    return None if value is None else float(value)


def _load_categories():
    try:
        categories = get_all_food_categories(tec_conf)
    except Exception:
        categories = []
    category_by_id = {str(c["id"]): c["name"] for c in categories}
    return categories, category_by_id


def _food_form(prefix: str, current=None):
    current = current or {}
    categories, category_by_id = _load_categories()
    category_ids = [None] + [str(c["id"]) for c in categories]
    current_category = (
        str(current.get("category_id")) if current.get("category_id") is not None else None
    )
    category_index = (
        category_ids.index(current_category) if current_category in category_ids else 0
    )

    item_name = st.text_input(
        "Nome alimento *",
        value=str(current.get("item_name") or ""),
        key=f"{prefix}_item_name",
    )

    category_id = st.selectbox(
        "Categoria",
        options=category_ids,
        index=category_index,
        format_func=lambda value: "Nessuna categoria"
        if value is None
        else category_by_id.get(value, value),
        key=f"{prefix}_category_id",
    )

    st.markdown("#### Macronutrienti")
    macro_values = {}
    macro_cols = st.columns(4)
    for col, (field, label, step) in zip(macro_cols, MACRO_FIELDS):
        with col:
            macro_values[field] = st.number_input(
                label,
                min_value=0.0,
                value=_to_float_or_none(current.get(field)),
                step=step,
                key=f"{prefix}_{field}",
            )

    indice_glicemico = st.number_input(
        "Indice glicemico",
        min_value=0,
        value=int(current["indice_glicemico"])
        if current.get("indice_glicemico") is not None
        else None,
        step=1,
        key=f"{prefix}_indice_glicemico",
    )

    micro_values = {}
    with st.expander("Vitamine, minerali e acidi grassi", expanded=False):
        cols = st.columns(3)
        for index, (field, label, step) in enumerate(MICRO_FIELDS):
            with cols[index % 3]:
                micro_values[field] = st.number_input(
                    label,
                    min_value=0.0,
                    value=_to_float_or_none(current.get(field)),
                    step=step,
                    key=f"{prefix}_{field}",
                )

    peculiarita = st.text_area(
        "Peculiarità nutrizionale / note",
        value=str(current.get("peculiarita_nutrizionale") or ""),
        key=f"{prefix}_peculiarita",
    )

    payload = {
        "item_name": item_name,
        "category_id": category_id,
        "indice_glicemico": indice_glicemico,
        "peculiarita_nutrizionale": peculiarita or None,
        **macro_values,
        **micro_values,
    }
    return payload


def _clear_food_cache():
    # L'editor dei piani alimentari mantiene il catalogo in st.cache_data.
    # Dopo una modifica invalidiamo la cache per rendere subito visibili i cambiamenti.
    st.cache_data.clear()


def _preview_rows(rows):
    preview_columns = [
        "item_name",
        "kcal",
        "carbs_g",
        "fats_g",
        "prots_g",
        "indice_glicemico",
        "peculiarita_nutrizionale",
    ]
    return [
        {column: row.get(column) for column in preview_columns if column in row}
        for row in rows
    ]


tab_catalogo, tab_nuovo, tab_import = st.tabs(
    ["Catalogo", "Nuovo alimento", "Import massivo"]
)


with tab_catalogo:
    try:
        foods = get_all_foods(tec_conf)
    except Exception as exc:
        st.error(f"Impossibile caricare il catalogo alimenti: {exc}")
        foods = []

    st.metric("Alimenti nel catalogo", len(foods))

    search = st.text_input(
        "🔍 Cerca alimento",
        placeholder="Nome o peculiarità nutrizionale...",
        key="food_catalog_search",
    ).strip().lower()

    filtered_foods = [
        food
        for food in foods
        if not search
        or search in str(food.get("item_name") or "").lower()
        or search in str(food.get("peculiarita_nutrizionale") or "").lower()
    ]

    if filtered_foods:
        st.dataframe(
            [
                {
                    "Alimento": food.get("item_name"),
                    "Kcal": food.get("kcal"),
                    "Carboidrati (g)": food.get("carbs_g"),
                    "Grassi (g)": food.get("fats_g"),
                    "Proteine (g)": food.get("prots_g"),
                    "IG": food.get("indice_glicemico"),
                }
                for food in filtered_foods
            ],
            use_container_width=True,
            hide_index=True,
        )
        st.divider()

        food_by_id = {str(food["id"]): food for food in filtered_foods}
        selected_id = st.selectbox(
            "Seleziona un alimento da modificare",
            options=list(food_by_id.keys()),
            format_func=lambda food_id: food_by_id[food_id]["item_name"],
            key="selected_food_id",
        )
        selected_food = food_by_id[selected_id]

        
        st.subheader(f"Modifica · {selected_food['item_name']}")
        with st.form(f"edit_food_form_{selected_id}"):
            edit_payload = _food_form(f"edit_{selected_id}", selected_food)
            confirm_delete = st.checkbox(
                "Confermo di voler eliminare questo alimento",
                key=f"confirm_delete_{selected_id}",
            )
            col_save, col_delete = st.columns(2)
            with col_save:
                save_clicked = st.form_submit_button(
                    "Salva modifiche", use_container_width=True
                )
            with col_delete:
                delete_clicked = st.form_submit_button(
                    "Elimina alimento", use_container_width=True
                )

            if save_clicked:
                if not edit_payload["item_name"].strip():
                    st.error("Il nome dell'alimento è obbligatorio.")
                else:
                    try:
                        update_food(tec_conf, selected_id, edit_payload)
                        _clear_food_cache()
                        st.success("Alimento aggiornato con successo.")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Errore durante l'aggiornamento: {exc}")

            if delete_clicked:
                if not confirm_delete:
                    st.warning("Conferma l'eliminazione prima di procedere.")
                else:
                    try:
                        deleted = delete_food(tec_conf, selected_id)
                        if deleted:
                            _clear_food_cache()
                            st.success("Alimento eliminato dal catalogo.")
                            st.rerun()
                        else:
                            st.warning("Alimento non trovato.")
                    except Exception as exc:
                        st.error(f"Errore durante l'eliminazione: {exc}")
    else:
        st.info("Nessun alimento trovato con i filtri correnti.")


with tab_nuovo:
    st.subheader("Aggiungi alimento")
    with st.form("new_food_form", clear_on_submit=False):
        new_payload = _food_form("new_food")
        create_clicked = st.form_submit_button("Aggiungi al catalogo")

        if create_clicked:
            if not new_payload["item_name"].strip():
                st.error("Il nome dell'alimento è obbligatorio.")
            else:
                try:
                    created = create_food(tec_conf, new_payload)
                    _clear_food_cache()
                    st.success(f"Alimento '{created['item_name']}' aggiunto con successo.")
                except Exception as exc:
                    st.error(f"Errore durante la creazione: {exc}")


with tab_import:
    st.subheader("Import massivo CSV / JSON")
    st.markdown(
        "Puoi usare direttamente i nomi campo del database (`item_name`, `kcal`, `carbs_g`, ...) "
        "oppure le intestazioni del file alimenti legacy già presente nel progetto."
    )

    uploaded_file = st.file_uploader(
        "Carica un file",
        type=["csv", "json"],
        key="foods_bulk_upload",
    )

    with st.expander("📄 Esempio e formato CSV", expanded=False):
        st.markdown(
            """
            **Indicazioni di formato**

            - Il file deve essere codificato in **UTF-8**.
            - La prima riga deve contenere le **intestazioni delle colonne**.
            - `item_name` è **obbligatorio**.
            - Tutti i valori nutrizionali sono riferiti a **100 g di alimento**.
            - I campi numerici possono essere lasciati vuoti se il dato non è disponibile.
            - Sono accettati sia il **punto** sia la **virgola** come separatore decimale.
            - Se si utilizza la virgola come separatore decimale, è consigliato usare `;` come separatore CSV.
            - `indice_glicemico` deve essere un **numero intero**.
            - `category_id` è opzionale e, se valorizzato, deve contenere l'**UUID di una categoria già esistente**. In caso contrario può essere lasciato vuoto.
            - `peculiarita_nutrizionale` è un campo testuale libero.
            - Non devono essere specificati `id`, `user_id`, `created_at` o `updated_at`.
            """
        )

        st.code(
            """category_id;item_name;kcal;carbs_g;fats_g;prots_g;indice_glicemico;vitamina_a_mcg;vitamina_d_mcg;vitamina_e_mg;vitamina_k_mcg;vitamina_c_mg;tiamina_b1_mg;riboflavina_b2_mg;niacina_b3_mg;vitamina_b6_mg;folato_b9_mcg;vitamina_b12_mcg;biotina_b7_mcg;acido_pantotenico_b5_mg;calcio_mg;ferro_mg;magnesio_mg;zinco_mg;rame_mg;manganese_mg;selenio_mcg;iodio_mcg;potassio_mg;sodio_mg;omega3_mg;omega6_mg;peculiarita_nutrizionale
    ;Mandorle;578;19,7;50,6;21,2;15;1;0;25;0;0;0,2;1,1;3,4;0,1;50;0;17;0,5;260;3,7;270;3,1;1;2,3;4;0;733;1;10;12200;Ricche di grassi monoinsaturi e vitamina E
    ;Petto di pollo;165;0;3,6;31;0;13;0,1;0,3;0;0;0,07;0,1;13,7;0,6;4;0,3;0,2;1;15;1;29;1;0,04;0,02;27;5;256;74;70;170;Fonte proteica magra ad alto valore biologico""",
            language="text",
        )

        st.caption(
            "Esempio con separatore ';' e virgola come separatore decimale. "
            "È possibile utilizzare anche le intestazioni del CSV legacy già supportate dall'applicazione."
        )


    with st.expander("🧩 Esempio e formato JSON", expanded=False):
        st.markdown(
            """
            **Indicazioni di formato**

            - Il file deve contenere una **lista JSON di alimenti**.
            - Ogni alimento deve essere rappresentato da un **oggetto JSON**.
            - `item_name` è **obbligatorio**.
            - Tutti i valori nutrizionali sono riferiti a **100 g di alimento**.
            - I valori numerici devono essere espressi preferibilmente come **numeri JSON**, utilizzando il punto come separatore decimale.
            - `indice_glicemico` deve essere un **numero intero**.
            - I dati non disponibili possono essere impostati a `null`.
            - `category_id` è opzionale e, se valorizzato, deve contenere l'**UUID di una categoria già esistente**.
            - `peculiarita_nutrizionale` è una stringa testuale libera.
            - Non devono essere specificati `id`, `user_id`, `created_at` o `updated_at`.

            È accettato anche un oggetto contenitore con una delle chiavi `foods`, `items` o `data`, purché contenga una lista di alimenti.
            """
        )

        st.code(
            """[
    {
        "category_id": null,
        "item_name": "Mandorle",
        "kcal": 578,
        "carbs_g": 19.7,
        "fats_g": 50.6,
        "prots_g": 21.2,
        "indice_glicemico": 15,
        "vitamina_a_mcg": 1,
        "vitamina_d_mcg": 0,
        "vitamina_e_mg": 25,
        "vitamina_k_mcg": 0,
        "vitamina_c_mg": 0,
        "tiamina_b1_mg": 0.2,
        "riboflavina_b2_mg": 1.1,
        "niacina_b3_mg": 3.4,
        "vitamina_b6_mg": 0.1,
        "folato_b9_mcg": 50,
        "vitamina_b12_mcg": 0,
        "biotina_b7_mcg": 17,
        "acido_pantotenico_b5_mg": 0.5,
        "calcio_mg": 260,
        "ferro_mg": 3.7,
        "magnesio_mg": 270,
        "zinco_mg": 3.1,
        "rame_mg": 1,
        "manganese_mg": 2.3,
        "selenio_mcg": 4,
        "iodio_mcg": 0,
        "potassio_mg": 733,
        "sodio_mg": 1,
        "omega3_mg": 10,
        "omega6_mg": 12200,
        "peculiarita_nutrizionale": "Ricche di grassi monoinsaturi e vitamina E"
    },
    {
        "category_id": null,
        "item_name": "Petto di pollo",
        "kcal": 165,
        "carbs_g": 0,
        "fats_g": 3.6,
        "prots_g": 31,
        "indice_glicemico": 0,
        "vitamina_a_mcg": 13,
        "vitamina_d_mcg": 0.1,
        "vitamina_e_mg": 0.3,
        "vitamina_k_mcg": 0,
        "vitamina_c_mg": 0,
        "tiamina_b1_mg": 0.07,
        "riboflavina_b2_mg": 0.1,
        "niacina_b3_mg": 13.7,
        "vitamina_b6_mg": 0.6,
        "folato_b9_mcg": 4,
        "vitamina_b12_mcg": 0.3,
        "biotina_b7_mcg": 0.2,
        "acido_pantotenico_b5_mg": 1,
        "calcio_mg": 15,
        "ferro_mg": 1,
        "magnesio_mg": 29,
        "zinco_mg": 1,
        "rame_mg": 0.04,
        "manganese_mg": 0.02,
        "selenio_mcg": 27,
        "iodio_mcg": 5,
        "potassio_mg": 256,
        "sodio_mg": 74,
        "omega3_mg": 70,
        "omega6_mg": 170,
        "peculiarita_nutrizionale": "Fonte proteica magra ad alto valore biologico"
    }
    ]""",
            language="json",
        )

    if uploaded_file is not None:
        rows, ignored_columns, errors = parse_food_import(
            uploaded_file.name, uploaded_file.getvalue()
        )

        if ignored_columns:
            st.warning(
                "Colonne non riconosciute e ignorate: " + ", ".join(ignored_columns)
            )

        if errors:
            st.error(
                "Il file contiene errori. Correggili prima dell'import:"
            )
            for error in errors[:20]:
                st.write(f"- {error}")
            if len(errors) > 20:
                st.caption(f"... e altri {len(errors) - 20} errori.")

        if rows:
            st.success(f"{len(rows)} righe valide rilevate.")
            st.dataframe(
                _preview_rows(rows[:100]),
                use_container_width=True,
                hide_index=True,
            )
            if len(rows) > 100:
                st.caption("Anteprima limitata alle prime 100 righe.")

            duplicate_choice = st.radio(
                "Se un alimento con lo stesso nome esiste già",
                options=["Salta il duplicato", "Aggiorna il record esistente"],
                horizontal=True,
            )
            duplicate_policy = (
                "skip" if duplicate_choice == "Salta il duplicato" else "update"
            )

            import_clicked = st.button(
                "Importa alimenti",
                type="primary",
                disabled=bool(errors),
                use_container_width=True,
            )

            if import_clicked:
                try:
                    result = bulk_import_foods(
                        tec_conf,
                        rows,
                        duplicate_policy=duplicate_policy,
                    )
                    _clear_food_cache()
                    st.success(
                        "Import completato: "
                        f"{result['inserted']} inseriti, "
                        f"{result['updated']} aggiornati, "
                        f"{result['skipped']} duplicati saltati."
                    )
                except Exception as exc:
                    st.error(f"Import annullato: {exc}")
        elif not errors:
            st.info("Il file non contiene righe importabili.")
