import psycopg2
import logging

# Configurazione base: basta chiamarla una volta all'inizio dello script
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)


def connect(conf):
    if not conf.DATABASE_URL:
        raise ValueError("DATABASE_URL non trovata nelle variabili d'ambiente.")
    try:
        connection = psycopg2.connect(conf.DATABASE_URL)
        logging.info("✅ Connected to DB")
        return connection
    except Exception as error:
        logging.error("❌ Errore durante lo stabilimento della connessione")
        logging.error(error)
        raise error

def disconnect(connection):
    if connection:
        connection.close()
        logging.info("Connessione al database chiusa.")

