import os
import pandas as pd
import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import execute_values
import numpy as np

# Carica le variabili d'ambiente dal file .env
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
CSV_FILE = "ppj/Nutriflow/DB/scripts/lista_alimenti.csv"
NOME_TABELLA = "foods"

# Dizionario di Mapping
MAPPING_COLONNE = {
    "Item (100 gr)": "item_name",
    "Kcal": "kcal",
    "Carbs": "carbs_g",
    "Fats": "fats_g",
    "Prots": "prots_g",
    "Indice Glicemico (IG)": "indice_glicemico",
    "Vitamina A (µg)": "vitamina_a_mcg",
    "Vitamina D (µg)": "vitamina_d_mcg",
    "Vitamina E (mg)": "vitamina_e_mg",
    "Vitamina K (µg)": "vitamina_k_mcg",
    "Vitamina C (mg)": "vitamina_c_mg",
    "Tiamina (B1) (mg)": "tiamina_b1_mg",
    "Riboflavina (B2) (mg)": "riboflavina_b2_mg",
    "Niacina (B3) (mg)": "niacina_b3_mg",
    "Vitamina B6 (mg)": "vitamina_b6_mg",
    "Folato (B9) (µg)": "folato_b9_mcg",
    "Vitamina B12 (µg)": "vitamina_b12_mcg",
    "Biotina (B7) (µg)": "biotina_b7_mcg",
    "Acido pantotenico (B5) (mg)": "acido_pantotenico_b5_mg",
    "Calcio (mg)": "calcio_mg",
    "Ferro (mg)": "ferro_mg",
    "Magnesio (mg)": "magnesio_mg",
    "Zinco (mg)": "zinco_mg",
    "Rame (mg)": "rame_mg",
    "Manganese (mg)": "manganese_mg",
    "Selenio (µg)": "selenio_mcg",
    "Iodio (µg)": "iodio_mcg",
    "Potassio (mg)": "potassio_mg",
    "Sodio (mg)": "sodio_mg",
    "Omega3 (mg)": "omega3_mg",
    "Omega6 (mg)": "omega6_mg",
    "Peculiarità Nutrizionale": "peculiarita_nutrizionale"
}

def prepara_dati_csv():
    """Legge il CSV in modo robusto, applica il mapping e restituisce query e valori."""
    try:
        # Aggiungiamo engine='python' e on_bad_lines='skip' 
        # per evitare blocchi dovuti a formattazioni anomale nelle righe descrittive
        df = pd.read_csv(
            CSV_FILE, 
            engine='python', 
            on_bad_lines='skip',
            header=0,
            sep='\t',
            decimal=','
        )
    except FileNotFoundError:
        print(f"Errore: File {CSV_FILE} non trovato.")
        return None, None
    except Exception as e:
        print(f"Errore di lettura del CSV: {e}")
        return None, None
    
    # Rinomina le colonne in base al mapping
    df = df.rename(columns=MAPPING_COLONNE)
    colonne_db = list(MAPPING_COLONNE.values())
    

    
    # Filtra tenendo solo le colonne esistenti nella tabella
    colonne_presenti = [col for col in colonne_db if col in df.columns]
    df = df[colonne_presenti]

    # Sostituisce i NaN di Pandas con None per mapparli correttamente a NULL su Postgres
    df = df.replace({np.nan: None})

    # Prepara query e lista di tuple
    colonne_str = ",".join(colonne_presenti)
    query = f"INSERT INTO {NOME_TABELLA} ({colonne_str}) VALUES %s"
    
    valori_da_inserire = [tuple(x) for x in df.to_numpy()]
    
    return query, valori_da_inserire

def main():
    if not DATABASE_URL:
        raise ValueError("DATABASE_URL non trovata nelle variabili d'ambiente.")

    query, valori_da_inserire = prepara_dati_csv()
    if not valori_da_inserire:
        print("Nessun dato da inserire.")
        return

    connection = None
    try:
        connection = psycopg2.connect(DATABASE_URL)
        
        with connection:
            with connection.cursor() as cursor:
                print(f"Inizio inserimento di {len(valori_da_inserire)} righe...")
                execute_values(cursor, query, valori_da_inserire, page_size=1000)
                                
        print("\n🎉 Inserimento completato con successo!")

    except Exception as error:
        print("\n❌ Errore durante l'inserimento dei valori")
        print(error)

    finally:
        if connection:
            connection.close()
            print("Connessione al database chiusa.")

if __name__ == "__main__":
    main()