import os
from dotenv import load_dotenv
import psycopg2
import logging
# import sys
from pathlib import Path

# # Aggiunge la root del progetto al path se lanciato da sotto cartelle
# sys.path.append(str(Path(__file__).resolve().parent.parent))

# from Common.configuration import Configuration as conf
# from Common.utils import *
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
current_dir = Path(__file__).resolve().parent
env_path = current_dir.parent.parent.parent / '.env'

# Carica esplicitamente il file .env dal percorso calcolato
load_dotenv(dotenv_path=env_path)
DATABASE_URL = os.getenv("DATABASE_URL")


# Lista degli script SQL da eseguire nell'ordine stabilito
SCRIPTS_TO_EXECUTE = [
    "01_ddl_schema.sql",
]


def execute_sql_file(cursor, file_path):
    """Legge ed esegue un file SQL."""
    logging.info(f"--> Esecuzione script: {os.path.basename(file_path)}...")
    with open(file_path, "r", encoding="utf-8") as file:
        sql_content = file.read()
        cursor.execute(sql_content)
    logging.info(f"✓ Completato: {os.path.basename(file_path)}")

def main():
    if not DATABASE_URL:
            raise ValueError("DATABASE_URL non trovata nelle variabili d'ambiente.")

    try:
        connection = psycopg2.connect(DATABASE_URL)
        with connection.cursor() as cursor:
            for script_name in SCRIPTS_TO_EXECUTE:
                script_path =   current_dir / script_name
                
                if os.path.exists(script_path):
                    execute_sql_file(cursor, script_path)
                else:
                    logging.warning(f"⚠️ Script '{script_name}' non trovato nella cartella. Salto il file.")
                    
    finally:
        if connection:
            connection.close()

if __name__ == "__main__":
    main()